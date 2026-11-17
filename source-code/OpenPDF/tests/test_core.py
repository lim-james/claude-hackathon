import itertools
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


class ProtectedFileToolTests(unittest.TestCase):
    # Compare text, Merge and Batch compress given a password-protected file.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paths = [self.make('appendix.pdf', 'Appendix'),
                      self.make('contract.pdf', 'Signed contract', user_pw='hunter2', owner_pw='boss',
                                encryption=fitz.PDF_ENCRYPT_AES_256, permissions=fitz.PDF_PERM_PRINT),
                      self.make('report.pdf', 'Quarterly report')]
        self.contract = self.paths[1]
        self.out = self.root / 'out'
        self.out.mkdir()
        self.targets = [self.out / (Path(p).stem + '-compressed.pdf') for p in self.paths]
        self.pdf = PDF(self.paths[2])
        self.status = []

    def tearDown(self):
        self.pdf.close()
        self.tmp.cleanup()

    def make(self, name, text, **encryption):
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), text)
        doc.save(self.root / name, **encryption)
        doc.close()
        return str(self.root / name)

    def app(self, typed, **parts):
        # The window's methods on a stand-in, with `typed` as what the user
        # enters in the password dialog (None: Cancel).
        from app import App
        app = SimpleNamespace(pdf=self.pdf, require=lambda: None, status=SimpleNamespace(set=self.status.append), **parts)
        app.ask_password = lambda path: App.ask_password(app, path)
        return App, app, patch('app.sd.askstring', return_value=typed)

    def test_merge_with_the_password_adds_the_pages_and_without_it_adds_none(self):
        for passwords in (None, {self.contract: 'wrong'}):
            with self.assertRaises(ValueError):
                self.pdf.merge(self.paths[:2], passwords)
            self.assertEqual(len(self.pdf.doc), 1)  # appendix.pdf was not added either
        self.pdf.merge(self.paths[:2], {self.contract: 'hunter2'})
        self.assertEqual([page.get_text().strip() for page in self.pdf.doc],
                         ['Quarterly report', 'Appendix', 'Signed contract'])

    def test_compare_with_the_password_writes_the_report_and_without_it_writes_nothing(self):
        report = self.root / 'diff.html'
        for password in ('', 'wrong'):
            with self.assertRaises(ValueError):
                self.pdf.compare(self.contract, report, password)
            self.assertFalse(report.exists())
        self.pdf.compare(self.contract, report, 'hunter2')
        self.assertIn('Signed', report.read_text(encoding='utf-8'))

    def test_batch_compress_keeps_the_password_and_restrictions_of_a_protected_file(self):
        written, skipped = PDF.compress_files(self.paths, self.targets, {self.contract: 'hunter2'})
        self.assertEqual((written, skipped), (self.targets, []))
        for target in (self.targets[0], self.targets[2]):
            with fitz.open(target) as copy:
                self.assertFalse(copy.needs_pass)
        with fitz.open(self.targets[1]) as copy:
            self.assertTrue(copy.needs_pass)
            self.assertFalse(copy.authenticate(''))
            self.assertEqual(copy.authenticate('boss'), 4)  # the permissions password still works
            self.assertEqual(copy.authenticate('hunter2'), 2)  # opens, without owner rights
            self.assertEqual(copy[0].get_text().strip(), 'Signed contract')
            everything = PDF.permission_mask(key for key, _label, _flags in PDF.PERMISSIONS)
            self.assertEqual(copy.permissions & everything, fitz.PDF_PERM_PRINT)

    def test_batch_compress_copy_opens_with_exactly_what_opened_the_source(self):
        everything = PDF.permission_mask(key for key, _label, _flags in PDF.PERMISSIONS)

        def opening(path):
            # What each password does to the file: (rights it gives, text it shows).
            result = {}
            for password in ('', 'hunter2', 'boss', 'wrong'):
                with fitz.open(path) as doc:
                    asked = doc.needs_pass
                    rights = doc.authenticate(password)
                    shown = doc[0].get_text().strip() if rights or not asked else None
                    result[password] = (asked, rights, shown, doc.permissions & everything, (doc.metadata or {}).get('encryption'))
            return result

        sources = {'open password only': dict(user_pw='hunter2'),
                   'permissions password only': dict(owner_pw='boss'),
                   'both': dict(user_pw='hunter2', owner_pw='boss')}
        methods = {'aes256': fitz.PDF_ENCRYPT_AES_256, 'aes128': fitz.PDF_ENCRYPT_AES_128, 'rc4': fitz.PDF_ENCRYPT_RC4_128}
        for (kind, passwords), (method, encryption) in itertools.product(sources.items(), methods.items()):
            source = self.make(f'{method} {kind}.pdf', 'Signed contract', encryption=encryption,
                               permissions=fitz.PDF_PERM_PRINT, **passwords)
            before = opening(source)
            self.assertEqual(before['hunter2' if 'user_pw' in passwords else ''][2], 'Signed contract')
            # Every password the prompt accepts; a file that opens without one is not asked about.
            for typed in [p for p in ('hunter2', 'boss') if before[p][1]] if before[''][0] else ['']:
                with self.subTest(source=kind, method=method, typed=typed):
                    target = self.out / f'{method} {kind} {typed}.pdf'
                    self.assertEqual(PDF.compress_files([source], [target], {source: typed}), ([target], []))
                    self.assertEqual(opening(target), before)

    def test_batch_compress_skips_a_file_it_cannot_open_and_compresses_the_rest(self):
        written, skipped = PDF.compress_files(self.paths, self.targets, {self.contract: 'wrong'})
        self.assertEqual((written, skipped), ([self.targets[0], self.targets[2]], [self.contract]))
        # Nothing is left behind for contract.pdf, so a rerun on it alone is not refused.
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['appendix-compressed.pdf', 'report-compressed.pdf'])

    def test_only_a_protected_file_is_asked_about_and_the_prompt_and_refusal_name_it(self):
        App, app, dialog = self.app('hunter2')
        with dialog as ask:
            self.assertEqual(App.ask_password(app, self.paths[0]), '')
            ask.assert_not_called()
            self.assertEqual(App.ask_password(app, self.contract), 'hunter2')
            self.assertEqual(ask.call_args.args[1], 'Password for contract.pdf:')
        for typed in ('wrong', ''):
            App, app, dialog = self.app(typed)
            with dialog, self.assertRaisesRegex(ValueError, 'contract.pdf'):
                App.ask_password(app, self.contract)
        App, app, dialog = self.app(None)
        with dialog:
            self.assertIsNone(App.ask_password(app, self.contract))  # Cancel

    def test_cancel_on_the_password_dialog_stops_merge_and_compare_without_an_error(self):
        # Like Cancel on Open's password dialog: no error box, nothing changed.
        from app import App
        for action in ('merge', 'compare'):
            calls = []
            _, app, dialog = self.app(None, busy=False, inline=None,
                                      change=lambda *args: calls.append(args),
                                      output=lambda ext: calls.append(ext),
                                      compare_in_background=lambda *args: calls.append(args))
            with dialog as ask, patch('app.mb.showerror') as error, \
                    patch('app.fd.askopenfilenames', return_value=tuple(self.paths)), \
                    patch('app.fd.askopenfilename', return_value=self.contract):
                App.guard(app, lambda: getattr(App, action)(app))
                ask.assert_called_once()  # report.pdf after contract.pdf is not asked about either
            error.assert_not_called()
            self.assertEqual((calls, self.status), ([], []), action)
        self.assertEqual(len(self.pdf.doc), 1)

    def test_app_merge_asks_first_and_a_refusal_leaves_no_undo_step(self):
        changes = []
        for typed, steps in ((None, 0), ('wrong', 0), ('hunter2', 1)):
            App, app, dialog = self.app(typed, change=lambda operation: (changes.append(1), operation()))
            with dialog, patch('app.fd.askopenfilenames', return_value=tuple(self.paths[:2])):
                try:
                    App.merge(app)
                except ValueError as refusal:
                    self.assertEqual(typed, 'wrong')
                    self.assertIn('contract.pdf', str(refusal))
            self.assertEqual(len(changes), steps, typed)
        self.assertEqual(len(self.pdf.doc), 3)

    def test_app_compare_asks_before_the_report_is_named_and_passes_the_password_on(self):
        started, named = [], []
        for typed in (None, 'wrong', 'hunter2'):
            App, app, dialog = self.app(typed, output=lambda ext: named.append(ext) or str(self.root / 'diff.html'),
                                        compare_in_background=lambda *args: started.append(args))
            with dialog, patch('app.fd.askopenfilename', return_value=self.contract):
                try:
                    App.compare(app)
                except ValueError as refusal:
                    self.assertEqual(typed, 'wrong')
                    self.assertIn('contract.pdf', str(refusal))
                if typed != 'hunter2':
                    self.assertEqual((named, started), ([], []))
        self.assertEqual(started, [(self.contract, str(self.root / 'diff.html'), 'hunter2')])

    def test_app_batch_asks_before_the_worker_starts_and_names_the_file_it_skipped(self):
        for typed, count, note in ((None, 2, ' Skipped: contract.pdf.'), ('hunter2', 3, '')):
            started = []
            App, app, dialog = self.app(typed, background=lambda work, done, message: started.append((work, done)))
            folder = self.root / f'batch-{count}'
            folder.mkdir()
            with dialog as ask, patch('app.fd.askopenfilenames', return_value=tuple(self.paths)), \
                    patch('app.fd.askdirectory', return_value=str(folder)):
                App.batch(app)
                (work, done), = started
                ask.assert_called_once()  # before the worker; unprotected files are not asked about
                done(work())
                ask.assert_called_once()
            self.assertEqual(len(list(folder.iterdir())), count)
            self.assertEqual(self.status[-1], f'Compressed {count} PDFs. Already optimized files may not shrink.{note}')
        with fitz.open(folder / 'contract-compressed.pdf') as copy:
            self.assertTrue(copy.needs_pass)

    def test_app_batch_skips_a_broken_pdf_and_still_compresses_the_rest(self):
        broken = self.root / 'broken.pdf'
        broken.write_bytes(b'this is not a PDF')
        started = []
        App, app, dialog = self.app('hunter2', background=lambda work, done, message: started.append((work, done)))
        with dialog as ask, patch('app.fd.askopenfilenames', return_value=(self.paths[2], str(broken), self.contract)), \
                patch('app.fd.askdirectory', return_value=str(self.out)):
            App.batch(app)
            (work, done), = started
            done(work())
            ask.assert_called_once()  # contract.pdf, after the broken file
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ['contract-compressed.pdf', 'report-compressed.pdf'])
        self.assertEqual(self.status[-1], 'Compressed 2 PDFs. Already optimized files may not shrink. Skipped: broken.pdf.')


if __name__ == '__main__':
    unittest.main()

