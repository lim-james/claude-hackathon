"""Removing text must leave the lines directly above and below it alone."""
import unittest
import fitz
from box_tools import text_sections, edit_section
from text_tools import find_matches, replace_match, removal_rects, lines

# Descenders above the edited line and tall letters below it: the glyphs
# that sit closest to the removal area.
ABOVE, EDITED, BELOW = 'gjpqy,;gjpqy,;gjpqy_gjpqy', "Pay (the) invoice, 'now'.", 'ÅÉbdfhkl|ÅÉbdfhkl|ÅÉbdfhkl'


def page_with(rows, embedded=None, rotation=0):
    """rows: (baseline, text, font, size). embedded: a font file's bytes."""
    doc = fitz.open()
    page = doc.new_page()
    if embedded:
        page.insert_font(fontname='E0', fontbuffer=embedded)
    for y, text, font, size in rows:
        page.insert_text((72, y), text, fontname='E0' if embedded else font, fontsize=size)
    page.set_rotation(rotation)
    return doc


def three_lines(font, size, gap, **options):
    return page_with([(300 - gap, ABOVE, font, size), (300, EDITED, font, size), (300 + gap, BELOW, font, size)], **options)


def glyphs(page, skip=()):
    """Every visible character with its position, except those in `skip`."""
    def key(char):
        return char['c'], round(char['origin'][0], 1), round(char['origin'][1], 1)
    skipped = {key(c) for c in skip}
    return sorted(key(c) for line in lines(page) for c in line['chars'] if c['c'].strip() and key(c) not in skipped)


