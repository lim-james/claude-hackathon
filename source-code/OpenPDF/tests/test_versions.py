import errno
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import fitz
import versions
from core import PDF


def make_pdf(pages=1, text='v1'):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class VersionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['OPENPDF_HISTORY_DIR'] = str(Path(self.tmp.name) / 'history')
        self.doc_path = str(Path(self.tmp.name) / 'report.pdf')
        Path(self.doc_path).write_bytes(make_pdf())

    def tearDown(self):
        os.environ.pop('OPENPDF_HISTORY_DIR', None)
        self.tmp.cleanup()

    def test_save_list_read_round_trip(self):
        self.assertEqual(versions.list_versions(self.doc_path), [])
        data = make_pdf(pages=2, text='snapshot')
        record = versions.save_version(self.doc_path, data, 'First draft')
        self.assertEqual(record['label'], 'First draft')
        self.assertEqual(record['pages'], 2)
        listed = versions.list_versions(self.doc_path)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['id'], record['id'])
        self.assertEqual(versions.read_version(self.doc_path, record['id']), data)

    def test_versions_are_sorted_newest_first(self):
        a = versions.save_version(self.doc_path, make_pdf(), 'A')
        b = versions.save_version(self.doc_path, make_pdf(), 'B')
        ids = [r['id'] for r in versions.list_versions(self.doc_path)]
        self.assertEqual(ids, [b['id'], a['id']])

    def test_delete_removes_file_and_index_entry(self):
        record = versions.save_version(self.doc_path, make_pdf(), 'temp')
        versions.delete_version(self.doc_path, record['id'])
        self.assertEqual(versions.list_versions(self.doc_path), [])
        self.assertFalse(versions.version_file(self.doc_path, record['id']).exists())
        with self.assertRaises(ValueError):
            versions.read_version(self.doc_path, record['id'])

    def test_history_is_isolated_per_document(self):
        other = str(Path(self.tmp.name) / 'other.pdf')
        Path(other).write_bytes(make_pdf())
        versions.save_version(self.doc_path, make_pdf(), 'mine')
        self.assertEqual(versions.list_versions(other), [])

    def test_empty_snapshot_rejected(self):
        with self.assertRaises(ValueError):
            versions.save_version(self.doc_path, b'')

    def test_index_survives_missing_snapshot_file(self):
        record = versions.save_version(self.doc_path, make_pdf(), 'gone')
        versions.version_file(self.doc_path, record['id']).unlink()  # file vanished
        self.assertEqual(versions.list_versions(self.doc_path), [])  # not listed, no crash


