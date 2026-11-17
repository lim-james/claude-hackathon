"""Local PDF operations. Coordinates are PDF points in unrotated page space."""
from pathlib import Path
import datetime
import difflib
import html
import io
import os
import shutil
import subprocess
import sys
import tempfile

import fitz
from PIL import Image
from text_tools import find_matches, replace_match, select_text
from rich_boxes import apply_redactions


def page_numbers(spec, count):
    result = []
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = map(int, part.split('-', 1))
            result.extend(range(a - 1, b, 1) if a <= b else range(a - 1, b - 2, -1))
        else:
            result.append(int(part) - 1)
    if not result or any(p < 0 or p >= count for p in result):
        raise ValueError(f'Enter pages between 1 and {count}, e.g. 1,3-5.')
    return result


def _ref(doc, xref, key):
    kind, value = doc.xref_get_key(xref, key)
    return int(value.split()[0]) if kind == 'xref' else 0


def _bookmarks_to(doc, page_xrefs):
    """Xrefs of the bookmarks that open one of these pages, parents before
    children. Read with MuPDF's object calls: ``get_toc`` costs about as much
    as removing a page from a long document."""
    mu = fitz.mupdf
    pdf = fitz._as_pdf_document(doc)

    def page_of(item):
        dest = mu.pdf_dict_gets(item, 'Dest')
        if not dest.m_internal:
            action = mu.pdf_dict_gets(item, 'A')
            if mu.pdf_to_name(mu.pdf_dict_gets(action, 'S')) != 'GoTo':
                return 0
            dest = mu.pdf_dict_gets(action, 'D')
        if mu.pdf_is_name(dest) or mu.pdf_is_string(dest):
            dest = mu.pdf_lookup_dest(pdf, dest)
        if mu.pdf_is_dict(dest):
            dest = mu.pdf_dict_gets(dest, 'D')
        page = mu.pdf_array_get(dest, 0) if mu.pdf_is_array(dest) else None
        return mu.pdf_to_num(page) if page is not None and mu.pdf_is_indirect(page) else 0

    found, seen = [], set()
    stack = [mu.pdf_dict_gets(mu.pdf_dict_getp(mu.pdf_trailer(pdf), 'Root/Outlines'), 'First')]
    while stack:
        item = stack.pop()
        while mu.pdf_is_dict(item) and mu.pdf_to_num(item) not in seen:
            seen.add(mu.pdf_to_num(item))
            if page_of(item) in page_xrefs:
                found.append(mu.pdf_to_num(item))
            stack.append(mu.pdf_dict_gets(item, 'Next'))
            item = mu.pdf_dict_gets(item, 'First')
    return found


def _unlink_bookmark(doc, xref):
    """Take an outline item out of its list; its children, if any, take its place."""
    parent, prev, following = (_ref(doc, xref, key) for key in ('Parent', 'Prev', 'Next'))
    first, last = _ref(doc, xref, 'First'), _ref(doc, xref, 'Last')
    if first and last:
        child = first
        while child:
            doc.xref_set_key(child, 'Parent', f'{parent} 0 R')
            child = _ref(doc, child, 'Next') if child != last else 0
        start, end = first, last
    else:
        start, end = following, prev
    for owner, key, value in ((start, 'Prev', prev), (end, 'Next', following)):
        if owner:
            doc.xref_set_key(owner, key, f'{value} 0 R' if value else 'null')
    for neighbour, key, value in ((prev, 'Next', start), (following, 'Prev', end)):
        if neighbour:
            doc.xref_set_key(neighbour, key, f'{value} 0 R' if value else 'null')
    if parent:
        if not prev:
            doc.xref_set_key(parent, 'First', f'{start} 0 R' if start else 'null')
        if not following:
            doc.xref_set_key(parent, 'Last', f'{end} 0 R' if end else 'null')
    for key in ('Parent', 'Prev', 'Next', 'First', 'Last'):
        doc.xref_set_key(xref, key, 'null')


