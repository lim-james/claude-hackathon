import itertools
import tempfile
import unittest
from pathlib import Path
import fitz
from core import PDF
from outline_tools import append_bookmarks

FIELDS = ('kind', 'page', 'to', 'zoom', 'bold', 'color', 'uri')


def outline(doc):
    """Level, title, page and the destination fields of every bookmark."""
    return [(level, title, page, {k: dest[k] for k in FIELDS if k in dest})
            for level, title, page, dest in doc.get_toc(simple=False)]


def shown_at(doc, x, y):
    """Where a mark painted at PDF point (x, y) appears on page 1 as rendered."""
    contents = doc.get_new_xref()
    doc.update_object(contents, '<<>>')
    doc.update_stream(contents, f'0 g {x - 2:g} {y - 2:g} 4 4 re f'.encode())
    doc.xref_set_key(doc[0].xref, 'Contents', f'{contents} 0 R')
    pix = doc[0].get_pixmap(alpha=False, colorspace=fitz.csGRAY)
    dark = [i for i, value in enumerate(pix.samples) if value < 128]
    if not dark:
        return None  # off the visible page
    return (round(sum(i % pix.width for i in dark) / len(dark) + 0.5),
            round(sum(i // pix.width for i in dark) / len(dark) + 0.5))


def stored(doc):
    """The outline objects exactly as they are written in the file."""
    return [doc.xref_object(xref, compressed=True) for xref in doc.get_outline_xrefs()]


class BookmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = PDF()
        for _ in range(3):
            self.pdf.doc.new_page()

    def tearDown(self):
        self.pdf.close()
        self.tmp.cleanup()

    def styled_outline(self):
        """A mid-page bookmark, a web link, and a zoomed, red, bold child."""
        doc = self.pdf.doc
        doc.set_toc([[1, 'Intro', 1], [1, 'Site', -1], [2, 'Method', 2]])
        toc = doc.get_toc(simple=False)
        toc[0][3]['to'] = fitz.Point(72, 500)
        toc[1][3] = {'kind': fitz.LINK_URI, 'uri': 'https://example.com'}
        toc[2][3].update(to=fitz.Point(72, 400), zoom=2.0, color=(1, 0, 0), bold=True)
        doc.set_toc(toc)

    def test_adding_a_bookmark_keeps_existing_ones(self):
        self.styled_outline()
        before, objects = outline(self.pdf.doc), stored(self.pdf.doc)
        self.assertEqual(before[1][3]['uri'], 'https://example.com')
        append_bookmarks(self.pdf.doc, [[1, 'New', 3]])
        after = outline(self.pdf.doc)
        self.assertEqual(after[:3], before)
        # Only the old last top-level item changes, and only by gaining /Next.
        now = stored(self.pdf.doc)
        self.assertEqual(now[0], objects[0])
        self.assertEqual(now[2], objects[2])
        self.assertEqual(now[1].replace(f'/Next {self.pdf.doc.get_outline_xrefs()[3]} 0 R', ''), objects[1])

    def test_new_bookmark_is_last_level_one_on_the_given_page(self):
        self.styled_outline()
        append_bookmarks(self.pdf.doc, [[1, 'New', 3]])
        level, title, page, dest = outline(self.pdf.doc)[-1]
        self.assertEqual((level, title, page), (1, 'New', 3))
        self.assertEqual((dest['kind'], dest['to']), (fitz.LINK_GOTO, fitz.Point(72, 36)))
        self.assertEqual(len(outline(self.pdf.doc)), 4)

    def test_first_bookmark_in_a_document_without_outline(self):
        self.assertEqual(self.pdf.doc.get_toc(), [])
        append_bookmarks(self.pdf.doc, [[1, 'Only', 2]])
        self.assertEqual(self.pdf.doc.get_toc(), [[1, 'Only', 2]])
        append_bookmarks(self.pdf.doc, [[1, 'Ünïcode (2)', 1]])
        self.assertEqual(self.pdf.doc.get_toc(), [[1, 'Only', 2], [1, 'Ünïcode (2)', 1]])

    def test_offset_and_rotated_pages_keep_their_destinations(self):
        doc = self.pdf.doc
        doc[0].set_cropbox(fitz.Rect(50, 50, 500, 700))
        doc.xref_set_key(doc[1].xref, 'MediaBox', '[0 100 595 942]')
        doc[2].set_rotation(90)
        doc.set_toc([[1, 'Cropped', 1, {'kind': fitz.LINK_GOTO, 'to': fitz.Point(72, 500)}],
                     [1, 'Shifted', 2, {'kind': fitz.LINK_GOTO, 'to': fitz.Point(72, 500)}],
                     [1, 'Turned', 3, {'kind': fitz.LINK_GOTO, 'to': fitz.Point(72, 500)}]])
        before = outline(doc)
        for page in (1, 2, 3):
            append_bookmarks(doc, [[1, f'New {page}', page]])
            append_bookmarks(doc, [[1, f'Point {page}', page, (100, 200)]])
        after = outline(doc)
        self.assertEqual(after[:3], before)
        # New bookmarks land at the top of the visible page whatever its boxes.
        self.assertEqual([entry[3]['to'] for entry in after[3::2]], [fitz.Point(72, 36)] * 3)
        self.assertEqual([entry[3]['to'] for entry in after[4::2]], [fitz.Point(100, 200)] * 3)
        self.assertEqual([entry[2] for entry in after[3:]], [1, 1, 2, 2, 3, 3])

    def test_destination_is_on_the_visible_page_for_every_rotation_and_box(self):
        # Media box at the origin or offset; crop box absent, at the origin or
        # offset; each under every rotation. A mark painted at the stored /XYZ
        # point must render at the point asked for, measured on the shown page.
        boxes = [('[0 0 595 842]', None), ('[0 0 595 842]', '[0 0 500 700]'),
                 ('[0 0 595 842]', '[50 50 500 700]'), ('[0 100 595 942]', None),
                 ('[0 100 595 942]', '[50 150 500 800]')]
        for (media, crop), rotation, point in itertools.product(boxes, (0, 90, 180, 270), (None, (100, 200))):
            with self.subTest(media=media, crop=crop, rotation=rotation, point=point):
                doc = fitz.open()
                page = doc.new_page()
                doc.xref_set_key(page.xref, 'MediaBox', media)
                if crop:
                    doc.xref_set_key(page.xref, 'CropBox', crop)
                doc.xref_set_key(page.xref, 'Rotate', str(rotation))
                append_bookmarks(doc, [[1, 'New', 1] + ([point] if point else [])])
                action = doc.xref_get_key(doc.get_outline_xrefs()[0], 'A/D')[1]
                x, y = (float(v) for v in action.split('/XYZ')[1].split()[:2])
                self.assertEqual(shown_at(doc, x, y), point or (72, 36))

    def test_nested_entries_follow_their_appended_parent(self):
        self.styled_outline()
        before = outline(self.pdf.doc)
        added = [[1, 'Appendix', 2], [2, 'A.1', 2], [3, 'A.1.1', 3], [2, 'A.2', 3], [1, 'Index', 3]]
        self.assertEqual(append_bookmarks(self.pdf.doc, added), 5)
        self.assertEqual(outline(self.pdf.doc)[:3], before)
        self.assertEqual(self.pdf.doc.get_toc()[3:], added)

    def test_rejects_entries_it_cannot_place(self):
        self.styled_outline()
        before = stored(self.pdf.doc)
        for entries in ([[2, 'Deep', 1]], [[1, 'A', 1], [3, 'B', 1]], [[1, 'Off', 4]], [[1, 'Zero', 0]]):
            with self.assertRaises(ValueError):
                append_bookmarks(self.pdf.doc, entries)
        self.assertEqual(append_bookmarks(self.pdf.doc, []), 0)
        self.assertEqual(stored(self.pdf.doc), before)

    def test_outline_with_missing_last_or_negative_count(self):
        self.styled_outline()
        doc = self.pdf.doc
        root = int(doc.xref_get_key(doc.pdf_catalog(), 'Outlines')[1].split()[0])
        doc.xref_set_key(root, 'Last', 'null')
        doc.xref_set_key(root, 'Count', '-2')
        before = outline(doc)
        append_bookmarks(doc, [[1, 'New', 3]])
        self.assertEqual(outline(doc), before + [(1, 'New', 3, {'kind': 1, 'page': 2, 'to': fitz.Point(72, 36), 'zoom': 0.0})])
        self.assertEqual(doc.xref_get_key(root, 'Count'), ('int', '-2'))

    def test_save_and_reopen(self):
        self.styled_outline()
        self.pdf.doc[0].set_cropbox(fitz.Rect(50, 50, 500, 700))
        before = outline(self.pdf.doc)
        append_bookmarks(self.pdf.doc, [[1, 'New', 3]])
        expected = outline(self.pdf.doc)
        path = Path(self.tmp.name) / 'bookmarks.pdf'
        self.pdf.save(path)
        with fitz.open(path) as saved:
            self.assertEqual(outline(saved), expected)
            self.assertEqual(outline(saved)[:3], before)
        with fitz.open(stream=self.pdf.doc.tobytes(garbage=4, deflate=True), filetype='pdf') as compacted:
            self.assertEqual(outline(compacted), expected)

    def test_undo_removes_only_the_new_bookmark(self):
        self.styled_outline()
        before = outline(self.pdf.doc)
        # The app's Undo: restore what was captured before the change. PR #58
        # captures with snapshot(); before it, with bytes().
        capture = getattr(self.pdf, 'snapshot', self.pdf.bytes)
        state = capture()
        append_bookmarks(self.pdf.doc, [[1, 'New', 3]])
        self.assertEqual(len(outline(self.pdf.doc)), 4)
        self.pdf.restore(state)
        self.assertEqual(outline(self.pdf.doc), before)


class MergeBookmarkTests(unittest.TestCase):
    """Merge brings the bookmarks of the files it adds, on their new pages."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pdf = PDF()
        for _ in range(2):
            self.pdf.doc.new_page()
        self.pdf.doc.set_toc([[1, 'Report', 1]])

    def tearDown(self):
        self.pdf.close()
        self.tmp.cleanup()

    def file(self, name, toc, pages=2, edit=None):
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page()
        doc.set_toc(toc, collapse=0)
        if edit:
            edit(doc)
        path = Path(self.tmp.name) / name
        doc.save(path)
        doc.close()
        return str(path)

    def appendix(self):
        return self.file('appendix.pdf', [[1, 'Appendix A', 1], [1, 'Appendix B', 2]])

    def test_merged_bookmarks_follow_their_pages(self):
        self.pdf.merge([self.appendix()])
        self.assertEqual(len(self.pdf.doc), 4)
        self.assertEqual(self.pdf.doc.get_toc(), [[1, 'Report', 1], [1, 'Appendix A', 3], [1, 'Appendix B', 4]])

    def test_open_document_without_bookmarks(self):
        plain = PDF()
        plain.doc.new_page()
        plain.merge([self.appendix()])
        self.assertEqual(plain.doc.get_toc(), [[1, 'Appendix A', 2], [1, 'Appendix B', 3]])
        plain.close()

    def test_two_files_in_one_call_and_nested_levels(self):
        nested = self.file('nested.pdf', [[1, 'Part', 1], [2, 'Chapter', 2], [3, 'Section', 3], [1, 'Index', 3]], pages=3)
        self.pdf.merge([self.appendix(), nested])
        self.assertEqual(len(self.pdf.doc), 7)
        self.assertEqual(self.pdf.doc.get_toc(), [
            [1, 'Report', 1], [1, 'Appendix A', 3], [1, 'Appendix B', 4],
            [1, 'Part', 5], [2, 'Chapter', 6], [3, 'Section', 7], [1, 'Index', 7]])

    def test_position_zoom_style_and_web_link_are_kept(self):
        def style(doc):
            doc[1].set_rotation(90)
            doc[2].set_cropbox(fitz.Rect(50, 50, 500, 700))
            toc = doc.get_toc(simple=False)
            toc[0][3]['to'] = fitz.Point(72, 500)
            toc[1][3].update(to=fitz.Point(72, 400), zoom=2.0, color=(1, 0, 0), bold=True, italic=True)
            toc[2][3] = {'kind': fitz.LINK_URI, 'uri': 'https://example.com/a(b)'}
            toc[3][3]['to'] = fitz.Point(100, 300)
            doc.set_toc(toc, collapse=0)
        path = self.file('styled.pdf', [[1, 'Mid page', 1], [2, 'Zoomed', 2], [1, 'Site', -1], [1, 'Cropped', 3]],
                         pages=3, edit=style)
        with fitz.open(path) as source:
            expected = outline(source)
            written = [source.xref_get_key(xref, 'A/D')[1].split('R', 1)[1] for xref in (source.get_outline_xrefs()[i] for i in (0, 1, 3))]
            italic = [entry[3].get('italic') for entry in source.get_toc(simple=False)]
        own = stored(self.pdf.doc)
        self.pdf.merge([path])
        merged = outline(self.pdf.doc)[1:]
        # Same as in the source file apart from the page numbers, moved by 2.
        self.assertEqual([(level, title, page if page < 0 else page - 2, dict(dest, page=dest['page'] - 2) if 'page' in dest else dest)
                          for level, title, page, dest in merged], expected)
        self.assertEqual(merged[0][3]['to'], fitz.Point(72, 500))
        self.assertEqual((merged[1][3]['zoom'], merged[1][3]['color'], merged[1][3]['bold']), (2.0, (1.0, 0.0, 0.0), True))
        self.assertEqual(merged[2][3]['uri'], 'https://example.com/a(b)')
        self.assertEqual([entry[3].get('italic') for entry in self.pdf.doc.get_toc(simple=False)[1:]], italic)
        # The view is the source's own text, on the copied page.
        xrefs = self.pdf.doc.get_outline_xrefs()
        self.assertEqual([self.pdf.doc.xref_get_key(xref, 'A/D')[1].split('R', 1)[1] for xref in (xrefs[1], xrefs[2], xrefs[4])], written)
        # The open document's own bookmark only gained /Next.
        self.assertEqual(stored(self.pdf.doc)[0].replace(f'/Next {xrefs[1]} 0 R', ''), own[0])
        saved = Path(self.tmp.name) / 'merged.pdf'
        self.pdf.save(saved)
        with fitz.open(saved) as reopened:
            self.assertEqual(outline(reopened)[1:], merged)

    def test_file_without_bookmarks_changes_nothing(self):
        before, objects = outline(self.pdf.doc), stored(self.pdf.doc)
        self.pdf.merge([self.file('plain.pdf', [])])
        self.assertEqual(len(self.pdf.doc), 4)
        self.assertEqual(outline(self.pdf.doc), before)
        self.assertEqual(stored(self.pdf.doc), objects)

    def test_named_and_unsupported_destinations_keep_their_entry(self):
        def rewire(doc):
            first, second, third, fourth = doc.get_outline_xrefs()
            catalog = doc.pdf_catalog()
            doc.xref_set_key(catalog, 'Dests', f'<</old [{doc[1].xref} 0 R /FitH 300]>>')
            tree = doc.get_new_xref()
            doc.update_object(tree, f'<</Names[(new) <</D[{doc[2].xref} 0 R /XYZ 10 20 0]>>]>>')
            doc.xref_set_key(catalog, 'Names', f'<</Dests {tree} 0 R>>')
            doc.xref_set_key(first, 'A', 'null')
            doc.xref_set_key(first, 'Dest', '/old')
            action = doc.get_new_xref()
            doc.update_object(action, '<</S/GoTo/D(new)>>')
            doc.xref_set_key(second, 'A', f'{action} 0 R')
            doc.xref_set_key(third, 'A', '<</S/JavaScript/JS(app.alert\\(1\\))>>')
            doc.xref_set_key(fourth, 'A', '<</S/GoToR/F(other.pdf)/D[1 /Fit]>>')
        path = self.file('named.pdf', [[1, 'Old name', 1], [1, 'New name', 1], [1, 'Script', 1], [1, 'Other file', 1]],
                         pages=3, edit=rewire)
        self.pdf.merge([path])
        self.assertEqual(self.pdf.doc.get_toc()[1:],
                         [[1, 'Old name', 4], [1, 'New name', 5], [1, 'Script', -1], [1, 'Other file', -1]])
        xrefs = self.pdf.doc.get_outline_xrefs()[1:]
        page = self.pdf.doc.page_xref
        self.assertEqual(self.pdf.doc.xref_get_key(xrefs[0], 'A/D'), ('array', f'[{page(3)} 0 R/FitH 300]'))
        self.assertEqual(self.pdf.doc.xref_get_key(xrefs[1], 'A/D'), ('array', f'[{page(4)} 0 R/XYZ 10 20 0]'))
        # Scripts and links to other files are not copied: the title opens nothing.
        for xref in xrefs[2:]:
            self.assertEqual(self.pdf.doc.xref_get_key(xref, 'A')[0], 'null')
            self.assertEqual(self.pdf.doc.xref_get_key(xref, 'Dest')[0], 'null')
        self.assertNotIn('JavaScript', ''.join(stored(self.pdf.doc)))

    def test_undo_after_merge_restores_pages_and_bookmarks(self):
        before = outline(self.pdf.doc)
        capture = getattr(self.pdf, 'snapshot', self.pdf.bytes)
        state = capture()
        self.pdf.merge([self.appendix()])
        self.assertEqual(len(self.pdf.doc.get_toc()), 3)
        self.pdf.restore(state)
        self.assertEqual((len(self.pdf.doc), outline(self.pdf.doc)), (2, before))
        # A second file that cannot be opened: the app restores the same way.
        state = capture()
        with self.assertRaises(Exception):
            self.pdf.merge([self.appendix(), str(Path(self.tmp.name) / 'missing.pdf')])
        self.pdf.restore(state)
        self.assertEqual((len(self.pdf.doc), outline(self.pdf.doc)), (2, before))


if __name__ == '__main__':
    unittest.main()
