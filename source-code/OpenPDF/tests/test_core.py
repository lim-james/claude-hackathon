import tempfile
import shutil
import unittest
from pathlib import Path
import fitz
from PIL import Image
from core import PDF, page_numbers, images_to_pdf


class PDFTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = PDF()
        self.pdf.doc.new_page().insert_text((72, 72), 'Secret 123456 and public text')

    def tearDown(self):
        self.pdf.close()
        self.tmp.cleanup()

    def test_page_ranges(self):
        self.assertEqual(page_numbers('3,1-2,3-2', 3), [2, 0, 1, 2, 1])
        for spec in ('', '0', '4', '-1'):
            with self.assertRaises(ValueError):
                page_numbers(spec, 3)

    def test_merge_select_split(self):
        source = self.root / 'source.pdf'
        self.pdf.save(source)
        self.pdf.merge([source])
        self.pdf.select('2,1,1')
        self.assertEqual(len(self.pdf.doc), 3)
        self.pdf.split(self.root)
        self.assertEqual(len(list(self.root.glob('page-*.pdf'))), 3)

    def test_redaction_removes_saved_text(self):
        rect = self.pdf.doc[0].search_for('123456')[0]
        self.pdf.redact(0, rect)
        output = self.root / 'redacted.pdf'
        self.pdf.save(output)
        with fitz.open(output) as reopened:
            self.assertNotIn('123456', reopened[0].get_text())
            self.assertIn('public', reopened[0].get_text())
            for xref in range(1, reopened.xref_length()):
                stream = reopened.xref_stream(xref)
                if stream:
                    self.assertNotIn(b'123456', stream)

    def test_password(self):
        output = self.root / 'encrypted.pdf'
        self.pdf.save(output, 'test-password')
        with self.assertRaises(ValueError):
            PDF(output, 'wrong')
        unlocked = PDF(output, 'test-password')
        self.assertIn('public text', unlocked.doc[0].get_text())
        unlocked.close()

    def test_forms_persist(self):
        self.pdf.add_field(0, fitz.Rect(50, 100, 250, 130), 'Name')
        self.pdf.add_field(0, fitz.Rect(50, 150, 70, 170), 'Consent', 'checkbox')
        self.pdf.fill_field(0, 'Name', 'Rishabh')
        self.pdf.fill_field(0, 'Consent', 'yes')
        output = self.root / 'form.pdf'
        self.pdf.save(output)
        other = PDF(output)
        fields = {name: value for _, name, value, *_ in other.fields()}
        self.assertEqual(fields['Name'], 'Rishabh')
        self.assertNotEqual(fields['Consent'], 'Off')
        other.close()

    def test_text_overflow_is_atomic(self):
        before = self.pdf.doc[0].get_text()
        with self.assertRaises(ValueError):
            self.pdf.insert_text(0, fitz.Rect(1, 1, 2, 2), 'too long', 20)
        self.assertEqual(self.pdf.doc[0].get_text(), before)

    def test_text_replace(self):
        self.pdf.replace_text(0, '123456', 'OK', 8)
        self.assertNotIn('123456', self.pdf.doc[0].get_text())
        self.assertIn('OK', self.pdf.doc[0].get_text())

    def test_exports_and_annotations(self):
        self.pdf.highlight(0, 'public')
        self.pdf.watermark('DRAFT')
        self.pdf.number_pages()
        self.pdf.export_docx_text(self.root / 'export.docx')
        self.pdf.export_text(self.root / 'export.txt')
        self.pdf.export_images(self.root)
        self.assertIn('public', (self.root / 'export.txt').read_text())
        self.assertTrue((self.root / 'export.docx').exists())
        self.assertTrue((self.root / 'page-0001.png').exists())
        self.assertIsNotNone(self.pdf.doc[0].first_annot)

    def test_page_numbers_stay_bottom_centre_on_rotated_pages(self):
        # Issue #35: the number was drawn sideways in the body of a rotated page.
        from PIL import ImageChops

        def ink(page):  # (offset from the page centre, distance to the bottom, width, height)
            pix = page.get_pixmap(dpi=72, alpha=False)
            image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            box = ImageChops.difference(image, Image.new('RGB', image.size, (255, 255, 255))).getbbox()
            self.assertIsNotNone(box, f'no page number on a page rotated {page.rotation}')
            return ((box[0] + box[2]) / 2 - pix.width / 2, pix.height - box[3],
                    box[2] - box[0], box[3] - box[1])

        pdf = PDF()
        try:
            for rotation in (0, 90, 180, 270):
                pdf.doc.new_page().set_rotation(rotation)
            pdf.number_pages()
            pdf.restore(pdf.bytes())
            # "1 / 4" .. "4 / 4" differ in their first digit, so compare each page with
            # the same label on an unrotated page.
            for index, rotation in enumerate((0, 90, 180, 270)):
                plain = PDF()
                try:
                    for _ in range(4):
                        plain.doc.new_page()
                    plain.number_pages()
                    want = ink(plain.doc[index])
                finally:
                    plain.close()
                got = ink(pdf.doc[index])
                self.assertAlmostEqual(got[0], want[0], delta=2, msg=f'{rotation}: not centred')
                self.assertAlmostEqual(got[1], 19, delta=2, msg=f'{rotation}: bottom')
                self.assertAlmostEqual(got[2], want[2], delta=1, msg=f'{rotation}: width')
                self.assertAlmostEqual(got[3], want[3], delta=1, msg=f'{rotation}: height')
        finally:
            pdf.close()

    def test_image_pdf_and_comparison(self):
        image = self.root / 'image.png'
        Image.new('RGB', (200, 100), 'red').save(image)
        images_to_pdf([image], self.root / 'image.pdf')
        self.pdf.compare(self.root / 'image.pdf', self.root / 'compare.html')
        self.assertIn('public', (self.root / 'compare.html').read_text())
        with fitz.open(self.root / 'image.pdf') as doc:
            self.assertEqual(len(doc[0].get_images()), 1)

    def test_sanitize(self):
        self.pdf.doc.set_metadata({'author': 'Private'})
        self.pdf.doc.embfile_add('secret.txt', b'secret')
        self.pdf.sanitize()
        self.assertFalse(self.pdf.doc.metadata['author'])
        self.assertEqual(self.pdf.doc.embfile_count(), 0)

    @unittest.skipUnless(shutil.which('tesseract'), 'Tesseract not on PATH')
    def test_ocr_searchable_output(self):
        self.pdf.doc[0].insert_text((72, 150), 'SEARCHABLE DOCUMENT', fontsize=28)
        self.pdf.ocr([0])
        self.assertIn('SEARCHABLE', self.pdf.doc[0].get_text())
        output = self.root / 'ocr.pdf'
        self.pdf.save(output)
        with fitz.open(output) as reopened:
            self.assertTrue(reopened[0].search_for('SEARCHABLE'))

    def test_rotated_selection_coordinates(self):
        page = self.pdf.doc[0]
        target = page.search_for('123456')[0]
        page.set_rotation(90)
        displayed = target * page.rotation_matrix
        actual = displayed * page.derotation_matrix
        self.pdf.redact(0, actual)
        self.assertNotIn('123456', self.pdf.doc[0].get_text())


