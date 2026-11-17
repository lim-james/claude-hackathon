"""Persistent local version history for PDF documents, keyed by file path.

Snapshots live under a per-user directory (never next to the document and never
uploaded). Each tracked document gets a folder named from a hash of its absolute
path, holding the snapshot PDFs plus an index.json of metadata.

index.json is replaced whole, never rewritten in place, so a full disk leaves the
previous index intact. An index that is there but unreadable (cut short by an
older build) is kept aside and the list is rebuilt from the snapshot files.
"""
from pathlib import Path
import hashlib
import json
import os
import tempfile
import time
import uuid

import fitz


def history_root():
    override = os.environ.get('OPENPDF_HISTORY_DIR')
    return Path(override) if override else Path.home() / '.openpdf' / 'history'


def _doc_dir(path):
    key = hashlib.sha256(str(Path(path).resolve()).encode('utf-8')).hexdigest()[:16]
    return history_root() / key


def _index_file(doc_dir):
    return doc_dir / 'index.json'


def _load_index(doc_dir):
    try:
        data = json.loads(_index_file(doc_dir).read_bytes().decode('utf-8'))
        if isinstance(data, list):
            return data
    except FileNotFoundError:
        if not any(doc_dir.glob('*.pdf')):
            return []
    except (OSError, ValueError):
        pass
    return _rebuild_index(doc_dir)


def _salvage(file):
    """The whole records that a damaged index still holds, by id."""
    try:
        text = file.read_bytes().decode('utf-8', 'replace')
    except OSError:
        return {}
    decoder, found = json.JSONDecoder(), {}
    start = text.find('{')
    while start != -1:
        try:
            record, end = decoder.raw_decode(text, start)
        except ValueError:
            break  # the record that was cut short
        if isinstance(record, dict) and isinstance(record.get('id'), str):
            found[record['id']] = record
        start = text.find('{', end)
    return found


def _rebuild_index(doc_dir):
    """List the snapshot files when index.json is unreadable or missing, with
    the details any damaged index still holds. The damaged file is renamed to
    index.json.unreadable-<time> and kept; only if it cannot be renamed does
    the next save replace it, with this full list."""
    file = _index_file(doc_dir)
    kept = sorted(doc_dir.glob('index.json.unreadable-*'))
    if file.exists():
        aside = doc_dir / f"index.json.unreadable-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        try:
            os.rename(file, aside)
            kept.append(aside)
        except OSError:
            kept.append(file)
    known = {}
    for damaged in kept:  # oldest first, so the newest copy of a record wins
        known.update(_salvage(damaged))
    index = []
    for pdf in doc_dir.glob('*.pdf'):
        record = known.get(pdf.stem)
        if record is None:
            try:
                with fitz.open(pdf) as doc:
                    pages = len(doc)
            except Exception:
                pages = 0
            stat = pdf.stat()
            record = {'id': pdf.stem, 'created': stat.st_mtime, 'label': '',
                      'source': '', 'pages': pages, 'bytes': stat.st_size}
        index.append(record)
    index.sort(key=lambda r: r.get('created', 0))
    if not file.exists():
        try:
            _save_index(doc_dir, index)
        except OSError:
            pass  # rebuilt again next time
    return index


def _write_atomic(target, data):
    # Write beside the target, then rename over it: a full disk or a crash
    # leaves the previous file whole instead of cut short.
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix='.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _save_index(doc_dir, index):
    _write_atomic(_index_file(doc_dir), json.dumps(index, indent=2).encode('utf-8'))


def save_version(path, data, label=''):
    if not data:
        raise ValueError('Nothing to snapshot yet.')
    doc_dir = _doc_dir(path)
    doc_dir.mkdir(parents=True, exist_ok=True)
    index = _load_index(doc_dir)
    version_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    written = doc_dir / f'{version_id}.pdf'
    try:
        written.write_bytes(data)
    except BaseException:
        written.unlink(missing_ok=True)  # no cut-short snapshot left behind
        raise
    try:
        with fitz.open(stream=data, filetype='pdf') as doc:
            pages = len(doc)
    except Exception:
        pages = 0
    record = {'id': version_id, 'created': time.time(), 'label': label.strip(),
              'source': Path(path).name, 'pages': pages, 'bytes': len(data)}
    index.append(record)
    try:
        _save_index(doc_dir, index)
    except BaseException:
        written.unlink(missing_ok=True)
        raise
    return record


def list_versions(path):
    doc_dir = _doc_dir(path)
    index = _load_index(doc_dir)
    valid = [r for r in index if (doc_dir / f"{r['id']}.pdf").exists()]
    return sorted(valid, key=lambda r: r.get('created', 0), reverse=True)


def version_file(path, version_id):
    return _doc_dir(path) / f'{version_id}.pdf'


def read_version(path, version_id):
    file = version_file(path, version_id)
    if not file.exists():
        raise ValueError('That version is no longer available.')
    return file.read_bytes()


def delete_version(path, version_id):
    doc_dir = _doc_dir(path)
    file = doc_dir / f'{version_id}.pdf'
    if file.exists():
        file.unlink()
    _save_index(doc_dir, [r for r in _load_index(doc_dir) if r['id'] != version_id])
