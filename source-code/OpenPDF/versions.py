"""Persistent local version history for PDF documents, keyed by file path.

Snapshots live under a per-user directory (never next to the document and never
uploaded). Each tracked document gets a folder named from a hash of its absolute
path, holding the snapshot PDFs plus an index.json of metadata.

A version whose content is already stored is recorded without a second copy:
records carry a content ``key`` and the ``file`` they point at (old records
have neither and point at their own id). Each document keeps at most LIMIT
versions; saving one more removes the oldest, and the dialog says so first.
Records also hold the document's full ``path``, so the history of a file that
was moved or deleted can be found again (``orphans``) and cleared.
"""
from pathlib import Path
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
import uuid

import fitz

LIMIT = 20  # versions kept per document; saving one more removes the oldest


def history_root():
    override = os.environ.get('OPENPDF_HISTORY_DIR')
    return Path(override) if override else Path.home() / '.openpdf' / 'history'


def _doc_dir(path):
    key = hashlib.sha256(str(Path(path).resolve()).encode('utf-8')).hexdigest()[:16]
    return history_root() / key


def _index_file(doc_dir):
    return doc_dir / 'index.json'


def _record_file(doc_dir, record):
    return doc_dir / f"{record.get('file') or record['id']}.pdf"


def _load_index(doc_dir):
    file = _index_file(doc_dir)
    if not file.exists():
        return []
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


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


# The trailer's /ID: two strings, each written as <hex> or as a (literal) with escapes.
_PDF_STRING = rb'(?:<[0-9A-Fa-f\s]*>|\((?:\\.|[^\\()])*\))'
_TRAILER_ID = re.compile(rb'/ID\s*\[\s*' + _PDF_STRING + rb'\s*' + _PDF_STRING + rb'\s*\]', re.S)


def content_key(data, secret=''):
    """A fingerprint of a PDF that ignores the trailer /ID, which PyMuPDF
    renews on every save of an unchanged document. With ``secret`` (the
    document's password) it is an HMAC, so the index does not hold a plain
    fingerprint of a protected document."""
    start = data.rfind(b'trailer')
    head, tail = (memoryview(data)[:start], bytes(data[start:])) if start != -1 else (memoryview(data), b'')
    tail = _TRAILER_ID.sub(b'/ID[<><>]', tail, count=1)
    digest = hmac.new(secret.encode('utf-8'), digestmod=hashlib.sha256) if secret else hashlib.sha256()
    digest.update(head)
    digest.update(tail)
    return digest.hexdigest()


def _stored(doc_dir, index):
    """Records whose snapshot file exists, oldest first."""
    valid = [r for r in index if _record_file(doc_dir, r).exists()]
    return sorted(valid, key=lambda r: r.get('created', 0))


def _remove_unreferenced(doc_dir, kept, removed):
    used = {_record_file(doc_dir, r) for r in kept}
    for record in removed:
        file = _record_file(doc_dir, record)
        if file not in used:
            file.unlink(missing_ok=True)


def next_removal(path, limit=LIMIT):
    """The versions that saving one more version of ``path`` would remove."""
    doc_dir = _doc_dir(path)
    stored = _stored(doc_dir, _load_index(doc_dir))
    return stored[:max(0, len(stored) - limit + 1)]


def save_version(path, data, label='', key=None, only_if_new=False, limit=LIMIT):
    """Record a version of ``path``. Content that is already stored is not
    written again. With ``only_if_new`` nothing is recorded when it is, and the
    existing record is returned. Beyond ``limit`` versions the oldest go.
    With a ``key``, ``data`` may be a function, called only when the content
    is not stored yet (an encrypted snapshot is slow to make)."""
    if callable(data) and key is None:
        data = data()
    if not callable(data) and not data:
        raise ValueError('Nothing to snapshot yet.')
    doc_dir = _doc_dir(path)
    doc_dir.mkdir(parents=True, exist_ok=True)
    index = _load_index(doc_dir)
    plain = key is None
    if plain:
        key = content_key(data)
    same = None
    for record in reversed(_stored(doc_dir, index)):
        known = record.get('key')
        if known is None and plain and record.get('bytes') == len(data):
            # Saved before keys existed: fingerprint it once, only when the size matches.
            try:
                known = record['key'] = content_key(_record_file(doc_dir, record).read_bytes())
            except OSError:
                continue
        if known == key:
            same = record
            break
    if same is not None and only_if_new:
        return same
    version_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    written = None
    if same is not None:
        file_id, pages, size = same.get('file') or same['id'], same.get('pages', 0), same.get('bytes', 0)
    else:
        if callable(data):
            data = data()
            if not data:
                raise ValueError('Nothing to snapshot yet.')
        file_id, written, size = version_id, doc_dir / f'{version_id}.pdf', len(data)
        _write_atomic(written, data)
        try:
            with fitz.open(stream=data, filetype='pdf') as doc:
                pages = len(doc)
        except Exception:
            pages = 0
    record = {'id': version_id, 'created': time.time(), 'label': label.strip(),
              'source': Path(path).name, 'path': str(Path(path).resolve()), 'pages': pages,
              'bytes': size, 'key': key, 'file': file_id}
    index.append(record)
    stored = _stored(doc_dir, index)
    removed = stored[:max(0, len(stored) - max(1, limit))]
    kept = [r for r in index if not any(r is gone for gone in removed)]
    try:
        _save_index(doc_dir, kept)
    except BaseException:
        if written is not None:
            written.unlink(missing_ok=True)
        raise
    _remove_unreferenced(doc_dir, kept, removed)
    return record


