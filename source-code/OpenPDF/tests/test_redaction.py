"""Redaction removes form fields and comments under the area, not only page content."""
import tempfile
import unittest
from pathlib import Path
import fitz
from core import PDF
from pen_tools import add_stroke

AREA = fitz.Rect(60, 190, 320, 250)
INSIDE = fitz.Rect(72, 200, 300, 240)
OUTSIDE = fitz.Rect(72, 400, 300, 440)


class RedactionOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = PDF()
        self.pdf.doc.new_page()

    def tearDown(self):
        self.pdf.close()
        self.tmp.cleanup()

    def export(self):
        path = self.root / 'redacted.pdf'
        self.pdf.save(path)
        return fitz.open(path)

    def has(self, doc, secret):
        # The secret in any object or stream, as literal, UTF-16 or hex string.
        forms = (secret.encode(), secret.encode('utf-16-be'),
                 secret.encode('utf-16-be').hex().encode(), secret.encode().hex().encode())
        blobs = [doc.xref_object(x).encode() + (doc.xref_stream(x) or b'')
                 for x in range(1, doc.xref_length())]
        return any(form.lower() in blob.lower() for blob in blobs for form in forms)

    def assert_clean(self, *secrets):
        with self.export() as doc:
            page = doc[0]
            self.assertEqual([w.field_name for w in page.widgets() if w.rect.intersects(AREA)], [])
            self.assertEqual([a.type[1] for a in page.annots() if a.rect.intersects(AREA)], [])
            for secret in secrets:
                self.assertFalse(self.has(doc, secret), secret)
            return page.get_text()

    def test_issue_script_field_and_note_are_gone(self):
        self.pdf.doc[0].insert_text((72, 215), 'PAGE-TEXT-SECRET', fontsize=12)
        self.pdf.add_field(0, fitz.Rect(72, 220, 300, 245), 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET-123-45-6789')
        self.pdf.note(0, fitz.Point(200, 195), 'NOTE-SECRET')
        self.pdf.redact(0, AREA)
        text = self.assert_clean('FIELD-SECRET', 'NOTE-SECRET', 'PAGE-TEXT-SECRET')
        self.assertEqual(text.strip(), '')
        with self.export() as doc:
            self.assertEqual(doc[0].search_for('FIELD-SECRET'), [])
            self.assertEqual(list(doc[0].widgets()), [])
            self.assertEqual(list(doc[0].annots()), [])

    def test_every_field_kind_is_removed(self):
        cases = {'text': ('TEXT-SECRET', None), 'checkbox': ('yes', None),
                 'dropdown': ('DROP-SECRET', ['DROP-SECRET', 'DROP-OTHER']),
                 'listbox': ('LIST-SECRET', ['LIST-SECRET', 'LIST-OTHER']),
                 'signature': (None, None)}
        self.assertEqual(set(cases), set(PDF.FIELD_TYPES))
        for kind, (value, choices) in cases.items():
            with self.subTest(kind=kind):
                self.pdf.restore(self.blank())
                name = f'FIELDNAME-{kind}'
                self.pdf.add_field(0, INSIDE, name, kind, choices)
                if value:
                    self.pdf.fill_field(0, name, value)
                self.pdf.redact(0, AREA)
                self.assert_clean(name, *(choices or []), *([value] if kind == 'text' else []))
                self.assertEqual(self.pdf.fields(), [])
                catalog = self.pdf.doc.pdf_catalog()
                self.assertEqual(self.pdf.doc.xref_get_key(catalog, 'AcroForm/Fields')[1], '[]')

    def blank(self):
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        return data

    def test_every_annotation_kind_is_removed(self):
        def markup(kind):
            def make():
                page = self.pdf.doc[0]
                page.insert_text((80, 220), 'marked words')
                annot = self.pdf.markup(0, page.search_for('marked words', quads=True), kind, 'AUTHOR-SECRET')
                self.pdf.doc.xref_set_key(annot.xref, 'Contents', '(ANNOT-SECRET)')  # its comment
            return make
        makers = {kind: markup(kind) for kind in PDF.MARKUP}
        makers.update({
            'note': lambda: self.pdf.note(0, fitz.Point(200, 195), 'ANNOT-SECRET', 'AUTHOR-SECRET'),
            'callout': lambda: self.pdf.callout(0, INSIDE, 'ANNOT-SECRET', 'AUTHOR-SECRET'),
            'stamp': lambda: self.pdf.stamp(0, INSIDE, 2, 'AUTHOR-SECRET'),
            'ink': lambda: add_stroke(self.pdf.doc[0], [(80, 200), (120, 230), (200, 210)]),
        })
        for shape in ('line', 'arrow', 'rectangle', 'oval'):
            makers[shape] = lambda shape=shape: self.pdf.shape(0, shape, INSIDE, author='AUTHOR-SECRET')
        for kind, make in makers.items():
            with self.subTest(kind=kind):
                self.pdf.restore(self.blank())
                make()
                self.assertEqual(len(self.pdf.annotations()), 1)
                self.pdf.redact(0, AREA)
                self.assert_clean('ANNOT-SECRET', 'AUTHOR-SECRET', 'Freehand pen')
                self.assertEqual(self.pdf.annotations(), [])

    def test_reply_to_a_redacted_note_is_removed(self):
        note = self.pdf.note(0, fitz.Point(200, 195), 'NOTE-SECRET')
        reply = self.pdf.note(0, fitz.Point(200, 500), 'REPLY-SECRET')  # sits outside the area
        self.pdf.doc.xref_set_key(reply.xref, 'IRT', f'{note.xref} 0 R')
        self.pdf.redact(0, AREA)
        self.assert_clean('NOTE-SECRET', 'REPLY-SECRET')
        self.assertEqual(self.pdf.annotations(), [])

    def test_partial_overlap_counts_as_inside(self):
        self.pdf.add_field(0, fitz.Rect(300, 240, 500, 300), 'edge')  # only a corner is inside
        self.pdf.fill_field(0, 'edge', 'EDGE-FIELD-SECRET')
        self.pdf.callout(0, fitz.Rect(10, 100, 70, 200), 'EDGE-NOTE-SECRET')
        self.pdf.redact(0, AREA)
        self.assert_clean('EDGE-FIELD-SECRET', 'EDGE-NOTE-SECRET')
        self.assertEqual(self.pdf.fields(), [])
        self.assertEqual(self.pdf.annotations(), [])

    def test_field_and_note_outside_the_area_are_kept(self):
        self.pdf.add_field(0, INSIDE, 'gone')
        self.pdf.fill_field(0, 'gone', 'FIELD-SECRET')
        self.pdf.add_field(0, OUTSIDE, 'kept')
        self.pdf.fill_field(0, 'kept', 'KEPT-VALUE')
        self.pdf.note(0, fitz.Point(200, 500), 'KEPT-NOTE')
        self.pdf.redact(0, AREA)
        with self.export() as doc:
            self.assertEqual([(w.field_name, w.field_value) for w in doc[0].widgets()],
                             [('kept', 'KEPT-VALUE')])
            self.assertEqual([a.info['content'] for a in doc[0].annots()], ['KEPT-NOTE'])
            self.assertFalse(self.has(doc, 'FIELD-SECRET'))

    def test_value_held_on_a_parent_field_is_removed_with_its_last_widget(self):
        # A field whose /V lives on a parent, as radio groups and multi-widget fields do.
        doc = self.pdf.doc
        self.pdf.add_field(0, INSIDE, 'first')
        self.pdf.add_field(0, OUTSIDE, 'second')
        kids = [w.xref for w in doc[0].widgets()]
        parent = doc.get_new_xref()
        doc.update_object(parent, '<</FT/Tx/T(group)/V(PARENT-SECRET)/Kids[%d 0 R %d 0 R]>>' % tuple(kids))
        for kid in kids:
            doc.xref_set_key(kid, 'Parent', f'{parent} 0 R')
            doc.xref_set_key(kid, 'T', 'null')
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/Fields', f'[{parent} 0 R]')
        self.pdf.redact(0, AREA)
        # One widget is left outside the area, so the shared field and value stay.
        self.assertEqual([(name, value) for _, name, value, _, _ in self.pdf.fields()],
                         [('group', 'PARENT-SECRET')])
        self.pdf.redact(0, OUTSIDE)
        self.assertEqual(self.pdf.fields(), [])
        self.assertEqual(doc.xref_get_key(doc.pdf_catalog(), 'AcroForm/Fields')[1], '[]')
        with self.export() as saved:
            self.assertFalse(self.has(saved, 'PARENT-SECRET'))

    def test_redacted_field_is_absent_from_form_data_export(self):
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
        self.pdf.add_field(0, OUTSIDE, 'kept')
        self.pdf.fill_field(0, 'kept', 'KEPT-VALUE')
        self.pdf.redact(0, AREA)
        path = self.root / 'form.xfdf'
        self.pdf.export_form_data(path)
        data = path.read_text(encoding='utf-8')
        self.assertNotIn('FIELD-SECRET', data)
        self.assertNotIn('ssn', data)
        self.assertIn('KEPT-VALUE', data)

    def test_selected_text_redaction_removes_field_and_note_over_the_text(self):
        page = self.pdf.doc[0]
        page.insert_text((80, 225), 'SELECTED-SECRET stays-public')
        quads = page.search_for('SELECTED-SECRET', quads=True)
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
        self.pdf.note(0, fitz.Point(90, 215), 'NOTE-SECRET')
        self.pdf.redact_quads(0, quads)
        text = self.assert_clean('FIELD-SECRET', 'NOTE-SECRET', 'SELECTED-SECRET')
        self.assertIn('stays-public', text)
        self.assertEqual(self.pdf.fields(), [])
        self.assertEqual(self.pdf.annotations(), [])

    def test_undo_snapshot_brings_field_and_note_back(self):
        self.pdf.add_field(0, INSIDE, 'ssn')
        self.pdf.fill_field(0, 'ssn', 'FIELD-SECRET')
        self.pdf.note(0, fitz.Point(200, 195), 'NOTE-SECRET')
        before = self.pdf.bytes()  # what App.change keeps for undo
        self.pdf.redact(0, AREA)
        self.assertEqual(self.pdf.fields(), [])
        self.pdf.restore(before)
        self.assertEqual([(name, value) for _, name, value, _, _ in self.pdf.fields()],
                         [('ssn', 'FIELD-SECRET')])
        self.assertEqual([a['text'] for a in self.pdf.annotations()], ['NOTE-SECRET'])

    def test_text_edit_under_a_note_keeps_the_note(self):
        # replace_text uses a redact annotation only to delete glyphs.
        page = self.pdf.doc[0]
        page.insert_text((80, 225), 'draft-wording')
        self.pdf.note(0, page.search_for('draft-wording')[0].tl, 'KEPT-NOTE')
        self.pdf.add_field(0, INSIDE, 'kept')
        self.pdf.fill_field(0, 'kept', 'KEPT-VALUE')
        self.pdf.replace_text(0, 'draft-wording', 'final', 8)
        self.assertNotIn('draft-wording', self.pdf.doc[0].get_text())
        self.assertEqual([a['text'] for a in self.pdf.annotations()], ['KEPT-NOTE'])
        self.assertEqual([(name, value) for _, name, value, _, _ in self.pdf.fields()],
                         [('kept', 'KEPT-VALUE')])


if __name__ == '__main__':
    unittest.main()
