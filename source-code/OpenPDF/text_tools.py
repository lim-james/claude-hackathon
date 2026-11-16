"""Character-level selection and style-preserving replacement."""
import gc
import io
import re
import time
import hashlib
from array import array
from bisect import bisect_right
from collections import deque
from itertools import chain, groupby
from operator import ge, itemgetter
import fitz
from rich_boxes import apply_redactions


def lines(page):
    result = []
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            chars = []
            for span in line['spans']:
                style = {k: v for k, v in span.items() if k != 'chars'}
                for char in span['chars']:
                    chars.append(dict(char, style=style))
            if chars:
                result.append({'chars': chars, 'dir': line['dir'], 'bbox': line['bbox']})
    return result


_CHAR, _BBOX, _ORIGIN = itemgetter('c'), itemgetter('bbox'), itemgetter('origin')


class SearchLine:
    """One text line packed for searching.

    `lines()` keeps a dictionary per character, far too much to hold for a
    whole document. This keeps the line's text as one string and the character
    boxes and origins as flat arrays, and rebuilds the per-character
    dictionaries only for the characters of a match.
    """
    __slots__ = ('text', 'boxes', 'origins', 'starts', 'spans', 'dir')

    def __init__(self, spans, direction):
        """`spans`: (style, chars) pairs, chars being rawdict character dictionaries."""
        self.starts, self.spans, self.dir, line = [], [], direction, []
        for style, chars in spans:
            if not chars:
                continue
            fixed, varying = {'style': style}, []
            for key in chars[0]:
                if key not in ('c', 'bbox', 'origin', 'style'):
                    values = list(map(itemgetter(key), chars))
                    if values.count(values[0]) == len(values):
                        fixed[key] = values[0]
                    else:
                        varying.append((key, values))
            self.starts.append(len(line))
            self.spans.append((fixed, varying))
            line += chars
        self.text = ''.join(map(_CHAR, line))
        self.boxes = array('d', list(chain.from_iterable(map(_BBOX, line))))
        self.origins = array('d', list(chain.from_iterable(map(_ORIGIN, line))))

    def chars(self, start, end):
        """The characters start..end as `lines()` would give them."""
        origins, boxes = iter(self.origins[2 * start:2 * end].tolist()), iter(self.boxes[4 * start:4 * end].tolist())
        places = zip(zip(origins, origins), zip(boxes, boxes, boxes, boxes), self.text[start:end])
        span = bisect_right(self.starts, start) - 1
        fixed, varying = self.spans[span]
        if not varying and (span + 1 == len(self.starts) or self.starts[span + 1] >= end):
            return [{'origin': origin, 'bbox': bbox, 'c': c, **fixed} for origin, bbox, c in places]
        result = []
        for n, (origin, bbox, c) in enumerate(places, start):
            while span + 1 < len(self.starts) and self.starts[span + 1] <= n:
                span += 1
            fixed, varying = self.spans[span]
            char = {'origin': origin, 'bbox': bbox, 'c': c, **fixed}
            for key, values in varying:
                char[key] = values[n - self.starts[span]]
            result.append(char)
        return result


def _union(boxes):
    """Union of character boxes (a flat x0, y0, x1, y1, ... list), as repeated
    `fitz.Rect |= box` gives it: empty boxes add nothing, an empty union is replaced."""
    if not (any(map(ge, boxes[0::4], boxes[2::4])) or any(map(ge, boxes[1::4], boxes[3::4]))):
        return min(boxes[0::4]), min(boxes[1::4]), max(boxes[2::4]), max(boxes[3::4])
    x0, y0, x1, y1 = boxes[:4]
    for n in range(4, len(boxes), 4):
        a, b, c, d = boxes[n:n + 4]
        if a >= c or b >= d:
            continue
        if x0 >= x1 or y0 >= y1:
            x0, y0, x1, y1 = a, b, c, d
        else:
            x0, y0, x1, y1 = min(x0, a), min(y0, b), max(x1, c), max(y1, d)
    return x0, y0, x1, y1


