"""Section detection, wrapping and refusals of box_tools beyond the common path."""
import io
import unittest
from unittest.mock import patch
import fitz
from PIL import Image
from core import PDF
from box_tools import edit_section, text_sections, wrap_text
from test_text_tools_edges import FONT_NAME, embedded_pdf


class Page(unittest.TestCase):
    def setUp(self):
        self.pdf = PDF()
        self.addCleanup(lambda: self.pdf.close())
        self.page = self.pdf.doc.new_page()

    def sections(self):
        self.pdf.restore(self.pdf.bytes())
        return text_sections(self.pdf.doc[0])


class SectionDetectionTests(Page):
    def test_pictures_blank_runs_and_vertical_text_are_not_sections(self):
        picture = io.BytesIO()
        Image.new('RGB', (40, 20), 'red').save(picture, format='PNG')
        self.page.insert_image(fitz.Rect(50, 400, 130, 440), stream=picture.getvalue())
        self.page.insert_text((50, 100), '   ', fontsize=12)
        self.page.insert_text((50, 250), 'sideways', fontsize=12, rotate=90)
        self.page.insert_text((50, 300), 'editable', fontsize=12)
        self.assertEqual([s['text'] for s in self.sections()], ['editable'])

    def test_overlapping_text_objects_in_one_style_become_one_section_in_reading_order(self):
        # Written out of order, as a replacement tool leaves them: the lower
        # row first, unrelated text, then the row above it.
        self.page.insert_text((60, 104), 'second row', fontsize=12)
        self.page.insert_text((60, 160), 'far away', fontsize=12)
        self.page.insert_text((50, 100), 'First row', fontsize=12)
        sections = self.sections()
        self.assertEqual([s['text'] for s in sections], ['First row\nsecond row', 'far away'])
        joined = sections[0]
        first, second = (self.pdf.doc[0].search_for(text)[0] for text in ('First row', 'second row'))
        for got, expected in zip(joined['rect'], first | second):
            self.assertAlmostEqual(got, expected, delta=0.5)
        self.assertEqual(tuple(joined['origin']), (50, 100))
        self.assertAlmostEqual(joined['line_height'], 4, places=3)
        # The joined section is edited as one, leaving its neighbour alone.
        edit_section(self.pdf.doc, 0, joined, 'Paid')
        self.assertEqual(self.pdf.doc[0].get_text().split('\n')[:2], ['far away', 'Paid'])

    def test_overlapping_text_in_another_style_stays_separate(self):
        self.page.insert_text((50, 100), 'Total ', fontsize=12)
        self.page.insert_text((70, 100.2), 'due', fontsize=12, color=(1, 0, 0))
        self.assertEqual(sorted(s['text'] for s in self.sections()), ['Total ', 'due'])


class WrapTests(unittest.TestCase):
    font = fitz.Font('helv')

    def wrap(self, text, width):
        return wrap_text(text, self.font, 12, width)

    def test_lines_break_at_spaces_and_fit_the_width(self):
        rows = self.wrap('alpha beta gamma delta', 60)
        self.assertEqual(rows, ['alpha beta', 'gamma', 'delta'])
        for row in rows:
            self.assertLessEqual(self.font.text_length(row, fontsize=12), 60.2)

    def test_explicit_newlines_and_blank_lines_are_kept(self):
        self.assertEqual(self.wrap('one\r\ntwo\n\nthree\rfour', 200), ['one', 'two', '', 'three', 'four'])

    def test_word_longer_than_the_box_is_split_across_rows(self):
        rows = self.wrap('a supercalifragilistic word', 40)
        self.assertEqual(''.join(rows), 'asupercalifragilisticword')
        self.assertGreater(len(rows), 3)
        for row in rows:
            self.assertLessEqual(self.font.text_length(row, fontsize=12), 40.2)

    def test_run_of_spaces_at_a_break_does_not_start_the_next_row(self):
        self.assertEqual(self.wrap('one      two', 22), ['one', 'two'])
        self.assertEqual(self.wrap('one' + ' ' * 40 + 'two', 22), ['one', 'two'])
        self.assertEqual(self.wrap(' ' * 40 + 'two', 22), ['two'])

    def test_tab_counts_as_four_spaces(self):
        self.assertEqual(self.wrap('a\tb', 100), ['a    b'])

    def test_box_narrower_than_one_character_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'narrower than one character'):
            self.wrap('W', 3)