def _recount(doc, xref):
    """Rewrite each /Count below outline root ``xref`` that no longer matches its items."""
    mu = fitz.mupdf
    pdf = fitz._as_pdf_document(doc)
    seen = set()

    def visit(item, number, is_root):
        # Returns how many items show below ``item`` when it is open.
        shown, child = 0, mu.pdf_dict_gets(item, 'First')
        while mu.pdf_is_dict(child) and mu.pdf_to_num(child) not in seen:
            seen.add(mu.pdf_to_num(child))
            below = visit(child, mu.pdf_to_num(child), False)
            shown += 1 + (below if mu.pdf_to_int(mu.pdf_dict_gets(child, 'Count')) >= 0 else 0)
            child = mu.pdf_dict_gets(child, 'Next')
        count = mu.pdf_dict_gets(item, 'Count')
        old = mu.pdf_to_int(count) if mu.pdf_is_int(count) else None
        new = shown if is_root or not (old or 0) < 0 else -shown
        if not shown and not is_root:
            new = None
        if new != old:
            doc.xref_set_key(number, 'Count', 'null' if new is None else str(new))
        return shown

    if xref:
        visit(mu.pdf_load_object(pdf, xref), xref, True)


class PDF:
    def __init__(self, path=None, password=''):
        self.doc = fitz.open(path) if path else fitz.open()
        if self.doc.needs_pass and not self.doc.authenticate(password):
            self.doc.close()
            raise ValueError('Incorrect password.')
        if path and not self.doc.is_pdf:
            data = self.doc.convert_to_pdf()
            self.doc.close()
            self.doc = fitz.open(stream=data, filetype='pdf')
        # Undo bookkeeping, see snapshot(). A plain PDF file is its own base,
        # read on the first edit; anything else gets one when it is first copied.
        self._base, self._tail, self._xrefs, self._changed, self._file = None, b'', 0, False, None
        if path and self.doc.name:
            self._file = (path, self._signature(path))

    @staticmethod
    def _signature(path):
        stat = os.stat(path)
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    def close(self):
        self.doc.close()

    def bytes(self):
        # Undo/preview snapshots are transient. Expensive garbage collection and
        # compression belong to Save as, not every mouse gesture or edit.
        return self.doc.tobytes(garbage=0, deflate=False)

    def restore(self, data):
        # data is what bytes() or snapshot() returned.
        base, tail = data if isinstance(data, tuple) else (data, b'')
        self.doc.close()
        self.doc = fitz.open(stream=base + tail if tail else base, filetype='pdf')
        self._base, self._tail, self._xrefs, self._changed, self._file = base, tail, self.doc.xref_length(), False, None

    def snapshot(self):
        """The document as it is now, for restore(): an undo step.

        A full copy costs time and memory in proportion to the file, so a step
        is stored as (base, tail): base is one full copy shared by every step,
        tail holds only the objects changed since, written as a PDF incremental
        update. base + tail is a complete PDF.
        """
        if self._file:
            path, signature = self._file
            self._file = None
            try:
                data = Path(path).read_bytes()
                if self._signature(path) == signature and len(data) == signature[1]:
                    self._base = data
            except OSError:
                pass
        tail = self._changes()
        if tail is None:
            # No usable base (new, encrypted or repaired file, or renumbered by
            # a garbage-collecting save): take one full copy and continue from it.
            data = self.bytes()
            self.restore(data)
            return data
        return (self._base, self._tail + tail)

    def _changes(self):
        """Objects changed since the document was opened, as an incremental update; None if unknown."""
        try:
            tail = self._incremental_update()
        except Exception:
            tail = None
        if tail is None:
            self._base = None
        return tail

    def _incremental_update(self):
        mu, pdf = fitz.mupdf, fitz._as_pdf_document(self.doc)
        state = pdf.m_internal
        count, changed = mu.pdf_xref_len(pdf), bool(state.num_incremental_sections)
        # MuPDF records every changed object in an incremental section, the way
        # it does for its own incremental save. That record is unusable once
        # objects were renumbered, or when the file needed repair or decryption
        # or does not start at its header.
        if (self._base is None or count < self._xrefs or self._changed > changed
                or state.repair_attempted or state.crypt or state.bias):
            return None
        self._xrefs, self._changed = count, changed
        if not changed:
            return b''

        def source(obj):
            buffer = mu.fz_new_buffer(512)
            out = mu.FzOutput(buffer)
            mu.pdf_print_obj(out, obj, 1, 1)
            out.fz_close_output()
            return mu.fz_buffer_extract_copy(buffer)

        out, offsets = [b'\n'], {}
        position = len(self._base) + len(self._tail) + 1
        for xref in range(1, count):
            if not mu.pdf_xref_is_incremental(pdf, xref):
                continue
            obj = mu.pdf_load_object(pdf, xref)
            parts = [b'%d 0 obj\n' % xref, source(obj)]
            if mu.pdf_obj_num_is_stream(pdf, xref):
                raw = mu.fz_buffer_extract_copy(mu.pdf_load_raw_stream_number(pdf, xref))
                if mu.pdf_to_int(mu.pdf_dict_gets(obj, 'Length')) != len(raw):
                    return None
                parts += [b'\nstream\n', raw, b'\nendstream']
            parts.append(b'\nendobj\n')
            offsets[xref] = position
            position += sum(map(len, parts))
            out += parts
        out.append(b'xref\n')
        numbers = sorted(offsets)
        start = 0
        while start < len(numbers):
            end = start
            while end + 1 < len(numbers) and numbers[end + 1] == numbers[end] + 1:
                end += 1
            out.append(b'%d %d\n' % (numbers[start], end - start + 1))
            out += [b'%010d 00000 n \n' % offsets[n] for n in numbers[start:end + 1]]
            start = end + 1
        out.append(b'trailer\n<</Size %d/Prev %d' % (count, state.startxref))
        trailer = mu.pdf_trailer(pdf)
        for key in ('Root', 'Info', 'ID'):
            value = mu.pdf_dict_gets(trailer, key)
            if value.m_internal and not mu.pdf_is_null(value):
                out.append(b'/%s %s' % (key.encode(), source(value)))
        out.append(b'>>\nstartxref\n%d\n%%%%EOF\n' % position)
        return b''.join(out)

    # User-facing permission groups; each maps to the low-level PDF_PERM_* flags
    # that a password-protected export grants to whoever opens with the password.
    PERMISSIONS = [
        ('print', 'Allow printing', fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ),
        ('copy', 'Allow copying text and images', fitz.PDF_PERM_COPY | fitz.PDF_PERM_ACCESSIBILITY),
        ('modify', 'Allow editing the content', fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ASSEMBLE),
        ('annotate', 'Allow comments and markup', fitz.PDF_PERM_ANNOTATE),
        ('forms', 'Allow filling form fields', fitz.PDF_PERM_FORM),
    ]

    @staticmethod
    def permission_mask(allowed_keys):
        allowed = set(allowed_keys)
        mask = 0
        for key, _label, flags in PDF.PERMISSIONS:
            if key in allowed:
                mask |= flags
        return mask

    def save(self, path, password='', owner_password='', permissions=None):
        options = dict(garbage=4, deflate=True)
        if password:
            if permissions is None:  # no restrictions chosen: grant everything
                permissions = self.permission_mask(key for key, _label, _flags in self.PERMISSIONS)
            options.update(encryption=fitz.PDF_ENCRYPT_AES_256,
                           user_pw=password, owner_pw=owner_password or password,
                           permissions=permissions)
        data = self.doc.tobytes(**options)
        self._base = self._file = None  # garbage collection renumbered the open document
        self._atomic_write(path, data)

    @staticmethod
    def _atomic_write(path, data):
        # Atomic replacement avoids truncating a source PDF that is still open.
        target = Path(path)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix='.pdf')
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(data)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def current_size(self):
        data = self.doc.tobytes(garbage=4, deflate=True)
        self._base = self._file = None  # garbage collection renumbered the open document
        return len(data)

    def optimized_bytes(self, dpi=120, quality=70, downsample_images=True,
                        subset_fonts=True, strip_metadata=False):
        # Work on a copy so the open document is never mutated by optimization.
        work = fitz.open('pdf', self.doc.tobytes())
        try:
            if strip_metadata:
                work.set_metadata({})
                work.del_xml_metadata()
            if downsample_images:
                # Downsample images meaningfully above the target DPI (Acrobat uses
                # a ~1.5x threshold) down to it, re-encoding at the chosen quality.
                work.rewrite_images(dpi_threshold=max(dpi + 1, round(dpi * 1.5)), dpi_target=dpi,
                                    quality=quality, lossy=True, lossless=True)
            if subset_fonts:
                try:
                    work.subset_fonts()
                except Exception:
                    pass  # subsetting can fail on unusual fonts; skip rather than abort
            return work.tobytes(garbage=4, deflate=True, clean=True)
        finally:
            work.close()

    def optimize(self, path, **options):
        data = self.optimized_bytes(**options)
        self._atomic_write(path, data)
        return len(data)

    def merge(self, paths):
        for path in paths:
            source = PDF(path)
            try:
                self.doc.insert_pdf(source.doc)
            finally:
                source.close()

    def select(self, spec):
        self.keep_pages(page_numbers(spec, len(self.doc)))

    def delete_page(self, index):
        self.keep_pages([i for i in range(len(self.doc)) if i != index])

    def keep_pages(self, pages):
        """Keep these 0-based pages in this order, and the bookmarks that still open one.

        A bookmark to a removed page is taken out and its children take its
        place, so none is left opening nothing and none to a kept page is lost.
        ``doc.delete_page`` keeps them all; ``doc.select`` keeps a parent with
        surviving children, opening nothing, and loses grandchildren.
        """
        kept = set(pages)
        removed = {self.doc.page_xref(i) for i in range(len(self.doc)) if i not in kept}
        dead = _bookmarks_to(self.doc, removed)
        for xref in dead:
            _unlink_bookmark(self.doc, xref)
        if dead:
            _recount(self.doc, _ref(self.doc, self.doc.pdf_catalog(), 'Outlines'))
        self.doc.select(pages)

    def split(self, folder):
        for index in range(len(self.doc)):
            out = fitz.open()
            out.insert_pdf(self.doc, from_page=index, to_page=index)
            out.save(str(Path(folder) / f'page-{index + 1:04d}.pdf'))
            out.close()

    def insert_text(self, index, rect, text, size=12):
        page = self.doc[index]
        # Shape commits only if all text fits, preventing partial silent exports.
        shape = page.new_shape()
        remaining = shape.insert_textbox(rect, text, fontsize=size, fontname='helv')
        if remaining < 0:
            raise ValueError('Text does not fit. Select a larger area or reduce font size.')
        shape.commit()

    def replace_text(self, index, old, new, size=12):
        page = self.doc[index]
        boxes = page.search_for(old)
        if not boxes:
            raise ValueError('Text not found on this page.')
        # Preflight every replacement before deleting any original text.
        for rect in boxes:
            trial = page.new_shape()
            if trial.insert_textbox(rect, new, fontsize=size, fontname='helv') < 0:
                raise ValueError('Replacement does not fit the original area. Use a smaller font.')
        for rect in boxes:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        apply_redactions(page, images=0, graphics=0)
        for rect in boxes:
            self.insert_text(index, rect, new, size)
        return len(boxes)

    def redact(self, index, rect):
        page = self.doc[index]
        page.add_redact_annot(rect, fill=(0, 0, 0))
        apply_redactions(page, images=2, graphics=2, text=0)

    def sanitize(self):
        self.doc.set_metadata({})
        self.doc.del_xml_metadata()
        for name in self.doc.embfile_names():
            self.doc.embfile_del(name)

    def add_image(self, index, rect, path):
        self.doc[index].insert_image(rect, filename=str(path), keep_proportion=True)

    def highlight(self, index, text):
        page = self.doc[index]
        boxes = page.search_for(text, quads=True)
        if not boxes:
            raise ValueError('No matching text on this page.')
        page.add_highlight_annot(boxes)
        return len(boxes)

    # Text-markup annotations draw over selected character quads.
    MARKUP = {'highlight': 'add_highlight_annot', 'strikeout': 'add_strikeout_annot',
              'underline': 'add_underline_annot', 'squiggly': 'add_squiggly_annot'}

    def markup(self, index, quads, kind, author='OpenPDF'):
        method = self.MARKUP.get(kind)
        if not method:
            raise ValueError('Unknown markup type.')
        if not quads:
            raise ValueError('Select text first, then choose a markup.')
        page = self.doc[index]  # keep the page alive so annot.update() can reach it
        annot = getattr(page, method)(quads)
        annot.set_info(title=author)
        annot.update()
        return annot

    def note(self, index, point, text, author='OpenPDF'):
        if not text.strip():
            raise ValueError('Enter note text.')
        page = self.doc[index]
        annot = page.add_text_annot(point, text)
        annot.set_info(title=author)
        annot.update()
        return annot

    def callout(self, index, rect, text, author='OpenPDF', size=11):
        if not text.strip():
            raise ValueError('Enter callout text.')
        page = self.doc[index]
        annot = page.add_freetext_annot(rect, text, fontsize=size)
        annot.set_info(title=author)
        annot.update()
        return annot

    def shape(self, index, kind, rect, color=(0.16, 0.16, 0.55), width=1.5, author='OpenPDF'):
        page = self.doc[index]
        if kind in ('line', 'arrow'):
            annot = page.add_line_annot(rect.tl, rect.br)
            if kind == 'arrow':
                annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
        elif kind == 'rectangle':
            annot = page.add_rect_annot(rect)
        elif kind == 'oval':
            annot = page.add_circle_annot(rect)
        else:
            raise ValueError('Unknown shape type.')
        annot.set_colors(stroke=color)
        annot.set_border(width=width)
        annot.set_info(title=author)
        annot.update()
        return annot

    # Adobe-style rubber stamps; id maps to fitz.STAMP_* (0 = Approved).
    STAMPS = ['Approved', 'AsIs', 'Confidential', 'Departmental', 'Experimental',
              'Expired', 'Final', 'ForComment', 'ForPublicRelease', 'NotApproved',
              'NotForPublicRelease', 'Sold', 'TopSecret', 'Draft']

    def stamp(self, index, rect, stamp_id, author='OpenPDF'):
        if not 0 <= stamp_id < len(self.STAMPS):
            raise ValueError('Unknown stamp.')
        page = self.doc[index]
        annot = page.add_stamp_annot(rect, stamp=stamp_id)
        annot.set_info(title=author)
        annot.update()
        return annot

    def annotations(self):
        # Every markup/shape/note/stamp/ink annotation across the document.
        items = []
        for index in range(len(self.doc)):
            page = self.doc[index]  # hold the page; annots die with their parent
            for annot in page.annots():
                info = annot.info
                items.append({'page': index, 'kind': annot.type[1],
                              'text': info.get('content', '') or '',
                              'author': info.get('title', '') or '',
                              'rect': tuple(annot.rect), 'xref': annot.xref})
        return items

    def delete_annotation(self, index, xref):
        page = self.doc[index]
        for annot in page.annots():
            if annot.xref == xref:
                page.delete_annot(annot)
                return True
        return False

    # Supported interactive field types. Radio groups need a parent-field
    # structure PyMuPDF's add_widget does not build, so they are not offered yet.
    FIELD_TYPES = {
        'text': fitz.PDF_WIDGET_TYPE_TEXT,
        'checkbox': fitz.PDF_WIDGET_TYPE_CHECKBOX,
        'dropdown': fitz.PDF_WIDGET_TYPE_COMBOBOX,
        'listbox': fitz.PDF_WIDGET_TYPE_LISTBOX,
        'signature': fitz.PDF_WIDGET_TYPE_SIGNATURE,
    }

    def add_field(self, index, rect, name, kind='text', choices=None):
        if not name.strip():
            raise ValueError('A field name is required.')
        widget_type = self.FIELD_TYPES.get(kind)
        if widget_type is None:
            raise ValueError('Unsupported field type.')
        if any(w.field_name == name for p in self.doc for w in (p.widgets() or [])):
            raise ValueError('Field names must be unique.')
        widget = fitz.Widget()
        widget.field_name = name
        widget.field_type = widget_type
        widget.rect = rect
        widget.border_width = 1
        widget.border_color = (0.3, 0.5, 0.7)
        widget.text_fontsize = 12
        if kind == 'checkbox':
            widget.field_value = 'Off'
        elif kind in ('dropdown', 'listbox'):
            options = [c for c in (choices or []) if c]
            if not options:
                raise ValueError('Provide at least one option for a dropdown or list box.')
            widget.choice_values = options
            widget.field_value = options[0]
        elif kind == 'text':
            widget.field_value = ''
        self.doc[index].add_widget(widget)

    def fields(self):
        return [(i, w.field_name, w.field_value, w.field_type, list(w.choice_values or []))
                for i, p in enumerate(self.doc) for w in (p.widgets() or [])]

    def fill_field(self, index, name, value):
        for w in self.doc[index].widgets() or []:
            if w.field_name == name:
                if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                    w.field_value = w.on_state() if str(value).lower() in ('yes', 'true', '1', 'on') else 'Off'
                elif w.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
                    w.field_value = value
                elif w.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
                    options = w.choice_values or []
                    if value and options and value not in options:
                        raise ValueError(f'{value!r} is not one of: {", ".join(options)}')
                    w.field_value = value
                else:
                    raise ValueError('This field type cannot be filled here.')
                w.update()
                return
        raise ValueError('Field not found.')

    _XFDF_NS = 'http://ns.adobe.com/xfdf/'

    def export_form_data(self, path):
        # Write field values as XFDF so a filled form's answers can be reused.
        import xml.etree.ElementTree as ET
        ns = self._XFDF_NS
        ET.register_namespace('', ns)
        root = ET.Element(f'{{{ns}}}xfdf')
        container = ET.SubElement(root, f'{{{ns}}}fields')
        count = 0
        for page in self.doc:
            for w in (page.widgets() or []):
                if w.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE:
                    continue
                field = ET.SubElement(container, f'{{{ns}}}field')
                field.set('name', w.field_name)
                value = ET.SubElement(field, f'{{{ns}}}value')
                value.text = '' if w.field_value is None else str(w.field_value)
                count += 1
        ET.ElementTree(root).write(str(path), encoding='utf-8', xml_declaration=True)
        return count

    def import_form_data(self, path):
        # Accept XFDF from OpenPDF or Acrobat (namespaced or not) and apply by name.
        import xml.etree.ElementTree as ET
        local = lambda tag: tag.rsplit('}', 1)[-1]
        tree = ET.parse(str(path))  # local, user-selected file
        values = {}
        for element in tree.iter():
            if local(element.tag) == 'field' and element.get('name'):
                value_el = next((c for c in element if local(c.tag) == 'value'), None)
                values[element.get('name')] = (value_el.text or '') if value_el is not None else ''
        applied = 0
        for page in self.doc:
            for w in (page.widgets() or []):
                if w.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE or w.field_name not in values:
                    continue
                val = values[w.field_name]
                if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                    w.field_value = w.on_state() if val not in ('', 'Off', 'off') else 'Off'
                else:
                    w.field_value = val
                w.update()
                applied += 1
        return applied

    def watermark(self, text, **options):
        from watermark_tools import add_watermarks
        add_watermarks(self.doc, text, **options)

    @staticmethod
    def _stamp_text(page, point, text, **options):
        # point is in displayed (rotated) page coordinates; insert_text works in unrotated space.
        page.insert_text(fitz.Point(point) * page.derotation_matrix, text,
                         rotate=page.rotation, **options)

    def number_pages(self):
        for i, page in enumerate(self.doc):
            self._stamp_text(page, (page.rect.width / 2, page.rect.height - 20),
                             f'{i + 1} / {len(self.doc)}', fontsize=10)

    # Header/footer slots; templates may contain <page> <pages> <date> <filename>.
    HEADER_FOOTER_SLOTS = ['header-left', 'header-center', 'header-right',
                           'footer-left', 'footer-center', 'footer-right']

    def _range(self, first, last):
        total = len(self.doc)
        first = 0 if first is None else max(0, first)
        last = total - 1 if last is None else min(total - 1, last)
        return first, last

    def _place(self, page, text, band, align, size, color, font, margin):
        width = fitz.get_text_length(text, fontname=font, fontsize=size)
        if align == 'left':
            x = margin
        elif align == 'right':
            x = page.rect.width - margin - width
        else:
            x = (page.rect.width - width) / 2
        y = margin if band in ('header', 'top') else page.rect.height - margin
        self._stamp_text(page, (x, y), text, fontsize=size, fontname=font, color=color)

    def apply_header_footer(self, slots, filename='', size=10, color=(0, 0, 0),
                            font='helv', margin=36, first=None, last=None):
        if not any((slots.get(k) or '').strip() for k in self.HEADER_FOOTER_SLOTS):
            raise ValueError('Enter text for at least one header or footer position.')
        first, last = self._range(first, last)
        total = len(self.doc)
        today = datetime.date.today().isoformat()
        def expand(template, index):
            return (template.replace('<page>', str(index + 1)).replace('<pages>', str(total))
                            .replace('<date>', today).replace('<filename>', filename))
        for index in range(first, last + 1):
            page = self.doc[index]
            for slot in self.HEADER_FOOTER_SLOTS:
                template = (slots.get(slot) or '').strip()
                if not template:
                    continue
                band, align = slot.split('-')
                self._place(page, expand(template, index), band, align, size, color, font, margin)
        return last - first + 1

    BATES_POSITIONS = ['bottom-right', 'bottom-center', 'bottom-left',
                       'top-right', 'top-center', 'top-left']

    def bates_number(self, prefix='', suffix='', start=1, digits=6, position='bottom-right',
                     size=10, color=(0, 0, 0), font='helv', margin=36, first=None, last=None):
        if digits < 1:
            raise ValueError('Use at least one digit.')
        if position not in self.BATES_POSITIONS:
            raise ValueError('Unknown Bates position.')
        first, last = self._range(first, last)
        band, align = position.split('-')
        counter = start
        for index in range(first, last + 1):
            label = f'{prefix}{counter:0{digits}d}{suffix}'
            counter += 1
            self._place(self.doc[index], label, band, align, size, color, font, margin)
        return counter  # next number, for continuing the sequence across documents

    def export_images(self, folder, dpi=144):
        for i, page in enumerate(self.doc):
            page.get_pixmap(dpi=dpi).save(str(Path(folder) / f'page-{i + 1:04d}.png'))

    def export_text(self, path):
        Path(path).write_text('\n\f\n'.join(p.get_text() for p in self.doc), encoding='utf-8')

    def export_docx(self, path):
        """Reconstruct editable paragraphs, runs, tables, images and page layout."""
        from pdf2docx import Converter
        if not any(p.get_text().strip() for p in self.doc):
            raise ValueError('This PDF has no text layer. Run OCR first for editable Word text.')
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            self.save(source)
            converter = Converter(str(source))
            try:
                converter.convert(str(path), multi_processing=False)
            finally:
                converter.close()

    def export_docx_text(self, path):
        from docx import Document
        out = Document()
        for i, page in enumerate(self.doc):
            if i:
                out.add_page_break()
            for block in page.get_text('blocks', sort=True):
                if block[6] == 0:
                    out.add_paragraph(block[4])
        out.save(path)

    def compare(self, other_path, output):
        other = PDF(other_path)
        try:
            left = '\n'.join(p.get_text(sort=True) for p in self.doc).splitlines()
            right = '\n'.join(p.get_text(sort=True) for p in other.doc).splitlines()
            result = difflib.HtmlDiff(wrapcolumn=80).make_file(
                left, right, fromdesc='Current document', todesc=html.escape(Path(other_path).name))
            Path(output).write_text(result, encoding='utf-8')
        finally:
            other.close()

    def ocr(self, indices, language='eng'):
        bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'tesseract'
        executable = bundle / 'tesseract.exe'
        environment = os.environ.copy()
        if executable.exists():
            environment['TESSDATA_PREFIX'] = str(bundle / 'tessdata')
        else:
            executable = shutil.which('tesseract')
            if not executable:
                hint = ('Use the bundled Windows release or install Tesseract.' if os.name == 'nt'
                        else 'Install it with your package manager, e.g. sudo apt install tesseract-ocr.')
                raise ValueError('Tesseract is missing. ' + hint)
        # A complete temporary result prevents partial changes if OCR fails.
        out = fitz.open()
        try:
            for i in range(len(self.doc)):
                if i not in indices:
                    out.insert_pdf(self.doc, from_page=i, to_page=i)
                    continue
                pix = self.doc[i].get_pixmap(dpi=200, alpha=False)
                with tempfile.TemporaryDirectory() as folder:
                    source = Path(folder) / 'scan.png'
                    target = Path(folder) / 'result'
                    pix.save(str(source))
                    result = subprocess.run([str(executable), str(source), str(target), '-l', language, 'pdf'],
                                            capture_output=True, timeout=120, env=environment,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    if result.returncode:
                        raise ValueError(result.stderr.decode(errors='replace') or 'OCR failed.')
                    data = target.with_suffix('.pdf').read_bytes()
                with fitz.open(stream=data, filetype='pdf') as scanned:
                    out.insert_pdf(scanned)
            self.restore(out.tobytes())
        finally:
            out.close()


def images_to_pdf(paths, output):
    doc = fitz.open()
    try:
        for path in paths:
            with Image.open(path) as image:
                image = image.convert('RGB')
                data = io.BytesIO()
                image.save(data, format='PNG')
                w, h = image.size
            scale = min(595 / w, 842 / h)
            page = doc.new_page(width=w * scale, height=h * scale)
            page.insert_image(page.rect, stream=data.getvalue())
        if not len(doc):
            raise ValueError('Select at least one image.')
        doc.save(output)
    finally:
        doc.close()


def office_to_pdf(path, folder):
    executable = shutil.which('soffice')
    if not executable:
        candidate = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'LibreOffice/program/soffice.exe'
        executable = str(candidate) if candidate.exists() else None
    if not executable:
        raise ValueError('Office conversion requires LibreOffice installed separately.')
    with tempfile.TemporaryDirectory() as profile:
        result = subprocess.run([executable, '-env:UserInstallation=' + Path(profile).as_uri(),
                                 '--headless', '--convert-to', 'pdf', '--outdir', str(folder), str(path)],
                                capture_output=True, text=True, timeout=120)
    output = Path(folder) / (Path(path).stem + '.pdf')
    if result.returncode or not output.exists():
        raise ValueError(result.stderr or result.stdout or 'Conversion failed.')
    return output

