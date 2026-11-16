import faulthandler
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


WAIT = 60    # seconds: no wait below is longer, so a stuck worker fails the test instead of holding the run
SLOW = 24     # pages that keep the worker busy for seconds
WORKING = 2   # seconds after its start by which the worker is at work on them
PROMPT = 20   # seconds within which result() must return once its worker is cancelled, killed or done


class OptimizeJobTests(unittest.TestCase):
    def setUp(self):
        # Last resort for a wait nobody bounded: print every thread's stack and end the run.
        faulthandler.dump_traceback_later(3 * WAIT, exit=True)
        self.addCleanup(faulthandler.cancel_dump_traceback_later)

    def finish(self, job, when=None):
        """job.result() on a thread, as the app calls it; when() runs on this thread meanwhile."""
        outcome = []
        def run():
            try:
                outcome.append(job.result(WAIT))
            except Exception as exc:
                outcome.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        if when:
            when()
        thread.join(PROMPT)
        self.assertFalse(thread.is_alive(), f'result() did not return within {PROMPT} s')
        self.assertFalse(job.started and job.process.is_alive(), 'the worker was left running')
        return outcome[0]

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
        worker = threading.Thread(target=lambda: result.append(job.result(WAIT)), daemon=True)
        start = last = time.perf_counter()
        worker.start()
        longest = 0
        while worker.is_alive() and last - start < 2 * WAIT:  # stands in for the window's event loop
            time.sleep(0.005)
            now = time.perf_counter()
            longest, last = max(longest, now - last), now
        self.assertTrue(result, 'result() did not return')
        data, images, reduced = result[0]
        self.assertEqual((images, reduced), (4, 4))
        self.assertLess(len(data), len(source))
        self.assertLess(longest, (last - start) / 4,
                        f'caller blocked {longest:.2f} s of a {last - start:.2f} s optimization')

    def source(self, pages=4):
        pdf = scan_pdf(200, pages=pages)
        try:
            return pdf.doc.tobytes()
        finally:
            pdf.close()

    def test_job_reports_failure(self):
        self.assertIsInstance(self.finish(OptimizeJob(b'not a pdf', {})), RuntimeError)

    # Cancel, or the worker killed, at each point of a job. Every one must end result() promptly.

    def after_start(self, job, seconds, act):
        def when():
            deadline = time.monotonic() + WAIT
            while not job.started and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(seconds)
            act()
        return when

    def assert_stopped(self, outcome):
        self.assertIsInstance(outcome, RuntimeError)
        self.assertIn('stopped unexpectedly', str(outcome))

    def assert_cancelled(self, outcome):  # not assertIsNone, which would print a whole document
        self.assertTrue(outcome is None, f'expected None, got {type(outcome).__name__}')

    def test_cancel_before_the_worker_starts(self):
        job = OptimizeJob(self.source(), {})
        job.cancel()
        self.assert_cancelled(self.finish(job))

    def test_cancel_while_the_worker_starts(self):
        # Estimate size, then Cancel straight away: the worker has not read the document yet.
        job = OptimizeJob(self.source(), {})
        self.assert_cancelled(self.finish(job, job.cancel))

    def test_worker_killed_while_it_starts(self):
        job = OptimizeJob(self.source(), dict(dpi=100))
        self.assert_stopped(self.finish(job, self.after_start(job, 0, lambda: job.process.kill())))

    def test_cancel_while_the_worker_runs(self):
        job = OptimizeJob(self.source(SLOW), dict(dpi=100))
        self.assert_cancelled(self.finish(job, self.after_start(job, WORKING, job.cancel)))

    def test_worker_killed_while_it_runs(self):
        job = OptimizeJob(self.source(SLOW), dict(dpi=100))
        self.assert_stopped(self.finish(job, self.after_start(job, WORKING, lambda: job.process.kill())))

    def sending(self, act):
        """A job stopped as its result starts to arrive: act() runs then, with the worker
        still writing, because the result is larger than a pipe holds and nobody reads it yet."""
        source, options = self.source(), dict(downsample_images=False, subset_fonts=False)
        self.assertGreater(len(PDF.optimize_data(source, **options)[0]), 1 << 20)
        job = OptimizeJob(source, options)
        receive = job.conn.recv
        def recv():
            time.sleep(0.02)
            self.assertTrue(job.process.is_alive(), 'the worker finished without its result being read')
            act(job)
            job.process.join(WAIT)
            return receive()
        job.conn.recv = recv
        return job

    def test_cancel_while_the_result_is_sent(self):
        self.assert_cancelled(self.finish(self.sending(lambda job: job.cancel())))

    def test_worker_killed_while_the_result_is_sent(self):
        self.assert_stopped(self.finish(self.sending(lambda job: job.process.kill())))

    def arrived(self, act):
        """A job whose whole result has been received: act() runs then, before result() returns."""
        job = OptimizeJob(self.source(), dict(downsample_images=False, subset_fonts=False))
        receive = job.conn.recv
        def recv():
            value = receive()
            act(job)
            return value
        job.conn.recv = recv
        return job

    def test_cancel_after_the_result_arrived(self):
        job = self.arrived(lambda job: job.cancel())
        self.assert_cancelled(self.finish(job))
        job.cancel()  # and again once result() has returned: nothing left to stop

    def test_worker_killed_after_the_result_arrived(self):
        data, images, reduced = self.finish(self.arrived(lambda job: job.process.kill()))
        self.assertEqual(images, 0)  # counted only when downsampling
        with fitz.open('pdf', data) as out:
            self.assertEqual(len(out), 4)

    def test_job_gives_up_after_its_timeout(self):
        job = OptimizeJob(self.source(), dict(dpi=100))
        with self.assertRaisesRegex(RuntimeError, 'did not finish'):
            job.result(0.01)
        self.assertFalse(job.process.is_alive())


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
