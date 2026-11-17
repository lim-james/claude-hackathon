import os
import stat
import tempfile
import shutil
import unittest
from unittest import mock
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


class SavedFileModeTests(unittest.TestCase):
    """Writes to a user-chosen path keep the old file's mode, or use the umask (#107)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = PDF()
        self.pdf.doc.new_page().insert_text((72, 72), 'Shared report text')

    def tearDown(self):
        self.pdf.close()
        self.tmp.cleanup()

    def writers(self):
        return {'save': self.pdf.save,
                'password export': lambda path: self.pdf.save(path, 'secret'),
                'optimize': self.pdf.optimize}

    def test_writes_round_trip_and_leave_no_temp_files(self):
        for name, write in self.writers().items():
            with self.subTest(name):
                path = self.root / f'{name}.pdf'
                write(path)
                write(path)  # overwrite an existing file too
                with fitz.open(path) as check:
                    if check.needs_pass:
                        check.authenticate('secret')
                    self.assertIn('Shared report text', check[0].get_text())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ['optimize.pdf', 'password export.pdf', 'save.pdf'])

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def with_umask(self, value):
        old = os.umask(value)
        self.addCleanup(os.umask, old)

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes')
    def test_overwrite_keeps_existing_mode(self):
        self.with_umask(0o022)
        for name, write in self.writers().items():
            for mode in (0o644, 0o664, 0o600):
                with self.subTest(name, mode=oct(mode)):
                    path = self.root / f'{name}-{mode:o}.pdf'
                    self.pdf.save(path)
                    os.chmod(path, mode)
                    write(path)
                    self.assertEqual(self.mode(path), mode)

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes')
    def test_new_file_uses_umask_default(self):
        for umask, expected in ((0o022, 0o644), (0o077, 0o600)):
            self.with_umask(umask)
            for name, write in self.writers().items():
                with self.subTest(name, umask=oct(umask)):
                    path = self.root / f'{name}-new-{umask:o}.pdf'
                    write(path)
                    self.assertEqual(self.mode(path), expected)
            folder = self.root / f'split-{umask:o}'
            folder.mkdir()
            self.pdf.split(folder)
            self.assertEqual({self.mode(p) for p in folder.iterdir()}, {expected})

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes')
    def test_save_still_works_when_mode_cannot_be_copied(self):
        self.with_umask(0o022)
        path = self.root / 'theirs.pdf'
        self.pdf.save(path)
        os.chmod(path, 0o664)
        with mock.patch('core.os.fchmod', side_effect=PermissionError):
            self.pdf.save(path)
        self.assertEqual(self.mode(path), 0o644)
        with fitz.open(path) as check:
            self.assertIn('Shared report text', check[0].get_text())

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes')
    def test_save_over_the_open_source_file(self):
        path = self.root / 'source.pdf'
        self.pdf.save(path)
        os.chmod(path, 0o644)
        reopened = PDF(path)
        try:
            reopened.doc.new_page()
            reopened.save(path)
        finally:
            reopened.close()
        self.assertEqual(self.mode(path), 0o644)
        with fitz.open(path) as check:
            self.assertEqual(len(check), 2)


if __name__ == '__main__':
    unittest.main()

