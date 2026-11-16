"""Add bookmarks without rewriting the ones a PDF already has.

``Document.set_toc`` deletes the whole outline and rebuilds it from what
``get_toc`` returned, which loses web-link actions, colours and zoom, and moves
destinations on pages with an offset crop or media box. Here new outline
objects are appended and linked in; existing objects keep their content.
"""
import re
import fitz

# The part of a destination after its page: a fit mode and its numbers.
_VIEW = re.compile(r'/(XYZ|FitBH|FitBV|FitB|FitH|FitV|FitR|Fit)((?: (?:null|-?\d+\.?\d*|-?\.\d+)){0,4})$')


def _ref(doc, xref, key):
    kind, value = doc.xref_get_key(xref, key)
    return int(value.split()[0]) if kind == 'xref' else 0


def _root(doc):
    """Xref of the outline root, created when the document has none."""
    catalog = doc.pdf_catalog()
    kind, value = doc.xref_get_key(catalog, 'Outlines')
    if kind == 'xref':
        return int(value.split()[0])
    root = doc.get_new_xref()
    # A root written inline in the catalog is moved to its own object so the
    # new items can name it as their parent.
    doc.update_object(root, value if kind == 'dict' else '<</Type/Outlines>>')
    doc.xref_set_key(catalog, 'Outlines', f'{root} 0 R')
    return root


def _last_top_item(doc, root):
    """Last level-1 item, following /Next when /Last is missing or stale."""
    item = _ref(doc, root, 'Last') or _ref(doc, root, 'First')
    seen = set()
    while item and item not in seen:
        seen.add(item)
        following = _ref(doc, item, 'Next')
        if not following or following in seen:
            return item
        item = following
    return 0


def _page_to_pdf(page):
    """Matrix from the page as shown (rotated, cropped, top-left origin) to PDF
    space. It is MuPDF's own page transform, the one rendering uses;
    ``page.transformation_matrix`` drops the crop box offset on rotated pages.
    """
    ctm = fitz.mupdf.FzMatrix()
    fitz.mupdf.pdf_page_transform(fitz.mupdf.pdf_page_from_fz_page(page.this), fitz.mupdf.FzRect(), ctm)
    return ~fitz.Matrix(ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f)


def _destination(doc, page_number, point):
    """GoTo action for a point measured from the top-left of the page as shown."""
    page = doc[page_number]
    target = fitz.Point(point) * _page_to_pdf(page)
    return f'/A<</S/GoTo/D[{page.xref} 0 R/XYZ {target.x:g} {target.y:g} 0]>>'


def _target(doc, page, options):
    """What a new item opens and how it is drawn, as PDF dictionary entries."""
    text = ''
    if options.get('uri'):
        text = f"/A<</S/URI/URI{fitz.get_pdf_str(str(options['uri']))}>>"
    elif page != -1 and options.get('view'):
        text = f"/A<</S/GoTo/D[{doc.page_xref(page - 1)} 0 R{options['view']}]>>"
    elif page != -1:
        text = _destination(doc, page - 1, options.get('point') or (72, 36))
    if options.get('color'):
        red, green, blue = (min(1.0, max(0.0, float(part))) for part in options['color'])
        text += f'/C[{red:g} {green:g} {blue:g}]'
    flags = (1 if options.get('italic') else 0) | (2 if options.get('bold') else 0)
    return text + (f'/F {flags}' if flags else '')


def _view(text):
    """``/XYZ 72 500 0`` from the tail of a destination array, or '' when it is anything else."""
    tokens = ' '.join(re.findall(r'/[^\s/\[\]]+|[^\s/\[\]]+', text))
    return tokens if _VIEW.match(tokens) else ''


def _source_destination(doc, xref, pages, names):
    """(0-based page, view) an outline item of ``doc`` jumps to, or None."""
    kind, value = doc.xref_get_key(xref, 'Dest')
    if kind == 'null' and doc.xref_get_key(xref, 'A/S') == ('name', '/GoTo'):
        kind, value = doc.xref_get_key(xref, 'A/D')
    if kind == 'xref':  # the destination is an object of its own
        value = doc.xref_object(int(value.split()[0]), compressed=True)
        kind = 'array' if value.startswith('[') else 'dict'
    if kind == 'dict':  # a named destination's dictionary form
        match = re.search(r'/D\s*(\[[^\]]*\])', value)
        kind, value = ('array', match.group(1)) if match else ('null', '')
    if kind == 'array':
        match = re.match(r'\[\s*(\d+) 0 R(.*)\]$', value, re.S)
        if match and int(match.group(1)) in pages:
            return pages[int(match.group(1))], _view(match.group(2))
    elif kind in ('name', 'string'):
        found = names().get(value[1:] if kind == 'name' else value)
        if found and isinstance(found.get('page'), int) and 0 <= found['page'] < doc.page_count:
            if 'to' in found:
                x, y = found['to']
                return found['page'], _view(f"/XYZ {x:g} {y:g} {found.get('zoom', 0):g}")
            return found['page'], _view(found.get('dest', ''))
    return None


