"""Editable added boxes stored as ordinary PDF text, with per-stream metadata.

Keeping metadata on its drawing stream lets PDF garbage collection renumber
objects safely. Rewrites such as redaction discard that stream and its editor
metadata together, so a later edit cannot resurrect removed text.
"""
from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache
import json
import re
import uuid
import fitz

KEY = 'OpenPDFStudioBox'
DEFAULT_STYLE = dict(family='Arial', size=18, color='#000000', bold=False,
                     italic=False, underline=False)


def new_box(rect):
    return dict(id=uuid.uuid4().hex, rect=list(rect), runs=[], fill=None,
                border=None, align='left', default=deepcopy(DEFAULT_STYLE))


def boxes(page):
    result = []
    for xref in page.get_contents():
        kind, value = page.parent.xref_get_key(xref, KEY)
        if kind != 'string':
            continue
        try:
            data = json.loads(value)
            if data.get('version') != 1 or not data.get('id'):
                continue
            data['xref'] = xref
            result.append(data)
        except (ValueError, TypeError, AttributeError):
            continue
    return result


def contents(page, refs):
    page.parent.xref_set_key(page.xref, 'Contents', '[' + ' '.join(f'{x} 0 R' for x in refs) + ']')


@contextmanager
def without_boxes(page, identifiers=None):
    original = page.get_contents()
    hidden = {b['xref'] for b in boxes(page) if identifiers is None or b['id'] in identifiers}
    try:
        if hidden:
            contents(page, [x for x in original if x not in hidden])
        yield page
    finally:
        if hidden:
            contents(page, original)


def font_name(style):
    pair = (bool(style.get('bold')), bool(style.get('italic')))
    names = {'Arial': ('helv', 'heit', 'hebo', 'hebi'),
             'Times New Roman': ('tiro', 'tiit', 'tibo', 'tibi'),
             'Courier New': ('cour', 'coit', 'cobo', 'cobi')}
    return names[style['family']][int(pair[0]) * 2 + int(pair[1])]


def rgb(value):
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)) if value else None


@lru_cache(maxsize=12)
def metric_font(name):
    return fitz.Font(name)


def layout(box, check_height=True, with_height=False):
    """Lay out styled runs without dropping overflowing text."""
    rect = fitz.Rect(box['rect'])
    width, height = rect.width - 8, rect.height - 8
    if width < 4 or height < 4:
        raise ValueError('Make the box larger before adding text.')
    fonts, chars = {}, []
    for run in box['runs']:
        style = dict(DEFAULT_STYLE, **run['style'])
        size = float(style['size'])
        if not 4 <= size <= 200:
            raise ValueError('Font size must be between 4 and 200 points.')
        name = font_name(style)
        if name not in fonts:
            fonts[name] = metric_font(name)
        font = fonts[name]
        for char in run['text'].replace('\r\n', '\n').replace('\r', '\n').replace('\t', '    '):
            if char != '\n' and (ord(char) > 255 or not font.has_glyph(ord(char))):
                raise ValueError('Added text boxes currently support Latin characters. Use a supported character.')
            chars.append(dict(char=char, style=style, font=name,
                              width=font.text_length(char, fontsize=size) if char != '\n' else 0,
                              ascent=font.ascender * size, descent=-font.descender * size))
    rows, row, used = [], [], 0.
    for char in chars:
        if char['char'] == '\n':
            rows.append((row, char))
            row = []
            used = 0.
            continue
        if char['width'] > width + .01:
            raise ValueError('The box is narrower than a letter. Widen it or reduce the font size.')
        while row and used + char['width'] > width + .01:
            split = next((i + 1 for i in range(len(row) - 1, -1, -1) if row[i]['char'].isspace()), len(row))
            rows.append((row[:split], row[0]))
            row = row[split:]
            used = sum(c['width'] for c in row)
        row.append(char)
        used += char['width']
    if row or (chars and chars[-1]['char'] == '\n'):
        rows.append((row, chars[-1]))
    y, placed = 4., []
    for row, fallback in rows:
        asc = max((c['ascent'] for c in row), default=fallback['ascent'])
        desc = max((c['descent'] for c in row), default=fallback['descent'])
        if check_height and y + asc + desc > rect.height - 4 + .01:
            raise ValueError('Text needs more height. Enlarge the box or reduce the font size; no text was discarded.')
        row_width = sum(c['width'] for c in row)
        x = 4 + (width - row_width) * {'left': 0, 'center': .5, 'right': 1}[box['align']]
        for c in row:
            placed.append((x, y + asc, c))
            x += c['width']
        y += (asc + desc) * 1.08
    required = (y - (asc + desc) * .08 + 4) if rows else 8
    return (placed, required) if with_height else placed


