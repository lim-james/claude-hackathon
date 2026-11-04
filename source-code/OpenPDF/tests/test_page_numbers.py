import unittest
import fitz
from core import PDF


class PageNumberTests(unittest.TestCase):
    def test_numbers_stay_upright_at_visible_footer_after_rotation(self):
        pdf = PDF()
        self.addCleanup(pdf.close)
        for rotation in (0, 90, 180, 270):
            page = pdf.doc.new_page(width=600, height=800)
            page.set_cropbox(fitz.Rect(50, 70, 550, 750))
            page.set_rotation(rotation)

        pdf.number_pages()
        pdf.restore(pdf.bytes())

        for index, page in enumerate(pdf.doc):
            with self.subTest(rotation=page.rotation):
                lines = [line for block in page.get_text('dict')['blocks']
                         for line in block.get('lines', [])]
                self.assertEqual(len(lines), 1)
                line = lines[0]
                self.assertEqual(''.join(s['text'] for s in line['spans']), f'{index + 1} / 4')
                direction = fitz.Point(line['dir']) * fitz.Matrix(page.rotation)
                self.assertAlmostEqual(direction.x, 1, places=4)
                self.assertAlmostEqual(direction.y, 0, places=4)
                origin = fitz.Point(line['spans'][0]['origin']) * page.rotation_matrix
                self.assertAlmostEqual(origin.x, page.rect.width / 2, delta=.1)
                self.assertAlmostEqual(origin.y, page.rect.height - 20, delta=.1)
                self.assertTrue(page.rect.contains(fitz.Rect(line['bbox']) * page.rotation_matrix))