def copied_bookmarks(source, first_page):
    """Entries for ``append_bookmarks`` that repeat the outline of ``source``.

    For a document whose pages were inserted, in order and unchanged, starting
    at 0-based page ``first_page`` of another one. A bookmark keeps its title,
    level, colour, bold and italic. One that jumps to a page keeps the exact
    position and zoom: the view is copied as written, which is right because
    the inserted page has the same boxes and rotation. Web links stay web
    links. A bookmark that does anything else (another file, a script, a
    destination that cannot be read) is kept as a title that opens nothing, or
    the top of its page when the page is known.
    """
    pages = {source.page_xref(number): number for number in range(source.page_count)}
    cache = []

    def names():
        if not cache:
            cache.append(source.resolve_names())
        return cache[0]

    entries = []
    for level, title, _, info in source.get_toc(simple=False):
        options = {key: info[key] for key in ('color', 'bold', 'italic') if info.get(key)}
        page = -1
        xref = info.get('xref', 0)
        if xref and source.xref_get_key(xref, 'A/S') == ('name', '/URI') and info.get('uri'):
            options['uri'] = info['uri']
        elif xref:
            found = _source_destination(source, xref, pages, names)
            if found:
                page, options['view'] = first_page + found[0] + 1, found[1]
            else:
                known = info.get('page')
                if (info.get('kind') in (fitz.LINK_GOTO, fitz.LINK_NAMED) and isinstance(known, int)
                        and 0 <= known < source.page_count):
                    page = first_page + known + 1
        entries.append([level, title, page, options])
    return entries


def append_bookmarks(doc, entries):
    """Append bookmarks after the existing ones and return how many were added.

    ``entries`` is a list in ``get_toc`` order: ``[level, title, page]`` with a
    1-based page, optionally followed by an ``(x, y)`` point on that page
    (default: the top). The first entry must be level 1 and a level may only
    go one deeper than the entry before it; deeper entries become children of
    the appended entry above them and start collapsed.

    Instead of a point the fourth item may be a dict: ``point`` as above;
    ``view``, the destination after its page exactly as the PDF stores it
    (``/XYZ 72 500 2``, ``/FitH 300``), in the page's own coordinates; ``uri``
    for a web link; ``color`` as (red, green, blue) from 0 to 1; ``bold`` and
    ``italic``. Page -1 makes a bookmark that opens no page.
    """
    entries = [list(entry) for entry in entries]
    if not entries:
        return 0
    previous = 0
    for level, title, page, *rest in entries:
        if not isinstance(level, int) or not 1 <= level <= previous + 1:
            raise ValueError(f'Bookmark "{title}" has level {level} after level {previous}.')
        if not isinstance(page, int) or not (page == -1 or 1 <= page <= doc.page_count):
            raise ValueError(f'Bookmark "{title}" points to page {page} of {doc.page_count}.')
        options = rest[0] if rest and isinstance(rest[0], dict) else {}
        if options.get('view') and not _VIEW.match(options['view']):
            raise ValueError(f'Bookmark "{title}" has an unreadable destination.')
        previous = level
    root = _root(doc)
    old_last = _last_top_item(doc, root)
    nodes = [{'xref': root, 'first': 0, 'last': 0, 'count': 0}]
    by_xref = {}
    newest = {0: nodes[0]}  # most recent node per level; level 0 is the root
    for level, title, page, *rest in entries:
        parent = newest[level - 1]
        options = rest[0] if rest and isinstance(rest[0], dict) else {'point': rest[0] if rest else None}
        node = {'xref': doc.get_new_xref(), 'parent': parent['xref'], 'first': 0, 'last': 0, 'count': 0,
                'prev': parent['last'], 'next': 0, 'title': fitz.get_pdf_str(str(title)),
                'dest': _target(doc, page, options)}
        # Reserve the number: get_new_xref returns the same one until it is used.
        doc.update_object(node['xref'], '<<>>')
        if parent['last']:
            by_xref[parent['last']]['next'] = node['xref']
        else:
            parent['first'] = node['xref']
        parent['last'] = node['xref']
        parent['count'] += 1
        newest[level] = node
        by_xref[node['xref']] = node
        nodes.append(node)
    top = [node for node in nodes[1:] if node['parent'] == root]
    if old_last:
        top[0]['prev'] = old_last
    for node in nodes[1:]:
        text = f"<</Title{node['title']}/Parent {node['parent']} 0 R{node['dest']}"
        for key in ('prev', 'next', 'first', 'last'):
            if node[key]:
                text += f'/{key.capitalize()} {node[key]} 0 R'
        if node['count']:
            text += f"/Count -{node['count']}"
        doc.update_object(node['xref'], text + '>>')
    if old_last:
        doc.xref_set_key(old_last, 'Next', f"{top[0]['xref']} 0 R")
    else:
        doc.xref_set_key(root, 'First', f"{top[0]['xref']} 0 R")
    doc.xref_set_key(root, 'Last', f"{top[-1]['xref']} 0 R")
    # /Count is the number of visible items. A negative or unreadable one is
    # left as it is; a missing one is only written for an outline we started.
    kind, value = doc.xref_get_key(root, 'Count')
    if kind == 'int' and int(value) >= 0:
        doc.xref_set_key(root, 'Count', str(int(value) + len(top)))
    elif kind == 'null' and not old_last:
        doc.xref_set_key(root, 'Count', str(len(top)))
    doc.init_doc()
    return len(entries)