def fit_box(box, bounds):
    """Grow to fit without shrinking fonts; shift upward at the page edge."""
    fitted = deepcopy(box)
    bounds = fitz.Rect(bounds)
    rect = fitz.Rect(box['rect'])
    if rect.is_empty:
        raise ValueError('Draw a text box inside the page.')
    longest, word = 0., 0.
    for run in box['runs']:
        style = dict(DEFAULT_STYLE, **run['style'])
        font = metric_font(font_name(style))
        for char in run['text']:
            if char.isspace():
                longest, word = max(longest, word), 0.
            else:
                word += font.text_length(char, fontsize=style['size'])
    longest = max(longest, word)
    default = dict(DEFAULT_STYLE, **box.get('default', {}))
    minimum_width = max(12., longest + 8)
    width = min(bounds.width, max(rect.width, minimum_width))
    rect.x0 = max(bounds.x0, min(rect.x0, bounds.x1 - width))
    rect.x1 = rect.x0 + width
    rect.y1 = max(rect.y1, rect.y0 + 12)
    fitted['rect'] = list(rect)
    placed, height = layout(fitted, check_height=False, with_height=True)
    if not fitted['runs']:
        font = metric_font(font_name(default))
        height = (font.ascender - font.descender) * default['size'] + 8
    if height > bounds.height + .01 and width < bounds.width:
        rect.x0, rect.x1 = bounds.x0, bounds.x1
        fitted['rect'] = list(rect)
        placed, height = layout(fitted, check_height=False, with_height=True)
    if height > bounds.height + .01:
        raise ValueError('Text exceeds the page height even at full width. Split it into another box or page.')
    height = min(bounds.height, max(rect.height, height))
    rect.y0 = max(bounds.y0, min(rect.y0, bounds.y1 - height))
    rect.y1 = rect.y0 + height
    fitted['rect'] = list(rect)
    return fitted, placed


def put_box(doc, index, box):
    page = doc[index]
    box, placed = fit_box(box, fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height))
    rect = fitz.Rect(box['rect'])
    if rect.is_empty or not fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height).contains(rect):
        raise ValueError('Keep the text box inside the page.')
    existing = next((b for b in boxes(page) if b['id'] == box['id']), None)
    before = page.get_contents()
    with fitz.open() as source:
        canvas = source.new_page(width=rect.width, height=rect.height)
        # Even an empty transparent box needs its own drawing stream.
        canvas.draw_rect(canvas.rect + (.5, .5, -.5, -.5), color=rgb(box['border']),
                         fill=rgb(box['fill']), width=1)
        runs = []
        for x, y, char in placed:
            if runs and runs[-1]['style'] == char['style'] and runs[-1]['y'] == y:
                runs[-1]['text'] += char['char']
                runs[-1]['width'] += char['width']
            else:
                runs.append(dict(x=x, y=y, text=char['char'], style=char['style'], font=char['font'], width=char['width']))
        shape = canvas.new_shape()
        for run in runs:
            x, y, char = run['x'], run['y'], run
            s = char['style']
            shape.insert_text((x, y), char['text'], fontname=char['font'],
                               fontsize=s['size'], color=rgb(s['color']))
            if s.get('underline'):
                shape.draw_line((x, y + s['size'] * .1), (x + char['width'], y + s['size'] * .1))
                shape.finish(color=rgb(s['color']), width=max(.5, s['size'] / 18))
        shape.commit()
        page.show_pdf_page(rect, source, 0)
    new_refs = [x for x in page.get_contents() if x not in before]
    if len(new_refs) != 1:
        raise RuntimeError('Could not create an independent text box.')
    stream = new_refs[0]
    data = {k: v for k, v in box.items() if k != 'xref'}
    data['version'] = 1
    doc.xref_set_key(stream, KEY, fitz.get_pdf_str(json.dumps(data, ensure_ascii=True)))
    if existing:
        contents(page, [stream if x == existing['xref'] else x for x in before])
    return stream


def delete_box(doc, index, identifier):
    page = doc[index]
    target = next((b for b in boxes(page) if b['id'] == identifier), None)
    if target:
        contents(page, [x for x in page.get_contents() if x != target['xref']])


_REF = r'(\d+) \d+ R'
_OBJR = re.compile(r'<<(?:(?!<<|>>).)*?/Obj (\d+) \d+ R(?:(?!<<|>>).)*?>>', re.S)


