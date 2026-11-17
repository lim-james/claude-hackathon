"""Add bookmarks without rewriting the ones a PDF already has.

``Document.set_toc`` deletes the whole outline and rebuilds it from what
``get_toc`` returned, which loses web-link actions, colours and zoom, and moves
destinations on pages with an offset crop or media box. Here new outline
objects are appended and linked in; existing objects keep their content.
"""
import fitz


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


def append_bookmarks(doc, entries):
    """Append bookmarks after the existing ones and return how many were added.

    ``entries`` is a list in ``get_toc`` order: ``[level, title, page]`` with a
    1-based page, optionally followed by an ``(x, y)`` point on that page
    (default: the top). The first entry must be level 1 and a level may only
    go one deeper than the entry before it; deeper entries become children of
    the appended entry above them and start collapsed.
    """
    entries = [list(entry) for entry in entries]
    if not entries:
        return 0
    previous = 0
    for level, title, page, *rest in entries:
        if not isinstance(level, int) or not 1 <= level <= previous + 1:
            raise ValueError(f'Bookmark "{title}" has level {level} after level {previous}.')
        if not isinstance(page, int) or not 1 <= page <= doc.page_count:
            raise ValueError(f'Bookmark "{title}" points to page {page} of {doc.page_count}.')
        previous = level
    root = _root(doc)
    old_last = _last_top_item(doc, root)
    nodes = [{'xref': root, 'first': 0, 'last': 0, 'count': 0}]
    newest = {0: nodes[0]}  # most recent node per level; level 0 is the root
    for level, title, page, *rest in entries:
        parent = newest[level - 1]
        node = {'xref': doc.get_new_xref(), 'parent': parent['xref'], 'first': 0, 'last': 0, 'count': 0,
                'prev': parent['last'], 'next': 0, 'title': fitz.get_pdf_str(str(title)),
                'dest': _destination(doc, page - 1, rest[0] if rest else (72, 36))}
        # Reserve the number: get_new_xref returns the same one until it is used.
        doc.update_object(node['xref'], '<<>>')
        if parent['last']:
            next(n for n in nodes if n['xref'] == parent['last'])['next'] = node['xref']
        else:
            parent['first'] = node['xref']
        parent['last'] = node['xref']
        parent['count'] += 1
        newest[level] = node
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
