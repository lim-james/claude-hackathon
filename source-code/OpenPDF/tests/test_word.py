import importlib.util
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from docx import Document
from core import PDF


@unittest.skipUnless(importlib.util.find_spec('pdf2docx'), 'pdf2docx required; exercised by Windows CI')
class WordTests(unittest.TestCase):
    def test_editable_styles_images_and_table(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = PDF()
            p = pdf.doc.new_page()
            p.insert_text((50, 60), 'Editable heading', fontsize=22, fontname='hebo', color=(0.2, 0.3, 0.7))
            p.insert_text((50, 90), 'Body text remains editable.', fontsize=12)
            for x in (50, 200, 350):
                p.draw_line((x, 150), (x, 230), color=(0, 0, 0))
            for y in (150, 190, 230):
                p.draw_line((50, y), (350, y), color=(0, 0, 0))
            for x, y, text in [(60, 175, 'Item'), (210, 175, 'Count'), (60, 215, 'Apples'), (210, 215, '3')]:
                p.insert_text((x, y), text, fontsize=12)
            image = io.BytesIO()
            Image.new('RGB', (80, 60), 'red').save(image, format='PNG')
            p.insert_image((50, 280, 130, 340), stream=image.getvalue())
            output = Path(folder) / 'layout.docx'
            try:
                pdf.export_docx(output)
            finally:
                pdf.close()
            doc = Document(output)
            runs = [r for paragraph in doc.paragraphs for r in paragraph.runs]
            self.assertTrue(any('Editable heading' in r.text for r in runs))
            self.assertTrue(any(r.font.size and r.font.size.pt >= 20 for r in runs))
            self.assertGreaterEqual(len(doc.tables), 1)
            self.assertIn('Apples', '\n'.join(c.text for t in doc.tables for row in t.rows for c in row.cells))
            self.assertGreaterEqual(len(doc.inline_shapes), 1)

    def test_scan_requires_ocr(self):
        pdf = PDF()
        pdf.doc.new_page()
        try:
            with self.assertRaisesRegex(ValueError, 'OCR'):
                pdf.export_docx('should-not-exist.docx')
        finally:
            pdf.close()

    @unittest.skipIf(os.name == 'nt', 'POSIX file modes')
    def test_export_keeps_mode_and_uses_umask(self):
        # Word export writes the chosen file directly; guard it alongside #107.
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)
        with tempfile.TemporaryDirectory() as folder:
            pdf = PDF()
            pdf.doc.new_page().insert_text((50, 60), 'Shared notes')
            try:
                path = Path(folder) / 'notes.docx'
                pdf.export_docx(path)
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)
                os.chmod(path, 0o664)
                pdf.export_docx(path)
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o664)
            finally:
                pdf.close()