def _ref(doc, xref, key):
    """The object number a key refers to, or 0."""
    kind, value = doc.xref_get_key(xref, key)
    return int(value.split()[0]) if kind == 'xref' else 0


def _drop_refs(doc, xref, key, targets):
    """Remove references to targets from an array (direct or indirect); return how many stay.

    The array text is edited in place, so entries of any generation survive.
    """
    kind, value = doc.xref_get_key(xref, key)
    if kind == 'xref':
        xref, key = int(value.split()[0]), None
        value = doc.xref_object(xref, compressed=True)
    elif kind != 'array':
        return 0
    array = re.sub(_REF, lambda m: '' if int(m.group(1)) in targets else m.group(0), value)
    if array != value:
        if key is None:
            doc.update_object(xref, array)
        else:
            doc.xref_set_key(xref, key, array)
    return len(re.findall(_REF, array))


def _form(doc):
    """Where the AcroForm dictionary lives: (xref, key prefix)."""
    form = _ref(doc, doc.pdf_catalog(), 'AcroForm')
    return (form, '') if form else (doc.pdf_catalog(), 'AcroForm/')


def _drop_empty_fields(doc, parents, gone):
    """A field whose last widget is gone keeps its value on the parent: unlink it."""
    form, prefix = _form(doc)
    while parents:
        above = {}
        for parent, kids in parents.items():
            if _drop_refs(doc, parent, 'Kids', kids):
                continue
            higher = _ref(doc, parent, 'Parent')
            if higher:
                above.setdefault(higher, set()).add(parent)
            else:
                _drop_refs(doc, form, prefix + 'Fields', {parent})
            gone.add(parent)
        parents = above


def _drop_struct_refs(doc, gone):
    """Tagged PDF: structure elements point at fields and comments through OBJR dictionaries."""
    if doc.xref_get_key(doc.pdf_catalog(), 'StructTreeRoot')[0] == 'null':
        return
    for xref in range(1, doc.xref_length()):
        if doc.xref_is_stream(xref):
            continue
        text = doc.xref_object(xref, compressed=True)
        if '/Obj ' not in text:
            continue
        new = _OBJR.sub(lambda m: ' null ' if int(m.group(1)) in gone else m.group(0), text)
        if new != text:
            doc.update_object(xref, new)


def _numbers(doc, xref, key):
    """The numbers of an array-valued key as stored; [] when missing or built from other objects."""
    kind, value = doc.xref_get_key(xref, key)
    if kind == 'xref':
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    return [] if 'R' in value else [float(n) for n in re.findall(r'-?\d*\.?\d+', value)]


def _pdf_rect(doc, xref):
    """An annotation's /Rect as stored (PDF coordinates), without loading the annotation."""
    numbers = _numbers(doc, xref, 'Rect')
    return fitz.Rect(numbers).normalize() if len(numbers) == 4 else None


_UNDRAWN_MARGIN = 20  # points: a note icon, a line ending or a pen width beyond the stored geometry