def remember_path(path):
    """Record ``path`` in a history saved before paths were recorded, so it is
    not later mistaken for the history of a moved or deleted file."""
    doc_dir = _doc_dir(path)
    index = _load_index(doc_dir)
    if index and any('path' not in r for r in index):
        for record in index:
            record.setdefault('path', str(Path(path).resolve()))
        _save_index(doc_dir, index)


def list_versions(path):
    doc_dir = _doc_dir(path)
    return list(reversed(_stored(doc_dir, _load_index(doc_dir))))


def shared_files(path):
    """How many listed versions point at each snapshot file."""
    doc_dir = _doc_dir(path)
    counts = {}
    for record in _stored(doc_dir, _load_index(doc_dir)):
        name = _record_file(doc_dir, record).name
        counts[name] = counts.get(name, 0) + 1
    return counts


def usage(path):
    """Versions listed for ``path`` and the bytes its history folder holds."""
    doc_dir = _doc_dir(path)
    return len(list_versions(path)), _folder_bytes(doc_dir)


def _folder_bytes(doc_dir):
    return sum(f.stat().st_size for f in doc_dir.glob('*.pdf')) if doc_dir.is_dir() else 0


def version_file(path, version_id):
    doc_dir = _doc_dir(path)
    for record in _load_index(doc_dir):
        if record.get('id') == version_id:
            return _record_file(doc_dir, record)
    return doc_dir / f'{version_id}.pdf'


def read_version(path, version_id):
    file = version_file(path, version_id)
    if not file.exists():
        raise ValueError('That version is no longer available.')
    return file.read_bytes()


def delete_version(path, version_id):
    doc_dir = _doc_dir(path)
    index = _load_index(doc_dir)
    removed = [r for r in index if r['id'] == version_id]
    kept = [r for r in index if r['id'] != version_id]
    _save_index(doc_dir, kept)
    _remove_unreferenced(doc_dir, kept, removed)


def _clear(doc_dir):
    # Snapshots first: if this stops part-way the index lists only what remains.
    for file in doc_dir.glob('*.pdf'):
        file.unlink(missing_ok=True)
    _index_file(doc_dir).unlink(missing_ok=True)
    try:
        doc_dir.rmdir()
    except OSError:
        pass


def clear_history(path):
    """Delete every saved version of ``path``."""
    _clear(_doc_dir(path))


def orphans(exclude=None):
    """Histories whose document is no longer at its recorded path, plus those
    saved before paths were recorded (``path`` is None for them)."""
    root = history_root()
    if not root.is_dir():
        return []
    skip = _doc_dir(exclude) if exclude else None
    found = []
    for doc_dir in sorted(root.iterdir()):
        if not doc_dir.is_dir() or doc_dir == skip:
            continue
        index = _load_index(doc_dir)
        paths = [r['path'] for r in index if r.get('path')]
        doc = paths[-1] if paths else None
        if doc and Path(doc).exists():
            continue
        size = _folder_bytes(doc_dir)
        if not index and not size:
            continue
        found.append({'folder': doc_dir.name, 'path': doc,
                      'source': index[-1].get('source', '') if index else '',
                      'versions': len(_stored(doc_dir, index)), 'bytes': size})
    return found


def clear_orphan(folder):
    """Delete the history folder ``folder`` named by ``orphans``."""
    doc_dir = history_root() / folder
    if Path(folder).name != folder or folder in ('', '.', '..') or not doc_dir.is_dir():
        raise ValueError('That history is no longer there.')
    _clear(doc_dir)