class DeletePageBookmarkTests(unittest.TestCase):
    """Delete page and Extract / reorder keep only bookmarks that still open something."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        doc = fitz.open()
        for number in range(1, 5):
            doc.new_page().insert_text((72, 72), f'Chapter {number}', fontsize=20)
        doc.set_toc([
            [1, 'Contents', -1],
            [1, 'Intro', 1, {'kind': fitz.LINK_GOTO, 'page': 0, 'to': fitz.Point(72, 500), 'zoom': 2.0}],
            [2, 'Intro detail', 2],
            [1, 'Method', 2],
            [2, 'Method detail', 2],
            [2, 'Method results', 3],
            [1, 'Results', 3, {'kind': fitz.LINK_GOTO, 'page': 2, 'to': fitz.Point(72, 400), 'zoom': 3.0,
                               'color': (1, 0, 0), 'bold': True}],
            [1, 'Site', -1, {'kind': fitz.LINK_URI, 'uri': 'https://example.com'}],
            [1, 'End', 4],
        ])
        self.path = self.root / 'four.pdf'
        doc.save(self.path)
        doc.close()
        self.pdf = PDF(str(self.path))

    def tearDown(self):
        self.pdf.close()
        self.tmp.cleanup()

    @staticmethod
    def outline(doc):
        """(level, title, page, what it opens and how it looks) per bookmark."""
        keep = ('kind', 'to', 'zoom', 'uri', 'color', 'bold')
        return [(level, title, page, {k: v for k, v in info.items() if k in keep})
                for level, title, page, info in doc.get_toc(simple=False)]

    def reopened(self):
        out = self.root / 'out.pdf'
        self.pdf.save(out)
        with fitz.open(out) as doc:
            return self.outline(doc), self.links_are_consistent(doc)

    @staticmethod
    def links_are_consistent(doc):
        """Every /First, /Last, /Prev, /Next, /Parent and /Count agrees with the others."""
        def ref(xref, key):
            kind, value = doc.xref_get_key(xref, key)
            return int(value.split()[0]) if kind == 'xref' else 0

        def walk(parent):
            children, child = [], ref(parent, 'First')
            while child:
                children.append(child)
                child = ref(child, 'Next')
            if not children:
                return (ref(parent, 'Last') == 0, 0)
            ok = (ref(parent, 'Last') == children[-1] and ref(children[0], 'Prev') == 0
                  and all(ref(c, 'Parent') == parent for c in children)
                  and all(ref(b, 'Prev') == a for a, b in zip(children, children[1:])))
            shown = 0
            for c in children:
                c_ok, below = walk(c)
                kind, value = doc.xref_get_key(c, 'Count')
                count = int(value) if kind == 'int' else 0
                ok = ok and c_ok and abs(count) == below
                shown += 1 + (below if count > 0 else 0)
            return ok, shown

        root = ref(doc.pdf_catalog(), 'Outlines')
        if not root:
            return True
        ok, shown = walk(root)
        return ok and doc.xref_get_key(root, 'Count')[1] == str(shown)

    def titles(self, outline):
        return [(level, title, page) for level, title, page, _ in outline]

    def test_issue_file_loses_the_deleted_pages_bookmark(self):
        doc = fitz.open()
        for number in range(3):
            doc.new_page()
        doc.set_toc([[1, 'Intro', 1], [1, 'Method', 2], [1, 'Results', 3]])
        doc.save(self.root / 'three.pdf')
        doc.close()
        self.pdf.close()
        self.pdf = PDF(str(self.root / 'three.pdf'))
        self.pdf.delete_page(1)
        self.assertEqual(self.pdf.doc.get_toc(), [[1, 'Intro', 1], [1, 'Results', 2]])
        outline, consistent = self.reopened()
        self.assertEqual(self.titles(outline), [(1, 'Intro', 1), (1, 'Results', 2)])
        self.assertTrue(consistent)

    def test_delete_keeps_other_bookmarks_and_their_children(self):
        before = self.outline(self.pdf.doc)
        self.pdf.delete_page(1)
        outline, consistent = self.reopened()
        self.assertTrue(consistent)
        # Method goes; its child on a kept page takes its place, a child on the
        # deleted page goes with it, a child of Intro on the deleted page goes.
        self.assertEqual(self.titles(outline), [
            (1, 'Contents', -1), (1, 'Intro', 1), (1, 'Method results', 2),
            (1, 'Results', 2), (1, 'Site', -1), (1, 'End', 3)])
        by_title = {entry[1]: entry[3] for entry in before}
        for level, title, page, info in outline:
            self.assertEqual(info, by_title[title], title)  # position, zoom, colour, bold, web link

    def test_delete_several_pages_in_a_row(self):
        for index in (1, 0, 1):
            self.pdf.delete_page(index)
        self.assertEqual(len(self.pdf.doc), 1)
        outline, consistent = self.reopened()
        self.assertTrue(consistent)
        self.assertEqual(self.titles(outline), [
            (1, 'Contents', -1), (1, 'Method results', 1), (1, 'Results', 1), (1, 'Site', -1)])

    def test_deleting_the_only_bookmarked_page_empties_the_outline(self):
        doc = fitz.open()
        doc.new_page()
        doc.new_page()
        doc.set_toc([[1, 'Only', 2]])
        doc.save(self.root / 'two.pdf')
        doc.close()
        self.pdf.close()
        self.pdf = PDF(str(self.root / 'two.pdf'))
        self.pdf.delete_page(1)
        outline, consistent = self.reopened()
        self.assertEqual(outline, [])
        self.assertTrue(consistent)

    def test_grandchild_on_a_kept_page_survives(self):
        doc = fitz.open()
        for number in range(3):
            doc.new_page()
        doc.set_toc([[1, 'A', 2], [2, 'B', 2], [3, 'C', 3], [3, 'C2', 2], [1, 'Z', 1]])
        doc.save(self.root / 'nested.pdf')
        doc.close()
        self.pdf.close()
        self.pdf = PDF(str(self.root / 'nested.pdf'))
        self.pdf.delete_page(1)
        outline, consistent = self.reopened()
        self.assertEqual(self.titles(outline), [(1, 'C', 2), (1, 'Z', 1)])
        self.assertTrue(consistent)

    def test_named_destination_to_the_deleted_page(self):
        doc = self.pdf.doc
        doc.set_toc([[1, 'Intro', 1]])
        names = doc.get_new_xref()
        doc.update_object(names, f'<</Names[(method) [{doc.page_xref(1)} 0 R /XYZ 0 0 0]]>>')
        doc.xref_set_key(doc.pdf_catalog(), 'Names', f'<</Dests {names} 0 R>>')
        intro = doc.get_toc(simple=False)[0][3]['xref']
        named = doc.get_new_xref()
        root = int(doc.xref_get_key(doc.pdf_catalog(), 'Outlines')[1].split()[0])
        doc.update_object(named, f'<</Title(Method)/Parent {root} 0 R/Prev {intro} 0 R/Dest(method)>>')
        doc.xref_set_key(intro, 'Next', f'{named} 0 R')
        doc.xref_set_key(root, 'Last', f'{named} 0 R')
        doc.xref_set_key(root, 'Count', '2')
        self.assertEqual(doc.get_toc(), [[1, 'Intro', 1], [1, 'Method', 2]])
        self.pdf.delete_page(1)
        outline, consistent = self.reopened()
        self.assertEqual(self.titles(outline), [(1, 'Intro', 1)])
        self.assertTrue(consistent)

    def test_undo_and_redo_of_a_delete(self):
        before = self.outline(self.pdf.doc)
        step = self.pdf.snapshot()
        self.pdf.delete_page(1)
        after = self.outline(self.pdf.doc)
        redo = self.pdf.snapshot()
        self.pdf.restore(step)
        self.assertEqual(len(self.pdf.doc), 4)
        self.assertEqual(self.outline(self.pdf.doc), before)
        self.pdf.restore(redo)
        self.assertEqual(self.outline(self.pdf.doc), after)
        self.pdf.delete_page(0)  # an edit after redo starts from the redone outline
        self.assertNotIn('Intro', [entry[1] for entry in self.outline(self.pdf.doc)])

    def test_extract_and_reorder(self):
        self.pdf.select('4,3,1')
        outline, consistent = self.reopened()
        self.assertTrue(consistent)
        self.assertEqual(self.titles(outline), [
            (1, 'Contents', -1), (1, 'Intro', 3), (1, 'Method results', 2),
            (1, 'Results', 2), (1, 'Site', -1), (1, 'End', 1)])
        self.pdf.close()
        self.pdf = PDF(str(self.path))
        self.pdf.select('4,3,2,1')  # reorder only: every bookmark stays where it was
        self.assertEqual(self.titles(self.outline(self.pdf.doc)), [
            (1, 'Contents', -1), (1, 'Intro', 4), (2, 'Intro detail', 3), (1, 'Method', 3),
            (2, 'Method detail', 3), (2, 'Method results', 2), (1, 'Results', 2), (1, 'Site', -1), (1, 'End', 1)])

    def test_split_after_delete(self):
        self.pdf.delete_page(1)
        folder = self.root / 'split'
        folder.mkdir()
        self.pdf.split(folder)
        files = sorted(folder.glob('page-*.pdf'))
        self.assertEqual(len(files), 3)
        with fitz.open(files[1]) as doc:
            self.assertIn('Chapter 3', doc[0].get_text())


if __name__ == '__main__':
    unittest.main()
