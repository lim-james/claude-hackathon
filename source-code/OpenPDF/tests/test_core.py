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

    def test_split_keeps_form_fields_of_merged_pages(self):
        form = PDF()
        form.doc.new_page().insert_text((72, 60), 'Application')
        form.add_field(0, fitz.Rect(72, 80, 300, 100), 'name')
        form.add_field(0, fitz.Rect(72, 120, 92, 140), 'agree', 'checkbox')
        form.add_field(0, fitz.Rect(72, 160, 300, 180), 'colour', 'dropdown', ['Red', 'Blue'])
        form.fill_field(0, 'name', 'Typed answer')
        form.fill_field(0, 'agree', 'yes')
        form.fill_field(0, 'colour', 'Blue')
        form.note(0, (320, 80), 'Sticky')
        form.save(self.root / 'form.pdf')
        form.close()
        cover = self.root / 'cover.pdf'
        self.pdf.save(cover)
        expected = [('name', 'Typed answer'), ('agree', 'Yes'), ('colour', 'Blue')]

        def split_pages(pdf, name):
            folder = self.root / name
            folder.mkdir()
            pdf.split(folder)
            pages = []
            for path in sorted(folder.glob('page-*.pdf')):
                with fitz.open(path) as doc:
                    page = doc[0]
                    pages.append(([(w.field_name, w.field_value) for w in page.widgets()],
                                  page.get_text(),
                                  [a.info['content'] for a in page.annots()]))
            return pages

        def check(pages, form_index):
            self.assertEqual(len(pages), 2)
            for index, (fields, text, notes) in enumerate(pages):
                if index == form_index:
                    self.assertEqual(fields, expected)
                    self.assertIn('Application', text)
                    self.assertIn('Typed answer', text)
                    self.assertEqual(notes, ['Sticky'])
                else:
                    self.assertEqual(fields, [])
                    self.assertIn('public text', text)

        merged = PDF(cover)
        merged.merge([self.root / 'form.pdf'])
        merged.save(self.root / 'merged.pdf')
        check(split_pages(merged, 'after-merge'), 1)
        # Split must leave the open document as it was: still a form, same answers.
        self.assertTrue(merged.doc.is_form_pdf)
        self.assertEqual([(name, value) for _i, name, value, _t, _c in merged.fields()], expected)
        check(split_pages(merged, 'second-split'), 1)
        merged.save(self.root / 'after-split.pdf')
        merged.close()
        with fitz.open(self.root / 'after-split.pdf') as saved:
            self.assertTrue(saved.is_form_pdf)

        reopened = PDF(self.root / 'merged.pdf')
        check(split_pages(reopened, 'reopened'), 1)
        reopened.close()

        form_first = PDF(self.root / 'form.pdf')
        form_first.merge([cover])
        check(split_pages(form_first, 'form-first'), 0)
        form_first.close()

        alone = PDF(self.root / 'form.pdf')
        pages = split_pages(alone, 'alone')
        alone.close()
        self.assertEqual([fields for fields, _text, _notes in pages], [expected])

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


if __name__ == '__main__':
    unittest.main()

