import errno
import json
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
        a = versions.save_version(self.doc_path, make_pdf(text='a'), 'A')
        b = versions.save_version(self.doc_path, make_pdf(text='b'), 'B')
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


class HistorySizeTests(unittest.TestCase):
    """Issue #104: unchanged versions are stored once, the history has a stated
    limit, and histories of moved files can be found and cleared."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = Path(self.tmp.name) / 'history'
        os.environ['OPENPDF_HISTORY_DIR'] = str(self.history)
        self.doc_path = str(Path(self.tmp.name) / 'report.pdf')
        Path(self.doc_path).write_bytes(make_pdf(pages=3, text='report'))
        self.pdf = PDF(self.doc_path)

    def tearDown(self):
        self.pdf.close()
        os.environ.pop('OPENPDF_HISTORY_DIR', None)
        self.tmp.cleanup()

    def stored_files(self):
        return sorted(self.history.rglob('*.pdf'))

    def test_unchanged_saves_store_one_copy(self):
        # What Save version passes for an unprotected document; each call renews the trailer /ID.
        datas = [self.pdf.doc.tobytes(garbage=4, deflate=True) for _ in range(5)]
        self.assertNotEqual(datas[0], datas[1])
        first = versions.save_version(self.doc_path, datas[0], 'v1')
        for n, data in enumerate(datas[1:]):
            again = versions.save_version(self.doc_path, data, f'v{n + 2}')
            self.assertTrue(again['unchanged'])
            self.assertEqual(again['id'], first['id'])
        self.assertEqual([r['label'] for r in versions.list_versions(self.doc_path)], ['v1'])
        self.assertEqual(len(self.stored_files()), 1)
        self.pdf.doc[0].insert_text((72, 144), 'an edit')
        versions.save_version(self.doc_path, self.pdf.doc.tobytes(garbage=4, deflate=True), 'edited')
        self.assertEqual(len(self.stored_files()), 2)

    def test_saving_one_past_the_limit_removes_the_oldest(self):
        records = []
        for n in range(4):
            self.pdf.doc[0].insert_text((72, 100 + 12 * n), f'edit {n}')
            self.assertEqual(len(versions.next_removal(self.doc_path, limit=3)), 1 if n >= 3 else 0)
            records.append(versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), f'v{n}', limit=3))
        self.assertEqual([r['label'] for r in versions.list_versions(self.doc_path)], ['v3', 'v2', 'v1'])
        self.assertEqual(len(self.stored_files()), 3)
        self.assertFalse(versions.version_file(self.doc_path, records[0]['id']).exists())
        self.assertEqual(versions.LIMIT, 20)

    def test_deleting_one_of_two_versions_sharing_a_file_keeps_the_other(self):
        a = versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'a')
        b = self.add_shared_record(a, 'b')
        versions.delete_version(self.doc_path, a['id'])
        restored = PDF.unlocked(versions.read_version(self.doc_path, b['id']))
        self.assertEqual(len(restored), 3)
        restored.close()
        versions.delete_version(self.doc_path, b['id'])
        self.assertEqual(self.stored_files(), [])

    def add_shared_record(self, record, label):
        # Earlier builds of this branch recorded an unchanged save as a second
        # record pointing at the same file; such histories must still work.
        index_file = versions._index_file(versions._doc_dir(self.doc_path))
        index = json.loads(index_file.read_text(encoding='utf-8'))
        alias = dict(record, id=record['id'] + 'b', created=record['created'] + 0.001, label=label)
        index_file.write_text(json.dumps(index + [alias]), encoding='utf-8')
        return alias

    def save_edited(self, text, **kw):
        self.pdf.doc[0].insert_text((72, 100 + 3 * len(self.stored_files())), text)
        return versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), text, **kw)

    def test_unchanged_saves_never_push_out_a_distinct_version(self):
        # Review of #120: with 19 distinct versions, unchanged saves used to add
        # entries that counted toward the limit and deleted the oldest distinct ones.
        for n in range(19):
            self.save_edited(f'distinct {n}')
        for n in range(3):
            self.assertEqual(versions.next_removal(self.doc_path), [])
            asked = []
            again = versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), f'unchanged {n}',
                                          confirm=lambda gone: asked.append(gone) or True)
            self.assertTrue(again['unchanged'])
            self.assertEqual(again['label'], 'distinct 18')
            self.assertEqual(asked, [])                     # nothing to ask: nothing is removed
        labels = [r['label'] for r in versions.list_versions(self.doc_path)]
        self.assertEqual(labels, [f'distinct {n}' for n in reversed(range(19))])
        self.assertEqual(len(self.stored_files()), 19)

    def test_unchanged_and_distinct_saves_mixed_keep_the_newest_twenty_distinct(self):
        first = self.save_edited('distinct 0')
        for n in range(1, 19):
            self.save_edited(f'distinct {n}')
        restored = PDF.unlocked(versions.read_version(self.doc_path, first['id']))
        self.pdf.replace(restored)                            # back to an old state: still stored
        self.assertTrue(versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'old again')['unchanged'])
        self.save_edited('distinct 19')                       # the 20th: removes nothing
        self.assertEqual(len(versions.list_versions(self.doc_path)), 20)
        self.assertTrue(versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'same')['unchanged'])
        self.assertEqual(len(versions.list_versions(self.doc_path)), 20)
        self.assertEqual([r['label'] for r in versions.next_removal(self.doc_path)], ['distinct 0'])
        self.pdf.doc[0].insert_text((72, 700), 'distinct 20')
        self.assertIsNone(versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'distinct 20',
                                                confirm=lambda gone: False))   # declined: nothing changes
        self.assertEqual(len(self.stored_files()), 20)
        versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'distinct 20', confirm=lambda gone: True)
        labels = [r['label'] for r in versions.list_versions(self.doc_path)]
        self.assertEqual(labels, [f'distinct {n}' for n in reversed(range(1, 21))])
        self.assertEqual(len(self.stored_files()), 20)

    def test_the_limit_counts_files_when_records_share_one(self):
        a = self.save_edited('a', limit=2)
        self.add_shared_record(a, 'a again')
        b = self.save_edited('b', limit=2)
        self.add_shared_record(b, 'b again')                 # 4 records, 2 files: within the limit of 2
        self.assertEqual(len(versions.list_versions(self.doc_path)), 4)
        self.assertEqual([r['label'] for r in versions.next_removal(self.doc_path, limit=2)], ['a', 'a again'])
        self.save_edited('c', limit=2)                        # removes a's file and both its records
        self.assertEqual([r['label'] for r in versions.list_versions(self.doc_path)], ['c', 'b again', 'b'])
        self.assertEqual(len(self.stored_files()), 2)

    def test_index_written_before_keys_still_lists_restores_and_dedupes(self):
        doc_dir = versions._doc_dir(self.doc_path)
        doc_dir.mkdir(parents=True)
        data = self.pdf.doc.tobytes(garbage=4, deflate=True)
        (doc_dir / '20260101-000000-abcdef.pdf').write_bytes(data)
        (doc_dir / 'index.json').write_text(json.dumps([{   # the record today's master writes
            'id': '20260101-000000-abcdef', 'created': 1.0, 'label': 'old', 'source': 'report.pdf',
            'pages': 3, 'bytes': len(data)}]), encoding='utf-8')
        self.assertEqual([r['label'] for r in versions.list_versions(self.doc_path)], ['old'])
        self.assertEqual(versions.read_version(self.doc_path, '20260101-000000-abcdef'), data)
        self.assertTrue(versions.next_removal(self.doc_path) == [])
        again = versions.save_version(self.doc_path, self.pdf.doc.tobytes(garbage=4, deflate=True), 'new')
        self.assertEqual((again['id'], again['unchanged']), ('20260101-000000-abcdef', True))
        self.assertEqual(len(self.stored_files()), 1)
        self.assertEqual(len(versions.list_versions(self.doc_path)), 1)

    def test_history_of_a_renamed_file_can_be_found_and_cleared(self):
        for n in range(2):
            self.pdf.doc[0].insert_text((72, 144 + 12 * n), f'edit {n}')
            versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), f'v{n}')
        self.pdf.close()
        renamed = str(Path(self.tmp.name) / 'renamed.pdf')
        os.rename(self.doc_path, renamed)
        self.pdf = PDF(renamed)
        self.assertEqual(versions.list_versions(renamed), [])
        found = versions.orphans(exclude=renamed)
        self.assertEqual([(o['path'], o['versions']) for o in found], [(str(Path(self.doc_path).resolve()), 2)])
        self.assertGreater(found[0]['bytes'], 0)
        versions.clear_orphan(found[0]['folder'])
        self.assertEqual(versions.orphans(exclude=renamed), [])
        self.assertEqual(sum(f.stat().st_size for f in self.history.rglob('*') if f.is_file()), 0)
        with self.assertRaises(ValueError):
            versions.clear_orphan('..')

    def test_a_document_still_in_place_is_not_an_orphan(self):
        versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'v')
        self.assertEqual(versions.orphans(), [])
        versions.clear_history(self.doc_path)
        self.assertEqual((versions.list_versions(self.doc_path), versions.usage(self.doc_path)), ([], (0, 0)))

    def test_restore_adds_no_copy_when_the_current_state_is_stored(self):
        from app import App
        first = versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'v1')
        self.pdf.doc[0].insert_text((72, 144), 'edit')
        versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'v2')
        app = SimpleNamespace(pdf=self.pdf, path=self.doc_path, password='', history=[], redo_history=[],
                              dirty=False, refresh=lambda: None, status=SimpleNamespace(set=lambda _m: None))
        App._restore_version(app, first['id'])
        self.assertEqual([r['label'] for r in versions.list_versions(self.doc_path)], ['v2', 'v1'])
        self.assertEqual(len(self.stored_files()), 2)
        self.pdf.doc[0].insert_text((72, 160), 'unsaved edit')
        App._restore_version(app, first['id'])                   # this state is new: it is kept
        self.assertEqual(versions.list_versions(self.doc_path)[0]['label'], 'Before restore')

    def test_a_failed_snapshot_write_leaves_the_index_and_no_partial_file(self):
        versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'kept')
        index = versions._index_file(versions._doc_dir(self.doc_path))
        before = index.read_bytes()
        self.pdf.doc[0].insert_text((72, 144), 'edit')
        real_replace = os.replace
        def full_disk(src, dst):
            if str(dst).endswith('index.json'):
                raise OSError(errno.ENOSPC, 'No space left on device')
            return real_replace(src, dst)
        with patch('versions.os.replace', side_effect=full_disk):
            with self.assertRaises(OSError):
                versions.save_version(self.doc_path, self.pdf.snapshot_bytes(), 'lost')
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual(len(self.stored_files()), 1)
        self.assertEqual([f.name for f in index.parent.iterdir() if f.suffix == '.tmp'], [])


class ProtectedHistorySizeTests(ProtectedDocumentVersionTests):
    def keyed_snapshot(self, label):
        from app import save_snapshot  # what Save version runs
        return save_snapshot(self.app(self.PASSWORD), label)

    def test_unchanged_protected_saves_store_one_encrypted_copy(self):
        a = self.keyed_snapshot('a')
        with patch.object(self.pdf, 'snapshot_bytes', wraps=self.pdf.snapshot_bytes) as encrypt:
            b = self.keyed_snapshot('b')
        encrypt.assert_not_called()  # content already stored: no slow encrypted copy is made
        self.assertEqual((b['id'], b['unchanged']), (a['id'], True))
        files = list(self.history.rglob('*.pdf'))
        self.assertEqual(len(files), 1)
        self.assert_protected(files[0])
        self.assertNotEqual(a['key'], versions.content_key(self.pdf.doc.tobytes(
            garbage=4, deflate=True, no_new_id=True, encryption=fitz.PDF_ENCRYPT_NONE)))  # keyed with the password
        self.pdf.doc[0].insert_text((72, 144), 'edit')
        self.keyed_snapshot('c')
        self.assertEqual(len(list(self.history.rglob('*.pdf'))), 2)
