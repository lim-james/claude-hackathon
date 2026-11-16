import tempfile
import shutil
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import fitz
from PIL import Image
from core import PDF, layout_text, page_numbers, images_to_pdf


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


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def document(self, name, pages):
        doc = fitz.open()
        for lines in pages:
            doc.new_page().insert_textbox((50, 50, 545, 790), '\n'.join(lines), fontsize=10)
        doc.save(self.root / name)
        doc.close()
        return str(self.root / name)

    def changed(self, report):
        # What difflib marks as added, removed or changed, e.g. 'add">new text'.
        html = Path(report).read_text(encoding='utf-8').replace('&nbsp;', ' ')
        return sorted(part.split('</span>')[0] for part in html.split('<span class="diff_')[1:])

    def test_layout_text_reads_columns_tables_and_gaps_as_sorted_text_does(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 60), 'Heading across the page', fontsize=14)
        page.insert_textbox((50, 80, 290, 300), '\n'.join(f'left line {i} of the column' for i in range(6)), fontsize=10)
        page.insert_textbox((310, 83, 545, 300), '\n'.join(f'right line {i}' for i in range(8)), fontsize=9)
        for y, row in ((400, ('Name', 'Qty', 'Price')), (421.5, ('Widget', '3', '9.50'))):
            for x, cell in zip((50, 200, 350), row):
                page.insert_text((x, y + x / 200), cell, fontsize=11)
        page.insert_text((300, 600), 'sideways note', rotate=90)
        doc.new_page()  # no text
        doc.new_page().insert_text((72, 700), 'last page')
        for page in doc:
            self.assertEqual(layout_text(page), page.get_text(sort=True))
        rows = [line.split() for line in layout_text(doc[0]).splitlines()]
        self.assertIn(['Widget', '3', '9.50'], rows)  # a table row stays one line
        self.assertIn('left line 0 of the column right line 0'.split(), rows)
        doc.close()

    def test_compare_reports_the_edited_lines_and_only_those(self):
        pages = [[f'page {p} line {n} stays the same' for n in range(30)] for p in range(3)]
        original = self.document('original.pdf', pages)
        pages[0][3] = 'page 0 line 3 was REWRITTEN'
        pages[1].insert(10, 'an ADDED line')
        del pages[2][20]
        edited = self.document('edited.pdf', pages)
        pdf = PDF(original)
        try:
            pdf.compare(edited, self.root / 'diff.html')
            pdf.compare(original, self.root / 'same.html')
        finally:
            pdf.close()
        marked = ' | '.join(self.changed(self.root / 'diff.html'))
        for text in ('stays the same', 'was REWRITTEN', 'an ADDED line', 'page 2 line 20 stays the same'):
            self.assertIn(text, marked)
        self.assertEqual(len(self.changed(self.root / 'diff.html')), 4, marked)
        self.assertEqual(self.changed(self.root / 'same.html'), [])

    def test_compare_costs_less_than_one_sorted_read_of_each_document(self):
        # Compare text read both documents with get_text(sort=True), 16 times the
        # cost of reading the text. Measured against that read in the same
        # process, so the check does not depend on the machine.
        path = self.document('long.pdf', [[f'line {n} ' + 'word ' * 12 for n in range(46)]] * 8)
        pdf = PDF(path)
        try:
            start = time.process_time()
            for page in pdf.doc:
                page.get_text(sort=True)
            sorted_read = time.process_time() - start
            start = time.process_time()
            pdf.compare(path, self.root / 'diff.html')
            compare = time.process_time() - start
        finally:
            pdf.close()
        self.assertLess(compare, sorted_read, f'compare {compare:.2f} s, one sorted read {sorted_read:.2f} s')

    def test_app_compare_runs_on_a_worker_copy_and_opens_the_report_when_done(self):
        from app import App
        pages = [['first line', 'second line']]
        current = PDF(self.document('current.pdf', pages))
        other = self.document('other.pdf', [['first line', 'CHANGED line']])
        output = self.root / 'diff.html'
        started, status = [], []
        app = SimpleNamespace(pdf=current, status=SimpleNamespace(set=status.append),
                              background=lambda work, done, message: started.append((work, done, message)))
        try:
            with patch('app.webbrowser.open') as browser:
                App.compare_in_background(app, other, str(output))
                (work, done, message), = started
                self.assertEqual(message, 'Comparing text…')
                self.assertFalse(output.exists())  # nothing compared on the calling (window) thread
                current.doc[0].insert_text((72, 300), 'typed after the compare started')
                done(work())
                browser.assert_called_once_with(output.resolve().as_uri())
        finally:
            current.close()
        self.assertEqual(self.changed(output), ['add">CHANGED line', 'sub">second line'])
        self.assertNotIn('typed&nbsp;after', output.read_text(encoding='utf-8'))
        self.assertIn('Text comparison opened', status[-1])


if __name__ == '__main__':
    unittest.main()

