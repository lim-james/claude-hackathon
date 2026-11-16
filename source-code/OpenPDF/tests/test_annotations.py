import tempfile
import unittest
from pathlib import Path
import fitz
from core import PDF


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.pdf = PDF()
        page = self.pdf.doc.new_page(width=500, height=700)
        page.insert_text((40, 50), 'The quick brown fox jumps')
        self.pdf.doc.new_page(width=500, height=700)
        self.blank = self.pdf.bytes()

    def tearDown(self):
        self.pdf.close()

    def quads(self, word='quick'):
        return self.pdf.doc[0].search_for(word, quads=True)

    def test_text_markup_kinds_survive_reopen(self):
        for kind, name in [('strikeout', 'StrikeOut'), ('underline', 'Underline'),
                           ('squiggly', 'Squiggly'), ('highlight', 'Highlight')]:
            with self.subTest(kind=kind):
                pdf = PDF()
                try:
                    page = pdf.doc.new_page(width=500, height=700)
                    page.insert_text((40, 50), 'The quick brown fox')
                    pdf.markup(0, page.search_for('quick', quads=True), kind, author='Tester')
                    pdf.restore(pdf.bytes())
                    reopened = pdf.doc[0]
                    annots = list(reopened.annots())
                    self.assertEqual(len(annots), 1)
                    self.assertEqual(annots[0].type[1], name)
                    self.assertEqual(annots[0].info.get('title'), 'Tester')
                finally:
                    pdf.close()

    def test_markup_requires_quads_and_valid_kind(self):
        with self.assertRaises(ValueError):
            self.pdf.markup(0, [], 'strikeout')
        with self.assertRaises(ValueError):
            self.pdf.markup(0, self.quads(), 'nonsense')

    def test_note_and_callout(self):
        self.pdf.note(0, fitz.Point(60, 60), 'A sticky note', author='Tester')
        self.pdf.callout(0, fitz.Rect(100, 200, 300, 260), 'A callout', author='Tester')
        self.pdf.restore(self.pdf.bytes())
        page = self.pdf.doc[0]
        kinds = sorted(a.type[1] for a in page.annots())
        self.assertEqual(kinds, ['FreeText', 'Text'])
        contents = sorted(a.info.get('content', '') for a in page.annots())
        self.assertIn('A sticky note', contents)
        self.assertIn('A callout', contents)

    def test_empty_note_or_callout_rejected(self):
        with self.assertRaises(ValueError):
            self.pdf.note(0, fitz.Point(60, 60), '   ')
        with self.assertRaises(ValueError):
            self.pdf.callout(0, fitz.Rect(100, 200, 300, 260), '')

    def test_shapes_including_arrow(self):
        rect = fitz.Rect(60, 300, 200, 360)
        expected = {'line': 'Line', 'arrow': 'Line', 'rectangle': 'Square', 'oval': 'Circle'}
        for kind, name in expected.items():
            with self.subTest(kind=kind):
                pdf = PDF()
                try:
                    pdf.doc.new_page(width=500, height=700)
                    pdf.shape(0, kind, rect, color=(1, 0, 0), width=2)
                    pdf.restore(pdf.bytes())
                    reopened = pdf.doc[0]
                    annots = list(reopened.annots())
                    self.assertEqual(len(annots), 1)
                    self.assertEqual(annots[0].type[1], name)
                finally:
                    pdf.close()
        with self.assertRaises(ValueError):
            self.pdf.shape(0, 'triangle', rect)

    def test_stamp_label_and_bounds(self):
        self.pdf.stamp(0, fitz.Rect(50, 350, 220, 395), 2, author='Tester')  # Confidential
        self.pdf.restore(self.pdf.bytes())
        page = self.pdf.doc[0]
        annot = next(iter(page.annots()))
        self.assertEqual(annot.type[1], 'Stamp')
        self.assertEqual(annot.info.get('content'), 'Confidential')
        for bad in (-1, len(PDF.STAMPS)):
            with self.assertRaises(ValueError):
                self.pdf.stamp(0, fitz.Rect(50, 350, 220, 395), bad)

    def test_annotations_enumeration_and_delete(self):
        self.pdf.markup(0, self.quads(), 'underline', author='A')
        self.pdf.note(1, fitz.Point(60, 60), 'note on page two', author='B')
        items = self.pdf.annotations()
        self.assertEqual(len(items), 2)
        self.assertEqual({i['page'] for i in items}, {0, 1})
        note = next(i for i in items if i['page'] == 1)
        self.assertEqual(note['text'], 'note on page two')
        self.assertEqual(note['author'], 'B')
        # delete the note, keep the markup
        self.assertTrue(self.pdf.delete_annotation(note))
        self.pdf.restore(self.pdf.bytes())
        remaining = self.pdf.annotations()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]['kind'], 'Underline')
        # deleting a row that is already gone is a no-op, not an error
        self.assertFalse(self.pdf.delete_annotation(note))
        self.assertFalse(self.pdf.delete_annotation(dict(note, page=99)))
        self.assertEqual(len(self.pdf.annotations()), 1)

    def six_notes(self):
        for n in range(1, 7):
            self.pdf.note(0, fitz.Point(300, 60 + 40 * n), f'note {n}')
        return self.pdf.annotations()

    def texts(self):
        return [item['text'] for item in self.pdf.annotations()]

    def test_delete_uses_the_held_row_after_the_document_is_renumbered(self):
        # Issue #74: Save as, Save version and opening Optimize all write with
        # garbage=4, which renumbers the open document under an open Comment list.
        with tempfile.TemporaryDirectory() as folder:
            renumber = {
                'save as': lambda: self.pdf.save(str(Path(folder) / 'saved.pdf')),
                'save version': lambda: self.pdf.doc.tobytes(garbage=4, deflate=True),
                'optimize': lambda: self.pdf.current_size(),
            }
            for name, action in renumber.items():
                with self.subTest(after=name):
                    self.pdf.restore(self.blank)
                    rows = self.six_notes()
                    action()
                    self.assertTrue(self.pdf.delete_annotation(rows[3]))
                    self.assertEqual(self.texts(), ['note 1', 'note 2', 'note 3', 'note 5', 'note 6'])
                    self.assertTrue(self.pdf.delete_annotation(rows[2]))
                    self.assertEqual(self.texts(), ['note 1', 'note 2', 'note 5', 'note 6'])

    def test_deleting_a_held_row_twice_removes_nothing_else(self):
        rows = self.six_notes()
        self.pdf.current_size()
        self.assertTrue(self.pdf.delete_annotation(rows[3]))
        self.assertFalse(self.pdf.delete_annotation(rows[3]))
        self.assertEqual(self.texts(), ['note 1', 'note 2', 'note 3', 'note 5', 'note 6'])

    def test_delete_picks_the_selected_one_of_two_identical_comments(self):
        self.pdf.note(0, fitz.Point(300, 100), 'twin')
        self.pdf.note(0, fitz.Point(300, 100), 'twin')
        self.pdf.note(0, fitz.Point(300, 200), 'other')
        rows = self.pdf.annotations()
        first = self.pdf.doc[0].first_annot.info['id']
        self.pdf.current_size()
        self.assertTrue(self.pdf.delete_annotation(rows[1]))
        self.assertEqual(self.texts(), ['twin', 'other'])
        self.assertEqual(self.pdf.doc[0].first_annot.info['id'], first)
        # Once the page has changed, a row that fits two comments deletes neither.
        self.pdf.note(0, fitz.Point(300, 100), 'twin')
        self.assertFalse(self.pdf.delete_annotation(dict(rows[1], index=1)))
        self.assertEqual(self.texts(), ['twin', 'other', 'twin'])

    def test_delete_finds_a_comment_that_moved_up_the_page_list(self):
        rows = self.six_notes()
        page = self.pdf.doc[0]
        page.delete_annot(page.first_annot)  # removed in the main window, list still open
        self.pdf.current_size()
        self.assertTrue(self.pdf.delete_annotation(rows[3]))
        self.assertEqual(self.texts(), ['note 2', 'note 3', 'note 5', 'note 6'])

    def test_delete_works_for_a_comment_without_a_name(self):
        self.pdf.note(0, fitz.Point(300, 100), 'from another program')
        self.pdf.note(0, fitz.Point(300, 200), 'keep')
        for annot in self.pdf.doc[0].annots():
            self.pdf.doc.xref_set_key(annot.xref, 'NM', 'null')
        self.pdf.restore(self.pdf.bytes())
        rows = self.pdf.annotations()
        self.assertEqual(self.pdf.doc.xref_get_key(rows[0]['xref'], 'NM')[0], 'null')
        self.pdf.current_size()
        self.assertTrue(self.pdf.delete_annotation(rows[0]))
        self.assertEqual(self.texts(), ['keep'])


if __name__ == '__main__':
    unittest.main()