def search_lines(page):
    """The page's text lines as `SearchLine`s; `lines()` without a dictionary per character."""
    result = []
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines', []):
            packed = SearchLine((({k: v for k, v in span.items() if k != 'chars'}, span['chars'])
                                 for span in line['spans']), line['dir'])
            if packed.text:
                result.append(packed)
    return result


def find_matches(doc, query, page_indices=None, line_provider=None):
    """Matches of `query`, page by page. `line_provider(index)` may return a
    page's lines as `search_lines()` or as `lines()` gives them."""
    if not query:
        return []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    matches = []
    # Extraction and match building allocate a dictionary per character, none
    # of it cyclic. The collector's passes over it roughly doubled the time to
    # build 9,200 matches (#43), so it waits until the search returns.
    collecting = gc.isenabled()
    gc.disable()
    try:
        _find(doc, pattern, page_indices, line_provider, matches)
    finally:
        if collecting:
            gc.enable()
    return matches


def _find(doc, pattern, page_indices, line_provider, matches):
    for index in (range(len(doc)) if page_indices is None else page_indices):
        page = doc[index]
        edge = None
        for line in (line_provider(index) if line_provider else search_lines(page)):
            if isinstance(line, dict):
                groups = groupby(line['chars'], key=lambda c: id(c['style']))
                line = SearchLine(((chars[0]['style'], chars) for chars in (list(g) for _, g in groups)), line['dir'])
            text, boxes = line.text, line.boxes
            for found in pattern.finditer(text):
                start, end = found.span()
                x0, y0, x1, y1 = _union(boxes[4 * start:4 * end].tolist())
                following = end
                while following < len(text) and text[following].isspace():
                    following += 1
                if following < len(text):
                    limit = boxes[4 * following]
                else:
                    if edge is None:
                        edge = page.cropbox.width - 12
                    limit = edge
                chars = line.chars(start, end)
                matches.append({'page': index, 'rect': (x0, y0, x1, y1), 'text': found.group(),
                                'chars': chars, 'style': chars[0]['style'],
                                'origin': chars[0]['origin'], 'dir': line.dir,
                                'right_limit': max(x1, limit), 'context': text})


class SlicedSearch:
    """Search page by page in short slices so the caller's event loop stays live.

    `schedule(callback)` queues the next slice (in Tk, an `after` call). Starting
    or cancelling a search makes slices still queued for an older one do nothing.
    """

    def __init__(self, schedule, budget=0.05, clock=time.perf_counter):
        self.schedule, self.budget, self.clock = schedule, budget, clock
        self.generation = 0
        self.pending, self.found = deque(), {}

    @property
    def running(self):
        return bool(self.pending)

    def start(self, pages, search_page, report):
        """Search `pages` in the given order. `search_page(index)` returns one
        page's matches; `report(matches, done)` gets everything found so far,
        in page order, after each slice."""
        self.generation += 1
        generation = self.generation
        self.pending, self.found = deque(pages), {}

        def tick():
            if generation != self.generation:
                return
            deadline = self.clock() + self.budget
            while self.pending:
                index = self.pending.popleft()
                self.found[index] = search_page(index)
                if self.clock() >= deadline:
                    break
            report([m for index in sorted(self.found) for m in self.found[index]], not self.pending)
            if self.pending and generation == self.generation:
                self.schedule(tick)
        if self.pending:
            self.schedule(tick)
        else:
            report([], True)

    def redo(self, pages):
        """Search these pages again, next, in the search that is running."""
        for index in sorted(pages, reverse=True):
            self.found.pop(index, None)
            if index in self.pending:
                self.pending.remove(index)
            self.pending.appendleft(index)

    def cancel(self):
        self.generation += 1
        self.pending, self.found = deque(), {}