def _footprint(doc, xref, rect):
    """Where a comment with no appearance stream may show: each viewer draws it from its geometry."""
    xs, ys = ([rect.x0, rect.x1], [rect.y0, rect.y1]) if rect is not None else ([], [])
    for key in ('L', 'Vertices', 'QuadPoints', 'InkList', 'CL'):
        numbers = _numbers(doc, xref, key)
        if len(numbers) % 2 == 0:
            xs, ys = xs + numbers[0::2], ys + numbers[1::2]
    if not xs:
        return None
    margin = _UNDRAWN_MARGIN
    return fitz.Rect(min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


def _touches(rect, areas):
    """Whether a rectangle is under an area. One with no area (a point, a line) is, when it lies in one."""
    if rect.is_empty:
        return any(a.x0 <= rect.x1 and rect.x0 <= a.x1 and a.y0 <= rect.y1 and rect.y0 <= a.y1 for a in areas)
    return any(rect.intersects(a) for a in areas)


def _unseen_widget(doc, xref):
    """A widget (not a child field) that shows nothing: hidden, or with no area."""
    try:
        if doc.xref_get_key(xref, 'Subtype')[1] != '/Widget' or doc.xref_get_key(xref, 'T')[0] != 'null':
            return False
        flags, rect = doc.xref_get_key(xref, 'F')[1], _pdf_rect(doc, xref)
    except (ValueError, RuntimeError):  # a reference to an object that is not there
        return False
    return rect is None or rect.is_empty or (flags.isdigit() and bool(int(flags) & 34))  # Hidden, NoView


def _unlist(doc, xref):
    """Take an annotation out of the /Annots of its page, wherever that page is."""
    owner = _ref(doc, xref, 'P')
    for page in [owner] if owner else [doc.page_xref(n) for n in range(doc.page_count)]:
        _drop_refs(doc, page, 'Annots', {xref})


def purge_overlays(page):
    """Delete form fields and comments under the page's pending redactions.

    MuPDF redaction rewrites the content stream only, so a filled field or a
    note inside a redacted area would survive the export. Call this before
    apply_redactions on a user-facing redaction, not when a redact annotation
    is only a way to delete glyphs for a text edit.

    Returns (form fields removed, comments removed).
    """
    doc = page.parent
    WIDGET, POPUP, LINK, REDACT = fitz.PDF_ANNOT_WIDGET, fitz.PDF_ANNOT_POPUP, fitz.PDF_ANNOT_LINK, fitz.PDF_ANNOT_REDACT
    listed = [(xref, kind) for xref, kind, _id in page.annot_xrefs()]
    rects = {xref: _pdf_rect(doc, xref) for xref, _kind in listed}
    areas = [rects[xref] for xref, kind in listed if kind == REDACT and rects[xref]]
    shown_areas = []

    def under(xref, kind):
        rect = rects[xref]
        if rect is not None and _touches(rect, areas):
            return True
        if kind in (POPUP, LINK):  # never drawn here, so the stored rectangle is all there is
            return False
        # Stored rectangles share one coordinate space, so few objects need loading.
        if kind != WIDGET and doc.xref_get_key(xref, 'AP')[0] not in ('dict', 'xref'):
            # No appearance stream: the viewer draws the comment from its geometry (a note
            # icon around a point, a line from /L), which can lie outside the stored rectangle.
            box = _footprint(doc, xref, rect)
            if box is not None:
                return _touches(box, areas)
        # Left to load: a rectangle that cannot be read, and one flagged NoRotate (a note
        # icon), which is drawn upright and so elsewhere on a rotated page.
        flags = doc.xref_get_key(xref, 'F')[1] if page.rotation else ''
        if kind is None or (rect is not None and not (flags.isdigit() and int(flags) & 16)):
            return False
        if not shown_areas:
            shown_areas.extend(a.rect for a in page.annots(types=[REDACT]))
        try:
            shown = (page.load_widget if kind == WIDGET else page.load_annot)(xref).rect
        except Exception:  # MuPDF cannot show it either
            return False
        if shown.x0 > shown.x1 or shown.y0 > shown.y1:  # MuPDF's answer for "no rectangle": nowhere
            return False
        return _touches(shown, shown_areas)

    widgets = [xref for xref, kind in listed if kind == WIDGET and under(xref, kind)]
    # MuPDF's own redaction removes a link under the area, but not one whose rectangle is a point.
    links = {xref for xref, kind in listed if kind == LINK and rects[xref] is not None
             and rects[xref].is_empty and under(xref, kind)}
    notes = [xref for xref, kind in listed if kind not in (WIDGET, POPUP, LINK, REDACT)]
    popups = {xref for xref, kind in listed if kind == POPUP}
    irt = {xref: _ref(doc, xref, 'IRT') for xref in notes}
    popup = {xref: _ref(doc, xref, 'Popup') for xref in notes}
    # Comments MuPDF does not list: /Annots entries of a subtype it does not know, and
    # objects that only the /IRT or /Popup of a comment leads to.
    kind, array = doc.xref_get_key(page.xref, 'Annots')
    if kind == 'xref':
        array = doc.xref_object(int(array.split()[0]), compressed=True)
    editable = '<<' not in array  # a dictionary written inside the array has no number to go by
    known = {xref for xref, _kind in listed}
    loose = set()
    queue = [int(n) for n in re.findall(_REF, array)] if editable else []
    queue += [*irt.values(), *popup.values()]
    while queue:
        xref = queue.pop()
        if xref in known or xref in loose or not 0 < xref < doc.xref_length():
            continue
        try:
            rects[xref], subtype = _pdf_rect(doc, xref), doc.xref_get_key(xref, 'Subtype')[1]
            irt[xref], popup[xref] = _ref(doc, xref, 'IRT'), _ref(doc, xref, 'Popup')
        except (ValueError, RuntimeError):  # not a dictionary
            continue
        loose.add(xref)
        if subtype == '/Popup':
            popups.add(xref)
        queue += [irt[xref], popup[xref]]
    kinds = dict(listed)
    doomed = {xref for xref in notes if under(xref, kinds[xref])} | {xref for xref in loose - popups if under(xref, None)}
    while True:  # replies go with the comment they answer
        more = {xref for xref, target in irt.items() if target in doomed} - doomed
        if not more:
            break
        doomed |= more
    # A popup is the window of its comment. It goes with the comment, and on its own when
    # the window is what lies under the area: the comment then stays, without a window.
    gone = {xref for xref in popups if under(xref, POPUP)} | links
    if doomed:
        gone |= {popup[xref] for xref in doomed} | {xref for xref in popups if _ref(doc, xref, 'Parent') in doomed}
    gone.discard(0)
    if not widgets and not doomed and not gone:
        return 0, 0
    gone |= set(widgets) | doomed
    parents = {}
    # PyMuPDF deletes the direct replies with a comment, rescans the page's
    # comments on every delete, and loads the widget after the one deleted. So:
    # replies before what they answer, comments before widgets, widgets last to first.
    def depth(xref, limit=len(doomed)):
        steps = 0
        while irt.get(xref) in doomed and steps < limit:
            xref, steps = irt[xref], steps + 1
        return steps
    for xref in sorted(doomed & set(notes), key=depth, reverse=True):
        try:
            annot = page.load_annot(xref)
        except Exception:  # already deleted, as the reply of another
            continue
        page.delete_annot(annot)
    for xref in reversed(widgets):
        parent = _ref(doc, xref, 'Parent')
        if parent:
            parents.setdefault(parent, set()).add(xref)
        page.delete_widget(page.load_widget(xref))  # also unlinks a field with no parent
    if editable:  # what MuPDF could not delete: unknown subtypes, popups, links
        _drop_refs(doc, page.xref, 'Annots', gone)
    for owner, window in popup.items():
        if window in gone and owner not in gone:
            doc.xref_set_key(owner, 'Popup', 'null')
    if widgets:
        form, prefix = _form(doc)
        for parent, kids in parents.items():
            # The value sits on the parent. Widgets left that show nothing would keep it in the
            # file unseen, so they go too; a widget still visible elsewhere keeps the field.
            kind, array = doc.xref_get_key(parent, 'Kids')
            rest = {int(n) for n in re.findall(_REF, array)} - kids if kind == 'array' else set()
            if rest and all(_unseen_widget(doc, kid) for kid in rest):
                for kid in rest:
                    _unlist(doc, kid)
                kids |= rest
                gone |= rest
        _drop_empty_fields(doc, parents, gone)
        _drop_refs(doc, form, prefix + 'CO', gone)
        doc.xref_set_key(form, prefix + 'XFA', 'null')  # XFA keeps its own copy of every value
    _drop_struct_refs(doc, gone)
    # Anything else that still refers to these objects must find them empty,
    # or save() would write the value out again.
    for xref in gone:
        doc.update_object(xref, '<<>>')
    return len(widgets), len(doomed)


def apply_redactions(page, preserve_native=False, **options):
    """Preserve untouched boxes; remove metadata for any box being redacted."""
    areas = [a.rect for a in page.annots() or [] if a.type[0] == fitz.PDF_ANNOT_REDACT]
    from native_layers import layers, KEY as NATIVE_KEY, restore_font_resource
    native = []
    fonts = {font[4]: font[0] for font in page.get_fonts(full=True) if not font[-1]}
    for section in layers(page):
        if not preserve_native and any(fitz.Rect(section['rect']).intersects(r) for r in areas):
            page.parent.xref_set_key(section['_stream'], NATIVE_KEY, 'null')
        else:
            native.append(section)
    detached = []
    for box in boxes(page):
        if not preserve_native and any(fitz.Rect(box['rect']).intersects(rect) for rect in areas):
            page.parent.xref_set_key(box['xref'], KEY, 'null')
        else:
            detached.append(box)
    refs = page.get_contents()
    if detached or native:
        hidden = {b['xref'] for b in detached} | {s['_stream'] for s in native}
        contents(page, [x for x in refs if x not in hidden])
    result = page.apply_redactions(**options)
    for section in native:
        name = section['_font_resource']
        restore_font_resource(page, name, fonts[name])
    if native:
        contents(page, page.get_contents() + [s['_stream'] for s in native])
    # MuPDF removes unused resources during redaction. Rebuild detached boxes
    # instead of restoring dangling references to their former Form XObjects.
    for box in detached:
        put_box(page.parent, page.number, box)
    return result


def move_page(doc, source, target):
    """Move to a final zero-based index, retaining the actual page object."""
    if not 0 <= source < len(doc) or not 0 <= target < len(doc):
        raise ValueError('Page position is outside the document.')
    if source != target:
        doc.move_page(source, target if target < source else (-1 if target == len(doc) - 1 else target + 1))