class ProtectedDocumentVersionTests(unittest.TestCase):
    """Snapshots of a password-protected PDF must not be readable without it."""
    PASSWORD = 'hunter2'
    SECRET = 'TOP SECRET salary 123456'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = Path(self.tmp.name) / 'history'
        os.environ['OPENPDF_HISTORY_DIR'] = str(self.history)
        self.doc_path = str(Path(self.tmp.name) / 'secret.pdf')
        source = PDF()
        source.doc.new_page().insert_text((72, 72), self.SECRET)
        source.save(self.doc_path, password=self.PASSWORD)
        source.close()
        self.pdf = PDF(self.doc_path, self.PASSWORD)

    def tearDown(self):
        self.pdf.close()
        os.environ.pop('OPENPDF_HISTORY_DIR', None)
        self.tmp.cleanup()

    def app(self, password):
        # The parts of App that _restore_version touches, without a window.
        return SimpleNamespace(pdf=self.pdf, path=self.doc_path, password=password, history=[],
                               redo_history=[], dirty=False, refresh=lambda: None,
                               status=SimpleNamespace(set=lambda _message: None))

    def snapshot(self, label='v1'):
        return versions.save_version(self.doc_path, self.pdf.snapshot_bytes(self.PASSWORD), label)

    def assert_protected(self, file):
        with fitz.open(file) as snap:
            self.assertTrue(snap.needs_pass)
            self.assertFalse(snap.authenticate('wrong'))
            self.assertTrue(snap.authenticate(self.PASSWORD))
            self.assertIn(self.SECRET, snap[0].get_text())

    def test_snapshot_of_protected_pdf_needs_the_password(self):
        record = self.snapshot()
        self.assertEqual(record['pages'], 1)
        self.assert_protected(versions.version_file(self.doc_path, record['id']))

    def test_password_and_text_are_not_written_to_the_history_folder(self):
        self.snapshot(label='labelled')
        for file in self.history.rglob('*'):
            if file.is_file():
                data = file.read_bytes()
                self.assertNotIn(self.PASSWORD.encode(), data, file.name)
                self.assertNotIn(self.SECRET.encode(), data, file.name)

    def test_restore_and_compare_read_a_protected_snapshot(self):
        record = self.snapshot()
        self.pdf.doc[0].insert_text((72, 144), 'later edit')
        out = Path(self.tmp.name) / 'diff.html'
        self.pdf.compare(str(versions.version_file(self.doc_path, record['id'])), str(out), self.PASSWORD)
        self.assertIn('later&nbsp;edit', out.read_text(encoding='utf-8'))
        self.pdf.restore(versions.read_version(self.doc_path, record['id']), self.PASSWORD)
        self.assertEqual(self.pdf.doc[0].get_text().strip(), self.SECRET)
        # The restored document keeps working for edits and in-memory undo steps.
        self.pdf.doc[0].insert_text((72, 144), 'after restore')
        self.pdf.restore(self.pdf.bytes())
        self.assertIn('after restore', self.pdf.doc[0].get_text())

    def test_app_restore_protects_the_before_restore_snapshot(self):
        from app import App
        record = self.snapshot()
        self.pdf.doc[0].insert_text((72, 144), 'later edit')
        app = self.app(self.PASSWORD)
        App._restore_version(app, record['id'])
        self.assertEqual(self.pdf.doc[0].get_text().strip(), self.SECRET)
        self.assertEqual(len(app.history), 1)
        listed = versions.list_versions(self.doc_path)
        self.assertEqual(sorted(r['label'] for r in listed), ['Before restore', 'v1'])
        for item in listed:
            self.assert_protected(versions.version_file(self.doc_path, item['id']))

    def test_restore_with_the_wrong_password_changes_nothing(self):
        from app import App
        record = self.snapshot()
        self.pdf.doc[0].insert_text((72, 144), 'later edit')
        app = self.app('')
        with self.assertRaises(ValueError):
            App._restore_version(app, record['id'])
        self.assertIn('later edit', self.pdf.doc[0].get_text())
        self.assertEqual(app.history, [])
        self.assertEqual(len(versions.list_versions(self.doc_path)), 1)
        with self.assertRaises(ValueError):
            self.pdf.compare(str(versions.version_file(self.doc_path, record['id'])),
                             str(Path(self.tmp.name) / 'diff.html'))

    def assert_restore_changed_nothing(self, app):
        self.assertIn('later edit', self.pdf.doc[0].get_text())
        self.assertEqual((app.history, app.dirty), ([], False))
        self.assertEqual([r['label'] for r in versions.list_versions(self.doc_path)], ['v1'])

    def test_restore_changes_nothing_when_the_before_restore_snapshot_cannot_be_written(self):
        from app import App
        record = self.snapshot()
        self.pdf.doc[0].insert_text((72, 144), 'later edit')
        app = self.app(self.PASSWORD)
        with patch('app.save_version', side_effect=OSError(errno.ENOSPC, 'No space left on device')):
            with self.assertRaises(OSError):
                App._restore_version(app, record['id'])
        self.assert_restore_changed_nothing(app)

    @unittest.skipIf(os.name == 'nt' or not hasattr(os, 'geteuid') or os.geteuid() == 0,
                     'needs a folder this user cannot write to')
    def test_restore_changes_nothing_when_the_history_folder_is_read_only(self):
        from app import App
        record = self.snapshot()
        self.pdf.doc[0].insert_text((72, 144), 'later edit')
        app = self.app(self.PASSWORD)
        folder = versions.version_file(self.doc_path, record['id']).parent
        os.chmod(folder, 0o555)
        try:
            with self.assertRaises(PermissionError):
                App._restore_version(app, record['id'])
        finally:
            os.chmod(folder, 0o755)
        self.assert_restore_changed_nothing(app)

    def test_unprotected_snapshots_still_restore_and_compare(self):
        # What an unprotected document saves, and what was saved before snapshots were encrypted.
        plain = PDF()
        plain.doc.new_page().insert_text((72, 72), 'plain text')
        data = plain.snapshot_bytes()
        plain.close()
        with fitz.open(stream=data, filetype='pdf') as snap:
            self.assertFalse(snap.needs_pass)
        record = versions.save_version(self.doc_path, data, 'old')
        out = Path(self.tmp.name) / 'diff.html'
        self.pdf.compare(str(versions.version_file(self.doc_path, record['id'])), str(out), self.PASSWORD)
        self.assertIn('plain&nbsp;text', out.read_text(encoding='utf-8'))
        self.pdf.restore(versions.read_version(self.doc_path, record['id']), self.PASSWORD)
        self.assertEqual(self.pdf.doc[0].get_text().strip(), 'plain text')


if __name__ == '__main__':
    unittest.main()
