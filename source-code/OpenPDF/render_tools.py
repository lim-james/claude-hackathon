"""Bounded UI caches and direct pixel transfer (no PNG encode/decode)."""
from collections import OrderedDict
from PIL import Image


def pixmap_image(pix):
    return Image.frombytes('RGBA' if pix.alpha else 'RGB', (pix.width, pix.height), pix.samples)


class PageCache:
    def __init__(self, limit=8):
        self.limit = limit
        self.owner = None
        self.data = OrderedDict()

    def sync(self, doc):
        if self.owner is not doc:
            self.owner = doc
            self.data.clear()

    def invalidate(self, pages=None):
        if pages is None:
            self.data.clear()
        else:
            for key in list(self.data):
                if key[1] in pages:
                    del self.data[key]

    def get(self, doc, kind, page, factory, variant=None):
        self.sync(doc)
        key = (kind, page, variant)
        if key not in self.data:
            self.data[key] = factory()
        self.data.move_to_end(key)
        # Bound each kind independently; a text lookup should not evict the
        # current rendered image. Large zoom images are limited separately.
        keys = [k for k in self.data if k[0] == kind]
        limit = 2 if kind == 'image' else self.limit
        while len(keys) > limit:
            del self.data[keys.pop(0)]
        return self.data[key]


class TextCache:
    """Packed search text (`text_tools.search_lines`) for every page of the open
    document, so a repeated or extended query extracts nothing again. Bounded by
    characters held rather than by pages; least recently searched pages go first."""

    def __init__(self, limit=1_500_000):
        self.limit = limit
        self.owner = None
        self.data = OrderedDict()
        self.size = 0

    def sync(self, doc):
        if self.owner is not doc:
            self.owner = doc
            self.invalidate()

    def invalidate(self, pages=None):
        for page in list(self.data) if pages is None else pages:
            if page in self.data:
                self.size -= self.data.pop(page)[1]

    def get(self, doc, page, factory):
        self.sync(doc)
        if page not in self.data:
            lines = factory()
            size = 1 + sum(len(line.text) for line in lines)
            self.data[page] = lines, size
            self.size += size
        self.data.move_to_end(page)
        while self.size > self.limit and len(self.data) > 1:
            self.size -= self.data.popitem(last=False)[1][1]
        return self.data[page][0]
