import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
import fitz
from PIL import Image
from core import PDF, OptimizeJob


def photo_bytes(w=1600, h=1200):
    img = Image.new('RGB', (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def scan_pdf(dpi, pages=1):
    """A4 pages that each hold one noisy JPEG, as a scanner makes them."""
    pdf = PDF()
    size = (round(8.27 * dpi), round(11.69 * dpi))
    buf = io.BytesIO()
    Image.effect_noise(size, 40).convert('RGB').save(buf, 'JPEG', quality=80)
    for _ in range(pages):
        page = pdf.doc.new_page()
        page.insert_image(page.rect, stream=buf.getvalue() + b'%d' % page.number)  # distinct image per page
    return pdf


class OptimizeJobTests(unittest.TestCase):
    def test_reports_when_no_image_was_reduced(self):
        # MuPDF only halves: 200 DPI would land below the 120 DPI target, 300 DPI would not.
        for dpi, expected in ((200, 0), (300, 1)):
            pdf = scan_pdf(dpi)
            try:
                before = pdf.doc[0].get_images()
                data, images, reduced = PDF.optimize_data(pdf.doc.tobytes())
                self.assertEqual((images, reduced), (1, expected), f'{dpi} DPI scan')
                with fitz.open('pdf', data) as out:
                    width = out[0].get_images()[0][2]
                self.assertEqual(width < round(8.27 * dpi), bool(expected))
                self.assertEqual(pdf.doc[0].get_images(), before, 'Source document was mutated')
            finally:
                pdf.close()

    def test_job_leaves_the_calling_thread_free(self):
        pdf = scan_pdf(200, pages=4)
        try:
            source = pdf.doc.tobytes()
        finally:
            pdf.close()
        job = OptimizeJob(source, dict(dpi=100, quality=60))
        result = []
        worker = threading.Thread(target=lambda: result.append(job.result()))
        start = last = time.perf_counter()
        worker.start()
        longest = 0
        while worker.is_alive():  # stands in for the window's event loop
            time.sleep(0.005)
            now = time.perf_counter()
            longest, last = max(longest, now - last), now
        data, images, reduced = result[0]
        self.assertEqual((images, reduced), (4, 4))
        self.assertLess(len(data), len(source))
        self.assertLess(longest, (last - start) / 4,
                        f'caller blocked {longest:.2f} s of a {last - start:.2f} s optimization')

    def test_job_reports_failure_and_cancel(self):
        with self.assertRaises(RuntimeError):
            OptimizeJob(b'not a pdf', {}).result()
        pdf = scan_pdf(200, pages=4)
        try:
            job = OptimizeJob(pdf.doc.tobytes(), {})
        finally:
            pdf.close()
        job.cancel()
        self.assertIsNone(job.result())


class OptimizeTests(unittest.TestCase):
    def make_image_pdf(self):
        pdf = PDF()
        page = pdf.doc.new_page(width=600, height=450)
        page.insert_image(fitz.Rect(0, 0, 600, 450), stream=photo_bytes())
        return pdf

    def test_downsampling_reduces_size_without_mutating_source(self):
        pdf = self.make_image_pdf()
        try:
            before = pdf.current_size()
            data = pdf.optimized_bytes(dpi=96, quality=50)
            self.assertLess(len(data), before, 'Optimization did not reduce size')
            # The live document is untouched by producing optimized bytes.
            self.assertEqual(pdf.current_size(), before, 'Source document was mutated')
            reopened = fitz.open('pdf', data)
            self.assertEqual(reopened.page_count, 1)
            reopened.close()
        finally:
            pdf.close()

    def test_optimize_writes_file_and_reports_size(self):
        pdf = self.make_image_pdf()
        try:
            with tempfile.TemporaryDirectory() as folder:
                out = Path(folder) / 'optimized.pdf'
                size = pdf.optimize(out, dpi=96, quality=50)
                self.assertTrue(out.exists())
                self.assertEqual(size, out.stat().st_size)
                with fitz.open(out) as saved:
                    self.assertEqual(saved.page_count, 1)
        finally:
            pdf.close()

    def test_options_can_be_disabled(self):
        pdf = self.make_image_pdf()
        try:
            # Nothing enabled: still valid output, just a plain rewrite.
            data = pdf.optimized_bytes(downsample_images=False, subset_fonts=False, strip_metadata=False)
            self.assertGreater(len(data), 0)
            fitz.open('pdf', data).close()
        finally:
            pdf.close()

    def test_strip_metadata_clears_fields(self):
        pdf = PDF()
        pdf.doc.new_page().insert_text((72, 72), 'hello')
        pdf.doc.set_metadata({'title': 'Secret', 'author': 'Someone'})
        try:
            data = pdf.optimized_bytes(downsample_images=False, subset_fonts=False, strip_metadata=True)
            reopened = fitz.open('pdf', data)
            self.assertFalse(reopened.metadata.get('title'))
            self.assertFalse(reopened.metadata.get('author'))
            reopened.close()
        finally:
            pdf.close()


if __name__ == '__main__':
    unittest.main()
