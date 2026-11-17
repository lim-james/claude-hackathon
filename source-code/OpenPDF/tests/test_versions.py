import os
import tempfile
import errno
import json
import unittest
from unittest import mock
from pathlib import Path
import fitz
import versions


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


    def _disk_full_after(self, half):
        def write(data):
            half(data[:len(data) // 2])
            raise OSError(errno.ENOSPC, 'No space left on device')
        return write

    def test_full_disk_while_writing_index_keeps_old_index(self):
        versions.save_version(self.doc_path, make_pdf(), 'A')
        versions.save_version(self.doc_path, make_pdf(), 'B')
        folder = versions._doc_dir(self.doc_path)
        before = (folder / 'index.json').read_bytes()
        real_fdopen = os.fdopen

        def fdopen(fd, *args, **kwargs):
            handle = real_fdopen(fd, *args, **kwargs)
            handle.write = self._disk_full_after(handle.write)
            return handle
        with mock.patch.object(versions.os, 'fdopen', fdopen):
            with self.assertRaises(OSError):
                versions.save_version(self.doc_path, make_pdf(), 'C')
        self.assertEqual((folder / 'index.json').read_bytes(), before)
        self.assertEqual([r['label'] for r in versions.list_versions(self.doc_path)], ['B', 'A'])
        self.assertEqual(len(list(folder.glob('*.pdf'))), 2)  # C's snapshot removed
        self.assertEqual(list(folder.glob('.*.tmp')), [])

    def test_full_disk_while_writing_snapshot_leaves_no_partial_file(self):
        versions.save_version(self.doc_path, make_pdf(), 'A')
        folder = versions._doc_dir(self.doc_path)
        real = Path.write_bytes

        def write_bytes(path, data):
            real(path, data[:len(data) // 2])
            raise OSError(errno.ENOSPC, 'No space left on device')
        with mock.patch.object(Path, 'write_bytes', write_bytes):
            with self.assertRaises(OSError):
                versions.save_version(self.doc_path, make_pdf(), 'B')
        self.assertEqual(len(list(folder.glob('*.pdf'))), 1)
        self.assertEqual([r['label'] for r in versions.list_versions(self.doc_path)], ['A'])

    def test_cut_short_index_is_rebuilt_and_kept_aside(self):
        for label in ('A', 'B', 'C'):
            versions.save_version(self.doc_path, make_pdf(), label)
        folder = versions._doc_dir(self.doc_path)
        index = folder / 'index.json'
        text = index.read_bytes()
        damaged = text[:text.index(b'"C"')]  # cut short inside the third record
        index.write_bytes(damaged)
        listed = versions.list_versions(self.doc_path)
        self.assertEqual(len(listed), 3)
        self.assertEqual(sorted(r['label'] for r in listed), ['', 'A', 'B'])  # C's record was lost, its file is not
        versions.save_version(self.doc_path, make_pdf(), 'D')
        self.assertEqual([r['label'] for r in versions.list_versions(self.doc_path)][0], 'D')
        self.assertEqual(len(versions.list_versions(self.doc_path)), 4)
        aside = list(folder.glob('index.json.unreadable-*'))
        self.assertEqual(len(aside), 1)
        self.assertEqual(aside[0].read_bytes(), damaged)
        self.assertEqual(len(json.loads(index.read_text(encoding='utf-8'))), 4)

    def test_index_that_is_not_a_list_is_not_replaced_by_one_entry(self):
        versions.save_version(self.doc_path, make_pdf(), 'A')
        folder = versions._doc_dir(self.doc_path)
        (folder / 'index.json').write_bytes(b'\xff{"oops"')  # unreadable bytes, too
        versions.save_version(self.doc_path, make_pdf(), 'B')
        self.assertEqual(len(versions.list_versions(self.doc_path)), 2)

    def test_delete_after_rebuild_keeps_the_rest(self):
        a = versions.save_version(self.doc_path, make_pdf(), 'A')
        b = versions.save_version(self.doc_path, make_pdf(), 'B')
        folder = versions._doc_dir(self.doc_path)
        (folder / 'index.json').write_text('[', encoding='utf-8')
        versions.delete_version(self.doc_path, a['id'])
        self.assertEqual([r['id'] for r in versions.list_versions(self.doc_path)], [b['id']])


if __name__ == '__main__':
    unittest.main()