def select_text(page, start, end, all_lines=None):
    """Return quads for a reading-order range between two page points."""
    all_lines = lines(page) if all_lines is None else all_lines
    flattened = [(i, c) for i, line in enumerate(all_lines) for c in line['chars']]
    if not flattened:
        return [], ''
    def closest(point):
        def distance(item):
            rect = fitz.Rect(item[1]['bbox'])
            dx = max(rect.x0 - point.x, 0, point.x - rect.x1)
            dy = max(rect.y0 - point.y, 0, point.y - rect.y1)
            return dx * dx + dy * dy
        return min(range(len(flattened)), key=lambda n: distance(flattened[n]))
    a, b = sorted((closest(fitz.Point(start)), closest(fitz.Point(end))))
    groups = {}
    for line_id, char in flattened[a:b + 1]:
        groups.setdefault(line_id, []).append(char)
    quads, text = [], []
    for line_id, chars in groups.items():
        line = all_lines[line_id]
        # Character quads retain the baseline orientation, including rotated text.
        if abs(line['dir'][1]) < 0.001 and line['dir'][0] > 0:
            rect = fitz.Rect(chars[0]['bbox'])
            for c in chars[1:]:
                rect |= fitz.Rect(c['bbox'])
            quads.append(rect.quad)
        else:
            for char in chars:
                quads.append(fitz.recover_char_quad(line['dir'], char['style'], char))
        text.append(''.join(c['c'] for c in chars))
    return quads, '\n'.join(text)


def font_for(doc, page, style, text):
    """Reuse embedded font outlines; repair subsets lacking a Unicode cmap."""
    name = style['font']
    def plain(value):
        return value.split('+')[-1].replace(' ', '').lower()
    for xref, ext, kind, base, resource, *_ in page.get_fonts(full=True):
        if plain(base) != plain(name):
            continue
        data = doc.extract_font(xref)[3]
        if data:
            cache = getattr(doc, '_opdf_fonts', None)
            if cache is None:
                cache = doc._opdf_fonts = {}
            key = (plain(name), hashlib.sha256(data).digest())
            cached = cache.get(key)
            if cached and all(cached[0].has_glyph(ord(c)) for c in set(text)):
                return cached
            font = fitz.Font(fontbuffer=data)
            if not all(font.has_glyph(ord(c)) for c in text):
                # ReportLab subsets often carry only a Mac cmap. PDF text traces
                # supply the actual Unicode -> glyph-id mapping without guessing.
                from fontTools.ttLib import TTFont
                from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
                tt = TTFont(io.BytesIO(data))
                mapping = dict(tt.getBestCmap() or {})
                glyph_count = len(tt.getGlyphOrder())
                for source_page in doc:
                    for trace in source_page.get_texttrace():
                        if plain(trace['font']) == plain(name):
                            for unicode, gid, *_ in trace['chars']:
                                if 0 <= unicode <= 0xFFFF and 0 < gid < glyph_count:
                                    mapping[unicode] = tt.getGlyphName(gid)
                # Some PDF subsets have a Macintosh-only cmap. Recover its
                # ASCII glyphs after verifying it agrees with observed PDF text.
                # Do not guess the custom high-byte encoding used by subsets.
                for table in tt['cmap'].tables:
                    if table.platformID == 1 and table.platEncID == 0:
                        ascii_map = {code: glyph for code, glyph in table.cmap.items() if 32 <= code < 127}
                        shared = set(ascii_map) & set(mapping)
                        if shared and all(ascii_map[c] == mapping[c] for c in shared):
                            for code, glyph in ascii_map.items():
                                mapping.setdefault(code, glyph)
                cmap = CmapSubtable.newSubtable(4)
                cmap.platformID, cmap.platEncID, cmap.language = 3, 1, 0
                cmap.cmap = mapping
                tt['cmap'].tables = [t for t in tt['cmap'].tables if not (t.platformID == 3 and t.platEncID == 1)] + [cmap]
                buffer = io.BytesIO()
                tt.save(buffer)
                tt.close()
                data = buffer.getvalue()
                font = fitz.Font(fontbuffer=data)
            missing = sorted(set(c for c in text if not font.has_glyph(ord(c))))
            if missing:
                raise ValueError('The embedded font lacks these replacement characters: ' + ''.join(missing) +
                                 '. Choose text supported by the font. The document was not changed.')
            cache[key] = (font, data, None)
            if len(cache) > 32:
                del cache[next(iter(cache))]
            return cache[key]
        aliases = {'helvetica': 'helv', 'helvetica-bold': 'hebo', 'helvetica-oblique': 'heit',
                   'helvetica-boldoblique': 'hebi', 'times-roman': 'tiro', 'times-bold': 'tibo',
                   'times-italic': 'tiit', 'times-bolditalic': 'tibi', 'courier': 'cour',
                   'courier-bold': 'cobo', 'courier-oblique': 'coit', 'courier-boldoblique': 'cobi'}
        alias = aliases.get(plain(base))
        if alias:
            font = fitz.Font(alias)
            if all(ord(c) <= 255 and font.has_glyph(ord(c)) for c in text):
                return font, None, alias
    raise ValueError(f'Cannot safely reuse font {name}. No font substitution was made.')


