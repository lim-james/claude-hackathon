"""Selection, font reuse and refusals of text_tools beyond the common path."""
import functools
import io
import unittest
import fitz
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
from core import PDF
from text_tools import find_matches, font_for, removal_rects, replace_match, select_text

FONT_NAME = 'NotoSerif-Regular'


def embedded_pdf(text, font_file=None):
    """A one-page PDF whose text uses an embedded font, named the way producers
    name subsets (ABCDEF+PostScriptName). `font_file` replaces the stored font
    program, leaving the page content as written."""
    full = fitz.Font().buffer  # MuPDF's built-in Noto Serif
    doc = fitz.open()
    page = doc.new_page()
    xref = page.insert_font(fontname='F0', fontbuffer=full)
    page.insert_text((50, 100), text, fontname='F0', fontsize=14, color=(0.2, 0.3, 0.7))
    doc.xref_set_key(xref, 'BaseFont', '/ABCDEF+' + FONT_NAME)
    if font_file:
        stored = [x for x in range(1, doc.xref_length()) if doc.xref_is_stream(x) and doc.xref_stream(x) == full]
        assert len(stored) == 1, stored
        doc.update_stream(stored[0], font_file)
    data = doc.tobytes()
    doc.close()
    pdf = PDF()
    pdf.restore(data)
    return pdf


