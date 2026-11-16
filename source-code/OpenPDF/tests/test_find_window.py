import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
import fitz


class FindWindowTests(unittest.TestCase):
    """The Find window itself; skipped where Tk has no display."""

    def setUp(self):
        from app import App
        try:
            self.app = App()
        except tk.TclError as error:
            self.skipTest(f'no display for Tk: {error}')
        self.errors = []
        self.app.report_callback_exception = lambda *error: self.errors.append(error)
        self.folder = tempfile.TemporaryDirectory()
        self.long, self.short = (str(Path(self.folder.name) / name) for name in ('long.pdf', 'short.pdf'))
        with fitz.open() as doc:
            for _ in range(12):
                doc.new_page().insert_text((72, 72), 'one paragraph on every page')
            doc.save(self.long)
        with fitz.open() as doc:
            doc.new_page().insert_text((72, 72), 'another file without the word')
            doc.save(self.short)

    def tearDown(self):
        self.app.dirty = False
        if self.app.pdf:
            self.app.pdf.close()
        self.app.destroy()
        self.folder.cleanup()

    def pump(self, until, seconds=10):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            self.app.update()
            if until():
                return True
            time.sleep(.002)
        return False

    def test_replacing_the_document_during_a_search_ends_the_searching_label(self):
        app = self.app
        slices = []
        app.search_job.budget, app.search_job.schedule = 0, slices.append  # one page a slice, run by hand
        for replace in (lambda: app.load(self.short), app.new):
            app.dirty = False
            app.load(self.long)
            if not app.search_window:
                app.search()
                label = next(w for w in app.search_window.winfo_children()[0].winfo_children() if isinstance(w, ttk.Label))
            app.find_entry.delete(0, 'end')
            app.find_entry.insert('end', 'paragraph')
            self.assertTrue(self.pump(lambda: slices))
            slices.pop(0)()
            self.assertTrue(app.search_job.running)
            self.assertEqual(label.cget('text'), 'Searching… 1 of 12 pages · 1 found')
            app.dirty = False
            replace()
            while slices:
                slices.pop(0)()
            self.pump(lambda: False, seconds=.3)
            self.assertFalse(app.search_job.running)
            self.assertEqual(app.search_matches, [])
            self.assertEqual(label.cget('text'), 'Type to highlight matching words on the page.')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
