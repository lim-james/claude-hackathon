"""Undo steps: each must give back exactly the earlier document, and cost what the edit changed."""
import io
import tempfile
import unittest
from pathlib import Path
import fitz
from PIL import Image
from core import PDF


def noise_jpeg(size=(600, 800)):
    data = io.BytesIO()
    Image.effect_noise(size, 48).convert('RGB').save(data, 'JPEG', quality=80)
    return data.getvalue()


def state(doc):
    """Everything a user can see or export: outline, and per page text, marks, fields, rotation, pixels."""
    pages = []
    for page in doc:
        pages.append((
            page.rotation, page.get_text(),
            sorted((a.type[1], tuple(round(v, 2) for v in a.rect), a.info.get('content', '')) for a in page.annots()),
            sorted((w.field_name, w.field_value) for w in page.widgets()),
            sorted((link['kind'], link.get('page', -1)) for link in page.get_links()),
            page.get_pixmap(dpi=72).samples))
    return doc.get_toc(), pages


def saved_content(path):
    """Page text plus every decoded stream of a saved file; streams may hold text as hex."""
    with fitz.open(path) as doc:
        streams = b''.join(doc.xref_stream(x) or b'' for x in range(1, doc.xref_length()))
        return ''.join(page.get_text() for page in doc) + streams.decode('latin-1')


