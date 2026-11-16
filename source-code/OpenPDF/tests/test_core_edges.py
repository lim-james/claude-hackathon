"""Error paths and edge inputs of core.PDF and its converters."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import fitz
from PIL import Image
from docx import Document
from core import PDF, images_to_pdf, office_to_pdf


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = PDF()
        self.addCleanup(lambda: self.pdf.close())
        self.pdf.doc.new_page().insert_text((72, 72), 'Secret 123456 and public text')

    def image(self, name='picture.png', size=(200, 100)):
        path = self.root / name
        Image.new('RGB', size, 'red').save(path)
        return path


class OpenAndSaveTests(Workspace):
    def test_opening_an_image_gives_a_one_page_pdf(self):
        opened = PDF(self.image())
        try:
            self.assertTrue(opened.doc.is_pdf)
            self.assertEqual(len(opened.doc), 1)
            self.assertEqual(len(opened.doc[0].get_images()), 1)
            output = self.root / 'from-image.pdf'
            opened.save(output)
        finally:
            opened.close()
        with fitz.open(output) as saved:
            self.assertTrue(saved.is_pdf)
            self.assertEqual(len(saved), 1)

    def test_failed_save_keeps_the_previous_file_and_leaves_no_temporary_file(self):
        target = self.root / 'report.pdf'
        target.write_bytes(b'previous contents')
        with patch('core.os.replace', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.pdf.save(target)
        self.assertEqual(target.read_bytes(), b'previous contents')
        self.assertEqual([p.name for p in self.root.iterdir()], ['report.pdf'])

    def test_optimize_still_writes_when_fonts_cannot_be_subset(self):
        target = self.root / 'small.pdf'
        with patch.object(fitz.Document, 'subset_fonts', side_effect=RuntimeError('unusual font')):
            size = self.pdf.optimize(target)
        self.assertEqual(size, target.stat().st_size)
        with fitz.open(target) as saved:
            self.assertIn('Secret 123456', saved[0].get_text())


class PageEditTests(Workspace):
    def test_replace_text_reports_a_missing_phrase(self):
        with self.assertRaisesRegex(ValueError, 'not found'):
            self.pdf.replace_text(0, 'absent phrase', 'anything')
        self.assertIn('Secret 123456 and public text', self.pdf.doc[0].get_text())

    def test_replace_text_that_does_not_fit_removes_nothing(self):
        with self.assertRaisesRegex(ValueError, 'does not fit'):
            self.pdf.replace_text(0, '123456', 'a much longer replacement than the six digits it stands in for')
        page = self.pdf.doc[0]
        self.assertIn('Secret 123456 and public text', page.get_text())
        self.assertEqual(list(page.annots()), [])

    def test_add_image_places_the_picture_inside_the_chosen_area(self):
        area = fitz.Rect(100, 200, 300, 400)
        self.pdf.add_image(0, area, self.image())
        placed = self.pdf.doc[0].get_image_info()
        self.assertEqual(len(placed), 1)
        box = fitz.Rect(placed[0]['bbox'])
        self.assertTrue(area.contains(box), box)
        # A 2:1 picture keeps its proportions in a square area.
        self.assertAlmostEqual(box.width / box.height, 2, places=2)

    def test_highlight_without_a_match_adds_nothing(self):
        with self.assertRaisesRegex(ValueError, 'No matching text'):
            self.pdf.highlight(0, 'absent phrase')
        self.assertEqual(list(self.pdf.doc[0].annots()), [])


class FormEdgeTests(Workspace):
    def setUp(self):
        super().setUp()
        self.pdf.add_field(0, fitz.Rect(50, 100, 250, 130), 'name', 'text')
        self.pdf.add_field(0, fitz.Rect(50, 300, 250, 360), 'approve', 'signature')

    def values(self):
        return {name: value for _, name, value, _, _ in self.pdf.fields()}

    def test_blank_field_name_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'name is required'):
            self.pdf.add_field(0, fitz.Rect(50, 400, 250, 430), '   ', 'text')
        self.assertEqual(sorted(self.values()), ['approve', 'name'])

    def test_signature_field_cannot_be_typed_into(self):
        with self.assertRaisesRegex(ValueError, 'cannot be filled'):
            self.pdf.fill_field(0, 'approve', 'Jo')
        self.assertFalse(self.values()['approve'])

    def test_filling_an_unknown_field_is_reported(self):
        with self.assertRaisesRegex(ValueError, 'Field not found'):
            self.pdf.fill_field(0, 'surname', 'Lee')
        self.assertEqual(self.values()['name'], '')

    def test_form_data_leaves_signatures_and_unknown_names_alone(self):
        self.pdf.fill_field(0, 'name', 'Jo')
        exported = self.root / 'answers.xfdf'
        self.assertEqual(self.pdf.export_form_data(exported), 1)
        self.assertNotIn('approve', exported.read_text(encoding='utf-8'))
        incoming = self.root / 'incoming.xfdf'
        incoming.write_text(
            '<xfdf><fields>'
            '<field name="name"><value>Sam</value></field>'
            '<field name="approve"><value>forged</value></field>'
            '<field name="surname"><value>Lee</value></field>'
            '</fields></xfdf>', encoding='utf-8')
        self.assertEqual(self.pdf.import_form_data(incoming), 1)
        values = self.values()
        self.assertEqual(values['name'], 'Sam')
        self.assertFalse(values['approve'])
        self.assertNotIn('surname', values)


class ExportTests(Workspace):
    def test_plain_word_export_breaks_between_pages_and_skips_pictures(self):
        self.pdf.add_image(0, fitz.Rect(72, 200, 172, 250), self.image())
        self.pdf.doc.new_page().insert_text((72, 72), 'Second page text')
        self.pdf.doc.new_page().insert_text((72, 72), 'Third page text')
        output = self.root / 'plain.docx'
        self.pdf.export_docx_text(output)
        document = Document(output)
        text = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        self.assertEqual(text, ['Secret 123456 and public text', 'Second page text', 'Third page text'])
        self.assertEqual(document.element.xml.count('w:type="page"'), 2)
        self.assertEqual(len(document.inline_shapes), 0)

    def test_images_to_pdf_needs_an_image(self):
        output = self.root / 'empty.pdf'
        with self.assertRaisesRegex(ValueError, 'at least one image'):
            images_to_pdf([], output)
        self.assertFalse(output.exists())


def recognised_pdf(text):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class OcrTests(Workspace):
    """Tesseract is replaced by a stand-in, so these run where it is not installed."""

    def setUp(self):
        super().setUp()
        self.pdf.doc.new_page().insert_text((72, 72), 'Scanned page two')
        self.pdf.doc.new_page().insert_text((72, 72), 'Typed page three')
        # No bundled tesseract folder here, so the PATH lookup decides.
        bundle = patch.object(sys, '_MEIPASS', str(self.root), create=True)
        bundle.start()
        self.addCleanup(bundle.stop)
        self.calls = []

    def page_text(self):
        return [page.get_text().strip() for page in self.pdf.doc]

    def tesseract(self, returncode=0, stderr=b''):
        def run(command, **options):
            self.calls.append(command)
            if not returncode:
                self.assertTrue(Path(command[1]).exists(), 'the page image is written before OCR runs')
                Path(command[2] + '.pdf').write_bytes(recognised_pdf('RECOGNISED TEXT'))
            return subprocess.CompletedProcess(command, returncode, b'', stderr)
        return run

    def run_ocr(self, indices, language='eng', **outcome):
        with patch('core.shutil.which', return_value='tesseract'), \
                patch('core.subprocess.run', side_effect=self.tesseract(**outcome)):
            self.pdf.ocr(indices, language)

    def test_missing_tesseract_is_explained_and_changes_nothing(self):
        before = self.page_text()
        with patch('core.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Tesseract is missing'):
                self.pdf.ocr([0])
        self.assertEqual(self.page_text(), before)

    def test_only_the_chosen_pages_are_replaced_by_ocr_output(self):
        self.run_ocr([1], 'deu')
        self.assertEqual(self.page_text(), ['Secret 123456 and public text', 'RECOGNISED TEXT', 'Typed page three'])
        self.assertEqual(len(self.calls), 1)
        command = self.calls[0]
        self.assertEqual(command[0], 'tesseract')
        self.assertEqual(command[3:], ['-l', 'deu', 'pdf'])

    def test_failed_ocr_reports_tesseract_and_keeps_every_page(self):
        before = self.page_text()
        with self.assertRaisesRegex(ValueError, 'language pack not installed'):
            self.run_ocr([0, 1], returncode=1, stderr=b'language pack not installed')
        self.assertEqual(self.page_text(), before)

    def test_failed_ocr_without_a_message_still_says_so(self):
        with self.assertRaisesRegex(ValueError, 'OCR failed'):
            self.run_ocr([2], returncode=1)
        self.assertEqual(len(self.pdf.doc), 3)

    def test_bundled_tesseract_is_preferred_and_given_its_language_data(self):
        bundle = self.root / 'tesseract'
        bundle.mkdir()
        (bundle / 'tesseract.exe').write_bytes(b'')
        seen = {}

        def run(command, **options):
            seen['env'] = options['env']
            return self.tesseract()(command)
        with patch('core.shutil.which', return_value=None), patch('core.subprocess.run', side_effect=run):
            self.pdf.ocr([0])
        self.assertEqual(self.calls[0][0], str(bundle / 'tesseract.exe'))
        self.assertEqual(seen['env']['TESSDATA_PREFIX'], str(bundle / 'tessdata'))
        self.assertEqual(self.page_text()[0], 'RECOGNISED TEXT')


class OfficeConversionTests(Workspace):
    def setUp(self):
        super().setUp()
        self.source = self.root / 'letter.docx'
        self.source.write_bytes(b'not really a document')
        self.out = self.root / 'converted'
        self.out.mkdir()
        self.calls = []

    def soffice(self, returncode=0, write=True, stderr='', stdout=''):
        def run(command, **options):
            self.calls.append(command)
            if write:
                (self.out / 'letter.pdf').write_bytes(recognised_pdf('Converted'))
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)
        return run

    def convert(self, which='soffice', **outcome):
        with patch('core.shutil.which', return_value=which), \
                patch.dict(os.environ, {'PROGRAMFILES': str(self.root / 'programs')}), \
                patch('core.subprocess.run', side_effect=self.soffice(**outcome)):
            return office_to_pdf(self.source, self.out)

    def test_missing_libreoffice_is_explained(self):
        with self.assertRaisesRegex(ValueError, 'requires LibreOffice'):
            self.convert(which=None)
        self.assertEqual(self.calls, [])

    def test_conversion_returns_the_pdf_written_beside_the_chosen_folder(self):
        output = self.convert()
        self.assertEqual(output, self.out / 'letter.pdf')
        with fitz.open(output) as converted:
            self.assertEqual(converted[0].get_text().strip(), 'Converted')
        command = self.calls[0]
        self.assertEqual(command[0], 'soffice')
        self.assertIn('--headless', command)
        self.assertEqual(command[-5:], ['--convert-to', 'pdf', '--outdir', str(self.out), str(self.source)])

    def test_libreoffice_in_program_files_is_used_when_not_on_path(self):
        installed = self.root / 'programs' / 'LibreOffice' / 'program' / 'soffice.exe'
        installed.parent.mkdir(parents=True)
        installed.write_bytes(b'')
        self.assertEqual(self.convert(which=None), self.out / 'letter.pdf')
        self.assertEqual(self.calls[0][0], str(installed))

    def test_failed_conversion_reports_libreoffice_output(self):
        with self.assertRaisesRegex(ValueError, 'source file could not be loaded'):
            self.convert(returncode=1, stderr='source file could not be loaded')

    def test_conversion_that_writes_no_pdf_is_a_failure(self):
        with self.assertRaisesRegex(ValueError, 'Conversion failed'):
            self.convert(write=False)


if __name__ == '__main__':
    unittest.main()