@functools.lru_cache(maxsize=None)
def mac_only_font():
    """Noto Serif as a bare subset: no Unicode cmap and no glyph names, only a
    Macintosh cmap for ASCII. Glyph ids stay as they were."""
    font = TTFont(io.BytesIO(fitz.Font().buffer))
    best, old = font.getBestCmap(), font.getGlyphOrder()
    new = ['.notdef'] + ['glyph%05d' % n for n in range(1, len(old))]
    renamed = dict(zip(old, new))
    top = font['CFF '].cff.topDictIndex[0]
    top.CharStrings.charStrings = {renamed[name]: item for name, item in top.CharStrings.charStrings.items()}
    top.charset = new
    font.setGlyphOrder(new)
    for table in ('GSUB', 'GPOS', 'GDEF'):  # they name glyphs too; not needed here
        del font[table]
    mac = CmapSubtable.newSubtable(6)
    mac.platformID, mac.platEncID, mac.language = 1, 0, 0
    mac.cmap = {code: renamed[best[code]] for code in range(32, 127)}
    font['cmap'].tables = [mac]
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def spans(page):
    return [s for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.pdf = PDF()
        self.addCleanup(self.pdf.close)
        self.page = self.pdf.doc.new_page()

    def test_empty_query_finds_nothing(self):
        self.page.insert_text((50, 100), 'some text')
        self.assertEqual(find_matches(self.pdf.doc, ''), [])

    def test_selecting_on_a_page_without_text_selects_nothing(self):
        self.assertEqual(select_text(self.page, (10, 10), (200, 200)), ([], ''))

    def test_vertical_text_is_selected_one_quad_per_character(self):
        self.page.insert_text((100, 300), 'vertical', fontsize=12, rotate=90)
        quads, text = select_text(self.page, (95, 299), (95, 262))
        self.assertEqual(text, 'vertical')
        self.assertEqual(len(quads), len('vertical'))
        line = fitz.Rect(self.page.search_for('vertical')[0])
        for quad in quads:
            self.assertTrue((line + (-1, -1, 1, 1)).contains(quad.rect), quad)
        # The text runs up the page: each character sits above the one before.
        tops = [quad.rect.y0 for quad in quads]
        self.assertEqual(tops, sorted(tops, reverse=True))


class RemovalRectTests(unittest.TestCase):
    @staticmethod
    def char(x0, x1, baseline=100, height=10):
        return {'bbox': (x0, baseline - height, x1, baseline + 2), 'origin': (x0, baseline)}

    def test_touching_characters_on_one_baseline_become_one_rect(self):
        rects = removal_rects([self.char(16, 22), self.char(10, 16), self.char(22, 30)])
        self.assertEqual(len(rects), 1)
        self.assertAlmostEqual(rects[0].x0, 10.05)
        self.assertAlmostEqual(rects[0].x1, 29.95)

    def test_a_gap_or_a_new_row_starts_another_rect(self):
        rects = removal_rects([self.char(10, 16), self.char(40, 46), self.char(10, 16, baseline=120)])
        self.assertEqual([(round(r.x0), round(r.y1)) for r in rects], [(10, 102), (40, 102), (10, 122)])

    def test_rects_stay_inside_the_glyph_bounds(self):
        (rect,) = removal_rects([self.char(10, 16)])
        self.assertTrue(fitz.Rect(10, 90, 16, 102).contains(rect))
        self.assertLess(rect.width, 6)

    def test_characters_without_area_are_ignored(self):
        self.assertEqual(removal_rects([self.char(10, 10.1), self.char(10, 16, height=-1.9)]), [])


class ReplaceRefusalTests(unittest.TestCase):
    """Every refusal happens before the page is touched."""

    def setUp(self):
        self.pdf = PDF()
        self.addCleanup(self.pdf.close)
        self.page = self.pdf.doc.new_page()
        self.page.insert_text((50, 100), 'plain words here', fontname='helv', fontsize=12)

    def refused(self, match, text, message):
        before = self.pdf.doc[0].get_text()
        annotations = len(list(self.pdf.doc[0].annots()))
        with self.assertRaisesRegex(ValueError, message):
            replace_match(self.pdf.doc, match, text)
        self.assertEqual(self.pdf.doc[0].get_text(), before)
        self.assertEqual(len(list(self.pdf.doc[0].annots())), annotations)

    def test_vertical_text_is_not_replaced(self):
        self.page.insert_text((300, 400), 'sideways', fontsize=12, rotate=90)
        self.refused(find_matches(self.pdf.doc, 'sideways')[0], 'upright', 'horizontal text')

    def test_multi_line_replacement_is_refused(self):
        match = find_matches(self.pdf.doc, 'words')[0]
        self.refused(match, 'two\nlines', 'single-line')
        self.refused(match, 'two\rlines', 'single-line')

    def test_match_across_two_styles_is_refused(self):
        end = self.page.search_for('here')[0].x1
        self.page.insert_text((end, 100), 'after', fontname='hebo', fontsize=12)
        matches = find_matches(self.pdf.doc, 'hereafter')
        self.assertEqual(len(matches), 1, 'the two runs share one line')
        self.refused(matches[0], 'now', 'multiple text styles')

    def test_pending_redaction_is_not_applied_by_a_replacement(self):
        self.page.add_redact_annot(fitz.Rect(300, 300, 350, 320))
        self.refused(find_matches(self.pdf.doc, 'words')[0], 'terms', 'redaction annotations')

    def test_standard_font_cannot_take_characters_it_lacks(self):
        self.refused(find_matches(self.pdf.doc, 'words')[0], 'Ω', 'Cannot safely reuse font')

    def test_font_that_is_not_on_the_page_is_not_substituted(self):
        match = find_matches(self.pdf.doc, 'words')[0]
        match['style'] = dict(match['style'], font='Papyrus')
        for char in match['chars']:
            char['style'] = match['style']
        self.refused(match, 'terms', 'Cannot safely reuse font Papyrus')


class CloseLineTests(unittest.TestCase):
    @unittest.expectedFailure  # replacing a word also deletes the letters above it when the glyph boxes overlap
    def test_replacing_a_word_keeps_the_closely_spaced_line_above(self):
        pdf = PDF()
        self.addCleanup(pdf.close)
        page = pdf.doc.new_page()
        page.insert_text((50, 100), 'Total due today', fontsize=12)
        page.insert_text((50, 114), 'second line here', fontsize=12)
        replace_match(pdf.doc, find_matches(pdf.doc, 'line')[0], 'lime')
        self.assertIn('Total due today', pdf.doc[0].get_text())


class EmbeddedFontTests(unittest.TestCase):
    def open(self, text, font_file=None):
        pdf = embedded_pdf(text, font_file)
        self.addCleanup(lambda: pdf.close())
        return pdf

    def test_replacement_reuses_the_embedded_font_and_style(self):
        pdf = self.open('Hello embedded world')
        match = find_matches(pdf.doc, 'embedded')[0]
        self.assertEqual(match['style']['font'], FONT_NAME)
        replace_match(pdf.doc, match, 'inline')
        pdf.restore(pdf.bytes())
        page = pdf.doc[0]
        self.assertFalse(page.search_for('embedded'))
        new = next(s for s in spans(page) if s['text'] == 'inline')
        for key in ('font', 'size', 'color'):
            self.assertEqual(new[key], match['style'][key], key)
        self.assertAlmostEqual(new['origin'][0], match['origin'][0], places=2)
        self.assertAlmostEqual(new['origin'][1], match['origin'][1], places=2)
        # The outlines were embedded again; a standard font did not stand in.
        self.assertTrue(all(kind == 'Type0' for _, _, kind, *_ in page.get_fonts()), page.get_fonts())

    def test_characters_missing_from_the_embedded_font_are_named_and_nothing_changes(self):
        pdf = self.open('Hello embedded world')
        before = pdf.doc[0].get_text()
        with self.assertRaisesRegex(ValueError, 'lacks these replacement characters: 中文'):
            replace_match(pdf.doc, find_matches(pdf.doc, 'embedded')[0], 'a文中')
        self.assertEqual(pdf.doc[0].get_text(), before)

    def test_font_is_prepared_once_per_document(self):
        pdf = self.open('Hello embedded world')
        style = find_matches(pdf.doc, 'Hello')[0]['style']
        font, data, alias = font_for(pdf.doc, pdf.doc[0], style, 'abc')
        self.assertTrue(data)
        self.assertIsNone(alias)
        self.assertIs(font_for(pdf.doc, pdf.doc[0], style, 'cab xyz')[0], font)

    def test_subset_with_only_a_macintosh_cmap_is_repaired_from_the_page_text(self):
        subset = mac_only_font()
        self.assertFalse(fitz.Font(fontbuffer=subset).has_glyph(ord('e')), 'the subset alone maps no characters')
        pdf = self.open('Café menu', subset)
        self.assertEqual(pdf.doc[0].get_text().strip(), 'Café menu')
        # é is known only from the page's own text; Z and o only from the
        # font's Macintosh table.
        replace_match(pdf.doc, find_matches(pdf.doc, 'menu')[0], 'Zoé')
        pdf.restore(pdf.bytes())
        self.assertEqual(pdf.doc[0].get_text().strip(), 'Café Zoé')
        self.assertEqual({s['font'] for s in spans(pdf.doc[0])}, {FONT_NAME})

    def test_repaired_subset_still_refuses_characters_it_never_showed(self):
        pdf = self.open('Café menu', mac_only_font())
        with self.assertRaisesRegex(ValueError, 'lacks these replacement characters: ü'):
            replace_match(pdf.doc, find_matches(pdf.doc, 'menu')[0], 'müsli')
        self.assertEqual(pdf.doc[0].get_text().strip(), 'Café menu')


if __name__ == '__main__':
    unittest.main()