class Session:
    """The undo bookkeeping of App.change, App.undo and App.redo, without the window."""
    def __init__(self, pdf):
        self.pdf, self.history, self.redo_history = pdf, [], []
        # Before the fix the app stored PDF.bytes(); the comparison uses whichever exists.
        self.snapshot = getattr(pdf, 'snapshot', pdf.bytes)

    def change(self, operation):
        before = self.snapshot()
        try:
            operation()
        except Exception:
            self.pdf.restore(before)
            raise
        self.history.append(before)
        self.history = self.history[-8:]
        self.redo_history.clear()

    def undo(self):
        self.redo_history.append(self.snapshot())
        self.pdf.restore(self.history.pop())

    def redo(self):
        self.history.append(self.snapshot())
        self.pdf.restore(self.redo_history.pop())

    def held(self):
        parts = {}
        for step in self.history + self.redo_history:
            for part in step if isinstance(step, tuple) else (step,):
                parts[id(part)] = len(part)
        return sum(parts.values())


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.image = self.root / 'image.jpg'
        self.image.write_bytes(noise_jpeg((200, 200)))

    def tearDown(self):
        self.tmp.cleanup()

    def source(self, name='source.pdf', **save_options):
        """Six pages with a filled field, a note, a link and bookmarks, saved to a file."""
        doc = fitz.open()
        for number in range(6):
            doc.new_page().insert_text((72, 72), f'Page {number + 1} Secret 123456 and public text')
        widget = fitz.Widget()
        widget.field_name, widget.field_type = 'ssn', fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect, widget.field_value = fitz.Rect(72, 100, 200, 120), '1'
        doc[0].add_widget(widget)
        doc[0].add_text_annot((300, 300), 'first note')
        doc[1].insert_link({'kind': fitz.LINK_GOTO, 'from': fitz.Rect(10, 10, 50, 50), 'page': 0, 'to': fitz.Point(0, 0)})
        doc.set_toc([[1, 'First', 1], [1, 'Third', 3]])
        path = self.root / name
        doc.save(path, **save_options)
        doc.close()
        return path

    def edits(self, pdf):
        return [
            lambda: pdf.insert_text(0, (60, 200, 400, 245), 'x'),
            lambda: pdf.doc[1].add_text_annot((100, 100), 'second note (é)'),
            lambda: pdf.fill_field(0, 'ssn', '987'),
            lambda: pdf.doc[2].set_rotation(90),
            lambda: pdf.doc.delete_page(3),
            lambda: pdf.doc.new_page(pno=1),
            lambda: pdf.redact(0, pdf.doc[0].search_for('123456')[0]),
            lambda: pdf.add_image(2, fitz.Rect(50, 50, 150, 150), self.image),
        ]

    def check_chain(self, pdf):
        session = Session(pdf)
        states = [state(pdf.doc)]
        for edit in self.edits(pdf):
            session.change(edit)
            states.append(state(pdf.doc))
        self.assertTrue(all(a != b for a, b in zip(states, states[1:])), 'every edit must change the document')
        for expected in reversed(states[:-1]):
            session.undo()
            self.assertFalse(pdf.doc.is_repaired)
            self.assertEqual(state(pdf.doc), expected)
        for expected in states[1:]:
            session.redo()
            self.assertFalse(pdf.doc.is_repaired)
            self.assertEqual(state(pdf.doc), expected)
        return session

    def test_eight_edits_undo_and_redo_exactly(self):
        pdf = PDF(self.source())
        self.addCleanup(pdf.close)
        self.check_chain(pdf)

    def test_chain_on_object_stream_file(self):
        pdf = PDF(self.source(use_objstms=1, garbage=4, deflate=True))
        self.addCleanup(pdf.close)
        self.check_chain(pdf)

    def test_chain_on_password_file(self):
        pdf = PDF(self.source())
        locked = self.root / 'locked.pdf'
        pdf.save(locked, 'test-password')
        pdf.close()
        pdf = PDF(locked, 'test-password')
        self.addCleanup(pdf.close)
        self.check_chain(pdf)

    def test_chain_on_new_document(self):
        pdf = PDF()
        self.addCleanup(pdf.close)
        pdf.merge([self.source()])
        self.check_chain(pdf)

    def test_chain_after_save_renumbered_the_document(self):
        pdf = PDF(self.source())
        self.addCleanup(pdf.close)
        session = Session(pdf)
        session.change(lambda: pdf.doc.delete_page(4))
        session.change(lambda: pdf.doc[0].add_text_annot((20, 20), 'before save'))
        expected = state(pdf.doc)
        pdf.save(self.root / 'saved.pdf')
        pdf.current_size()
        self.assertEqual(state(pdf.doc), expected)
        self.check_chain(pdf)

    def test_edit_after_undo_continues_from_the_restored_document(self):
        pdf = PDF(self.source())
        self.addCleanup(pdf.close)
        session = Session(pdf)
        start = state(pdf.doc)
        session.change(lambda: pdf.insert_text(0, (60, 200, 400, 245), 'dropped'))
        session.undo()
        session.change(lambda: pdf.doc[1].add_text_annot((100, 100), 'kept'))
        self.assertEqual(session.redo_history, [])
        kept = state(pdf.doc)
        session.change(lambda: pdf.doc[2].set_rotation(270))
        session.undo()
        self.assertEqual(state(pdf.doc), kept)
        self.assertNotIn('dropped', pdf.doc[0].get_text())
        session.undo()
        self.assertEqual(state(pdf.doc), start)

    def test_failed_operation_leaves_document_as_it_was(self):
        pdf = PDF(self.source())
        self.addCleanup(pdf.close)
        session = Session(pdf)
        session.change(lambda: pdf.fill_field(0, 'ssn', '42'))
        expected = state(pdf.doc)

        def fails_midway():
            pdf.doc[0].add_text_annot((10, 10), 'half done')
            pdf.doc.delete_page(1)
            raise RuntimeError('forced')
        with self.assertRaises(RuntimeError):
            session.change(fails_midway)
        self.assertEqual(state(pdf.doc), expected)
        self.assertEqual(len(session.history), 1)

    def test_deleted_objects_and_metadata_come_back(self):
        pdf = PDF(self.source())
        self.addCleanup(pdf.close)
        session = Session(pdf)
        session.change(lambda: pdf.doc.set_metadata({'title': 'Title (é) 中'}))
        expected, metadata = state(pdf.doc), pdf.doc.metadata

        def remove():
            page = pdf.doc[0]
            page.delete_annot(next(page.annots()))
            page.delete_widget(next(page.widgets()))
            pdf.sanitize()
        session.change(remove)
        self.assertEqual(pdf.fields(), [])
        session.undo()
        self.assertEqual(state(pdf.doc), expected)
        self.assertEqual(pdf.doc.metadata, metadata)

    def test_bytes_still_restore(self):
        pdf = PDF(self.source())
        self.addCleanup(pdf.close)
        expected = state(pdf.doc)
        data = pdf.bytes()
        pdf.doc.delete_page(0)
        pdf.restore(data)
        self.assertEqual(state(pdf.doc), expected)
        self.check_chain(pdf)

    def test_redaction_stays_out_of_the_saved_file(self):
        pdf = PDF(self.source())
        self.addCleanup(pdf.close)
        session = Session(pdf)

        def redact_all():
            for index in range(len(pdf.doc)):
                pdf.redact(index, pdf.doc[index].search_for('123456')[0])
        session.change(redact_all)
        session.change(lambda: pdf.doc[1].add_text_annot((100, 100), 'after redaction'))
        output = self.root / 'redacted.pdf'
        pdf.save(output)
        secret = ('123456', '123456'.encode().hex())
        for form in secret:
            self.assertNotIn(form, saved_content(output))
        # Undo and redo reopen the document from the history; the export must still be clean.
        session.undo()
        session.undo()
        session.redo()
        pdf.save(output)
        for form in secret:
            self.assertNotIn(form, saved_content(output))
        session.undo()
        pdf.save(output)
        for form in secret:
            self.assertIn(form, saved_content(output))

    def test_history_costs_what_the_edits_changed(self):
        # Issue #44: every step used to hold a full copy, 8 copies of a scan after 8 edits.
        doc = fitz.open()
        for _ in range(4):
            page = doc.new_page()
            page.insert_image(page.rect, stream=noise_jpeg())
        path = self.root / 'scan.pdf'
        doc.save(path)
        doc.close()
        size = path.stat().st_size
        self.assertGreater(size, 500_000)
        pdf = PDF(path)
        self.addCleanup(pdf.close)
        session = Session(pdf)
        start = state(pdf.doc)
        for number in range(10):
            session.change(lambda: pdf.insert_text(0, (60, 20 + number * 20, 400, 45 + number * 20), 'x'))
        self.assertEqual(len(session.history), 8)
        self.assertLess(session.held(), size * 1.25,
                        f'8 undo steps hold {session.held()} bytes for a {size} byte document')
        for _ in range(8):
            session.undo()
        self.assertEqual(pdf.doc[0].get_text().split(), ['x', 'x'])
        self.assertEqual(state(pdf.doc)[1][1:], start[1][1:])
        self.assertLess(session.held(), size * 1.25)


if __name__ == '__main__':
    unittest.main()