class CloseLineTests(unittest.TestCase):
    def assert_only_match_changes(self, doc, query, replacement, label=''):
        match = find_matches(doc, query)[0]
        others = glyphs(doc[0], skip=match['chars'])
        replace_match(doc, match, replacement)
        remaining = glyphs(doc[0])
        self.assertEqual([g for g in others if g not in remaining], [], label + ': other text was deleted')
        text = doc[0].get_text()
        self.assertNotIn('invoice', text, label)
        self.assertIn(replacement, text, label)

    def test_issue_script_keeps_the_line_above(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 100), 'Total due today: 4,500 USD', fontsize=12)
            page.insert_text((72, 114.4), 'Please pay the invoice now', fontsize=12)
            replace_match(doc, find_matches(doc, 'invoice')[0], 'bill')
            first, second = doc[0].get_text(sort=True).split('\n')[:2]
            self.assertEqual(first, 'Total due today: 4,500 USD')
            self.assertEqual(' '.join(second.split()), 'Please pay the bill now')

    def test_replacing_a_word_or_a_whole_line_keeps_both_neighbours(self):
        cases = [('helv', 12, 13.8), ('helv', 12, 14.4), ('helv', 12, 11), ('helv', 12, 8.4), ('helv', 10, 12),
                 ('helv', 11, 13.4), ('helv', 6, 6), ('helv', 30, 30), ('hebo', 12, 13.8), ('heit', 12, 13.8),
                 ('tiro', 12, 13.8), ('tiro', 12, 8.4), ('tibo', 12, 13.8), ('tiit', 12, 12), ('cour', 12, 12),
                 ('cour', 12, 8.4), ('cobo', 12, 14.4)]
        for font, size, gap in cases:
            for query, replacement in (('invoice', 'bill'), (EDITED, 'Paid')):
                with self.subTest(font=font, size=size, gap=gap, query=query), three_lines(font, size, gap) as doc:
                    self.assert_only_match_changes(doc, query, replacement, f'{font} {size} pt, {gap} pt apart')

    def test_embedded_font_keeps_both_neighbours(self):
        embedded = fitz.Font('cjk').buffer  # Droid Sans Fallback, shipped inside PyMuPDF
        for gap in (13.8, 12, 8.4):
            for query, replacement in (('invoice', 'bill'), (EDITED, 'Paid')):
                with self.subTest(gap=gap, query=query), three_lines(None, 12, gap, embedded=embedded) as saved:
                    with fitz.open('pdf', saved.tobytes()) as doc:
                        self.assertEqual(doc[0].get_fonts(full=True)[0][1], 'ttf')
                        self.assert_only_match_changes(doc, query, replacement, f'embedded, {gap} pt apart')

    def test_cjk_lines_keep_both_neighbours(self):
        rows = [(288, '請求書の合計金額です', 'japan', 12), (300, '今すぐ請求書を払う', 'japan', 12), (312, '支払期限は来週です', 'japan', 12)]
        with page_with(rows) as doc:
            match = find_matches(doc, '請求書を')[0]
            others = glyphs(doc[0], skip=match['chars'])
            replace_match(doc, match, '')
            self.assertEqual(glyphs(doc[0]), others)

    def test_small_line_between_two_headings_keeps_them(self):
        rows = [(280, ABOVE, 'helv', 24), (300, EDITED, 'helv', 10), (322, BELOW, 'helv', 24)]
        for query, replacement in (('invoice', 'bill'), (EDITED, 'Paid')):
            with self.subTest(query=query), page_with(rows) as doc:
                self.assert_only_match_changes(doc, query, replacement)

    def test_large_line_between_two_small_lines_keeps_them(self):
        rows = [(288, ABOVE, 'tiro', 8), (300, EDITED, 'helv', 18), (311, BELOW, 'cour', 8)]
        for query, replacement in (('invoice', 'bill'), (EDITED, 'Paid')):
            with self.subTest(query=query), page_with(rows) as doc:
                self.assert_only_match_changes(doc, query, replacement)

    def test_superscripts_and_their_base_line_do_not_take_each_other(self):
        def build():
            doc = fitz.open()
            page = doc.new_page()
            x = 72
            page.insert_text((72, 288), ABOVE, fontsize=12)
            for text, size, rise in (('Area in m', 12, 0), ('2,3', 7, 4.5), (' invoice[', 12, 0), ('14', 7, 4.5), ('] now', 12, 0)):
                page.insert_text((x, 300 - rise), text, fontsize=size)
                x += fitz.get_text_length(text, fontsize=size)
            page.insert_text((72, 312), BELOW, fontsize=12)
            return doc
        for query in ('invoice', 'in m', '2,3', '14', 'now'):
            with self.subTest(query=query), build() as doc:
                match = find_matches(doc, query)[0]
                others = glyphs(doc[0], skip=match['chars'])
                replace_match(doc, match, '')
                self.assertEqual(glyphs(doc[0]), others)

    def test_rotated_pages_keep_both_neighbours(self):
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation), three_lines('helv', 12, 13.8, rotation=rotation) as doc:
                self.assert_only_match_changes(doc, 'invoice', 'bill', f'rotated {rotation}')

    def test_editing_a_box_keeps_both_neighbours(self):
        for font, gap in (('helv', 13.8), ('helv', 8.4), ('tiro', 13.8), ('cour', 12)):
            rows = [(300 - gap, ABOVE, font, 12), (300, EDITED, 'tibo', 12), (300 + gap, BELOW, font, 12)]
            with self.subTest(font=font, gap=gap), page_with(rows) as doc:
                section = next(s for s in text_sections(doc[0]) if s['text'] == EDITED)
                others = glyphs(doc[0], skip=section['chars'])
                edit_section(doc, 0, section, 'Pay the bill')
                remaining = glyphs(doc[0])
                self.assertEqual([g for g in others if g not in remaining], [])
                self.assertNotIn('invoice', doc[0].get_text())
                self.assertIn('Pay the bill', doc[0].get_text())

    def test_one_thin_rectangle_per_line_inside_the_glyph_boxes(self):
        with three_lines('helv', 12, 14.4) as doc:
            for query in ('invoice', EDITED):
                match = find_matches(doc, query)[0]
                rects = removal_rects(match['chars'])
                self.assertEqual(len(rects), 1)
                box = fitz.Rect(match['rect'])
                self.assertLess(rects[0].height, box.height * .1)
                self.assertAlmostEqual((rects[0].y0 + rects[0].y1) / 2, (box.y0 + box.y1) / 2, places=3)
                self.assertAlmostEqual(rects[0].x0, box.x0 + .05, places=3)
                self.assertAlmostEqual(rects[0].x1, box.x1 - .05, places=3)


if __name__ == '__main__':
    unittest.main()