def removal_rects(chars):
    """Merge adjacent glyph bounds on one baseline, never across gaps/rows.

    Hundreds of per-character redaction annotations make MuPDF's annotation
    lookup quadratic. Contiguous line segments remove the same glyphs with
    usually one annotation per line.
    """
    segments = []
    for char in sorted(chars, key=lambda c: (round(c['origin'][1], 1), c['bbox'][0])):
        rect = fitz.Rect(char['bbox'])
        if rect.width <= .2 or rect.height <= .2:
            continue
        baseline = char['origin'][1]
        if segments and abs(segments[-1][0] - baseline) < .1 and rect.x0 <= segments[-1][1].x1 + .1:
            segments[-1][1] |= rect
        else:
            segments.append([baseline, rect])
    return [rect + (.05, .05, -.05, -.05) for _, rect in segments]


def replace_match(doc, match, text):
    page = doc[match['page']]
    style = match['style']
    if tuple(match['dir']) != (1.0, 0.0):
        raise ValueError('Replacement currently supports horizontal text, including rotated PDF pages.')
    if '\n' in text or '\r' in text:
        raise ValueError('Use a single-line replacement.')
    if any(any(c['style'].get(k) != style.get(k) for k in ('font', 'size', 'color', 'alpha')) for c in match['chars']):
        raise ValueError('This match spans multiple text styles. Select a shorter match within one style.')
    if any(a.type[0] == fitz.PDF_ANNOT_REDACT for a in page.annots() or []):
        raise ValueError('Apply or remove existing redaction annotations before replacing text.')
    font, data, alias = font_for(doc, page, style, text) if text else (None, None, None)
    if text:
        width = font.text_length(text, fontsize=style['size'])
        if match['origin'][0] + width > match['right_limit'] + 0.25:
            raise ValueError('Replacement would overlap following text or the page edge at the original font size. '
                             'Use shorter text; the font size will not be silently changed.')
        if data:
            alias = 'OPDF' + str(doc.xref_length())
    # Transparent redaction removes only text; keep image/vector backgrounds.
    for r in removal_rects(match['chars']):
        page.add_redact_annot(r, fill=False, cross_out=False)
    apply_redactions(page, images=0, graphics=0, text=0)
    if text:
        if data:
            page.insert_font(fontname=alias, fontbuffer=data)
        color = fitz.sRGB_to_pdf(style['color'])
        page.insert_text(match['origin'], text, fontname=alias, fontsize=style['size'],
                         color=color, fill_opacity=style.get('alpha', 255) / 255)