class EditRefusalTests(Page):
    def setUp(self):
        super().setUp()
        self.page.insert_text((50, 100), 'Original paragraph', fontsize=12)
        self.section = self.sections()[0]

    def refused(self, message, text='Changed', rect=None):
        with self.assertRaisesRegex(ValueError, message):
            edit_section(self.pdf.doc, 0, self.section, text, rect)
        self.assertEqual(self.pdf.doc[0].get_text().strip(), 'Original paragraph')

    def test_box_dragged_off_the_page_is_refused(self):
        self.refused('inside the page', rect=fitz.Rect(self.section['rect']) + (560, 0, 560, 0))
        self.refused('inside the page', rect=fitz.Rect(50, -20, 200, 0))

    def test_box_without_area_is_refused(self):
        self.refused('inside the page', rect=fitz.Rect(50, 100, 50, 100.0001) & fitz.Rect(60, 0, 70, 10))

    def test_pending_redaction_is_not_applied_by_an_edit(self):
        self.pdf.doc[0].add_redact_annot(fitz.Rect(300, 300, 350, 320))
        self.refused('redaction annotations')
        self.assertEqual(len(list(self.pdf.doc[0].annots())), 1)

    def test_section_changed_since_it_was_selected_is_refused(self):
        edit_section(self.pdf.doc, 0, self.section, 'Edited elsewhere')
        with self.assertRaisesRegex(ValueError, 'section changed'):
            edit_section(self.pdf.doc, 0, self.section, 'Late edit')
        self.assertEqual(self.pdf.doc[0].get_text().strip(), 'Edited elsewhere')

    def test_failure_while_writing_reaches_the_caller(self):
        with patch('box_tools.register_layer', side_effect=RuntimeError('layer table full')):
            with self.assertRaisesRegex(RuntimeError, 'layer table full'):
                edit_section(self.pdf.doc, 0, self.section, 'Changed')


class CloseLineTests(Page):
    @unittest.expectedFailure  # editing a line also deletes the line above it when their glyph boxes overlap
    def test_editing_a_line_keeps_the_closely_spaced_line_above(self):
        self.page.insert_text((50, 100), 'Total due today', fontsize=12)
        self.page.insert_text((50, 114), 'second line here', fontsize=12, color=(1, 0, 0))
        section = next(s for s in self.sections() if s['text'] == 'second line here')
        edit_section(self.pdf.doc, 0, section, 'second lime here', fitz.Rect(section['rect']) + (0, 0, 40, 0))
        self.assertEqual(self.pdf.doc[0].get_text().splitlines(), ['Total due today', 'second lime here'])


class EmbeddedFontEditTests(unittest.TestCase):
    def test_edit_keeps_the_embedded_font(self):
        pdf = embedded_pdf('Hello embedded world')
        self.addCleanup(lambda: pdf.close())
        section = text_sections(pdf.doc[0])[0]
        self.assertEqual(edit_section(pdf.doc, 0, section, 'Edited words'), 1)
        pdf.restore(pdf.bytes())
        (edited,) = text_sections(pdf.doc[0])
        self.assertEqual(edited['text'], 'Edited words')
        for key in ('font', 'size', 'color'):
            self.assertEqual(edited['style'][key], section['style'][key], key)
        self.assertEqual(edited['style']['font'], FONT_NAME)
        self.assertTrue(all(kind == 'Type0' for _, _, kind, *_ in pdf.doc[0].get_fonts()))

    def test_edit_refuses_characters_the_embedded_font_lacks(self):
        pdf = embedded_pdf('Hello embedded world')
        self.addCleanup(lambda: pdf.close())
        section = text_sections(pdf.doc[0])[0]
        with self.assertRaisesRegex(ValueError, 'lacks these replacement characters'):
            edit_section(pdf.doc, 0, section, 'Hello 世界')
        self.assertEqual(pdf.doc[0].get_text().strip(), 'Hello embedded world')


if __name__ == '__main__':
    unittest.main()
