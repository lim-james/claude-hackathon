import gc
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import fitz
from box_tools import text_sections, remove_section
from render_tools import PageCache, TextCache, pixmap_image
from text_tools import removal_rects, find_matches, lines, search_lines, SlicedSearch


def per_character_matches(doc, query):
    """find_matches as it was before search text was packed (issue #43): the reference answer."""
    matches = []
    for index in range(len(doc)):
        page = doc[index]
        for line in lines(page):
            text = ''.join(c['c'] for c in line['chars'])
            for found in re.finditer(re.escape(query), text, re.IGNORECASE):
                chars = line['chars'][found.start():found.end()]
                rect = fitz.Rect(chars[0]['bbox'])
                for char in chars[1:]:
                    rect |= fitz.Rect(char['bbox'])
                following = line['chars'][found.end():]
                limit = next((c['bbox'][0] for c in following if not c['c'].isspace()), page.cropbox.width - 12)
                matches.append({'page': index, 'rect': tuple(rect), 'text': found.group(),
                                'chars': chars, 'style': chars[0]['style'],
                                'origin': chars[0]['origin'], 'dir': line['dir'],
                                'right_limit': max(rect.x1, limit), 'context': text})
    return matches


def search_document():
    """Two fonts on one line, upright and rotated text, a rotated page, an empty page."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox((50, 50, 545, 400), 'the quarterly report paragraph and table update a.b( end\n' * 6, fontsize=10)
    x = 50 + fitz.get_text_length('Two fonts in one para', fontname='helv', fontsize=11)
    page.insert_text((50, 450), 'Two fonts in one para', fontname='helv', fontsize=11)
    page.insert_text((x, 450), 'graph of THE report', fontname='tiro', fontsize=11)
    page.insert_text((300, 700), 'the sideways paragraph', rotate=90)
    doc.new_page()
    turned = doc.new_page(width=400, height=300)
    turned.insert_text((40, 60), 'Paragraph at the page edge: the last paragraph')
    turned.set_rotation(90)
    return doc


class PerformanceTests(unittest.TestCase):
    def test_long_paragraph_removal_uses_line_regions_not_character_objects(self):
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_textbox((40, 40, 540, 780), 'A useful paragraph with words to drag and edit. ' * 24, fontsize=12)
            section = text_sections(page)[0]
            self.assertGreater(len(section['chars']), 1000)
            self.assertLess(len(removal_rects(section['chars'])), 30)
            original, calls = fitz.Page.add_redact_annot, []
            def record(page, *args, **kwargs):
                calls.append(args)
                return original(page, *args, **kwargs)
            with patch.object(fitz.Page, 'add_redact_annot', record):
                remove_section(page, section)
            self.assertLess(len(calls), 30)
            self.assertFalse(page.get_text().strip())

    def test_grouped_removal_does_not_bridge_unrelated_text_in_a_gap(self):
        with fitz.open() as doc:
            page = doc.new_page()
            for x, text in [(40, 'Left'), (140, 'Keep'), (240, 'Right')]:
                page.insert_text((x, 50), text)
            source = lines(page)
            chars = [c for line in source for c in line['chars'] if c['bbox'][0] < 100 or c['bbox'][0] > 230]
            remove_section(page, {'chars': chars})
            self.assertEqual(page.get_text().strip(), 'Keep')

    def test_cache_invalidates_only_edited_page_and_resets_on_restore(self):
        cache, doc = PageCache(), object()
        first, second = object(), object()
        self.assertIs(cache.get(doc, 'sections', 0, lambda: first), first)
        cache.get(doc, 'sections', 1, lambda: second)
        cache.invalidate({0})
        replacement = object()
        self.assertIs(cache.get(doc, 'sections', 0, lambda: replacement), replacement)
        self.assertIs(cache.get(doc, 'sections', 1, lambda: None), second)
        self.assertIsNone(cache.get(object(), 'sections', 1, lambda: None))

    def test_cache_is_bounded(self):
        cache, doc = PageCache(limit=3), object()
        for i in range(40):
            cache.get(doc, 'sections', i, lambda: i)
            cache.get(doc, 'image', i, lambda: i)
        self.assertEqual(len(cache.data), 5)

    def test_cached_search_matches_uncached_search(self):
        with fitz.open() as doc:
            for _ in range(3):
                doc.new_page().insert_text((40, 50), 'Find this word')
            cached = {i: lines(doc[i]) for i in range(3)}
            self.assertEqual(find_matches(doc, 'word'), find_matches(doc, 'word', line_provider=cached.__getitem__))
            self.assertEqual(len(find_matches(doc, 'word', page_indices=[1])), 1)

    def test_packed_search_gives_the_per_character_answers(self):
        with search_document() as doc:
            self.assertTrue(any(len(line.spans) > 1 for line in search_lines(doc[0])), 'no line with two fonts')
            cache = TextCache()
            packed = lambda index: cache.get(doc, index, lambda: search_lines(doc[index]))
            per_character = {index: lines(doc[index]) for index in range(len(doc))}
            for query, hits in [('paragraph', 10), ('PARAGRAPH', 10), ('the', 10), ('missing', 0), ('a.b(', 6)]:
                expected = per_character_matches(doc, query)
                self.assertEqual(len(expected), hits, query)
                self.assertEqual(find_matches(doc, query), expected, query)
                self.assertEqual(find_matches(doc, query, line_provider=packed), expected, query)
                self.assertEqual(find_matches(doc, query, line_provider=per_character.__getitem__), expected, query)
            self.assertEqual(find_matches(doc, 'paragraph', [2], packed), [m for m in per_character_matches(doc, 'paragraph') if m['page'] == 2])

    def test_match_box_skips_empty_character_boxes_like_rect_union(self):
        def line(*boxes):
            chars = [{'c': 'x', 'bbox': box, 'origin': (box[0], box[3])} for box in boxes]
            style = {'font': 'f'}
            return {'chars': [dict(c, style=style) for c in chars], 'dir': (1.0, 0.0), 'bbox': boxes[0]}
        with fitz.open() as doc:
            doc.new_page()
            for boxes in [((10, 10, 10, 20), (12, 11, 18, 19), (18, 10, 24, 20)),
                          ((10, 10, 16, 20), (16, 30, 16, 40), (16, 9, 22, 21)),
                          ((10, 10, 10, 20), (12, 12, 12, 30))]:
                boxes = [tuple(map(float, box)) for box in boxes]
                expected = fitz.Rect(boxes[0])
                for box in boxes[1:]:
                    expected |= fitz.Rect(box)
                found = find_matches(doc, 'x' * len(boxes), line_provider=lambda index: [line(*boxes)])
                self.assertEqual(found[0]['rect'], tuple(expected))

    def test_search_text_cache_is_bounded_and_dropped_with_the_document(self):
        cache, doc = TextCache(limit=100), object()
        page = lambda: [SimpleNamespace(text='x' * 30)]
        for i in range(40):
            cache.get(doc, i, page)
        self.assertLessEqual(cache.size, 100)
        self.assertEqual(list(cache.data), [37, 38, 39])
        cache.invalidate({38})
        self.assertEqual(list(cache.data), [37, 39])
        fresh = [SimpleNamespace(text='new')]
        self.assertIs(cache.get(doc, 38, lambda: fresh), fresh)
        self.assertIsNot(cache.get(doc, 39, lambda: fresh), fresh)
        cache.invalidate()
        self.assertEqual((len(cache.data), cache.size), (0, 0))
        cache.get(doc, 1, page)
        cache.sync(object())
        self.assertEqual((len(cache.data), cache.size), (0, 0))

    def test_whole_document_fits_the_search_text_cache_and_is_extracted_once(self):
        with fitz.open() as doc:
            for _ in range(30):
                doc.new_page().insert_text((40, 50), 'Find this word')
            cache, extracted = TextCache(), []
            def packed(index):
                return cache.get(doc, index, lambda: extracted.append(index) or search_lines(doc[index]))
            first = find_matches(doc, 'word', line_provider=packed)
            self.assertEqual(find_matches(doc, 'wor', line_provider=packed), find_matches(doc, 'wor'))
            self.assertEqual(find_matches(doc, 'word', line_provider=packed), first)
            self.assertEqual(extracted, list(range(30)))

    def sliced(self, doc):
        """A SlicedSearch whose slices hold one page each, and its queue of pending slices."""
        queue, ticks = [], iter(range(10 ** 6))
        return SlicedSearch(queue.append, budget=0.5, clock=lambda: next(ticks)), queue

    def test_sliced_search_ends_with_the_one_shot_answer(self):
        with search_document() as doc:
            search, queue = self.sliced(doc)
            reports = []
            search.start([1, 0, 2], lambda index: find_matches(doc, 'paragraph', [index]),
                         lambda matches, done: reports.append((len(matches), done)))
            self.assertTrue(search.running)
            while queue:
                queue.pop(0)()
            self.assertEqual(reports, [(0, False), (8, False), (10, True)])
            self.assertFalse(search.running)
            self.assertEqual([m for index in sorted(search.found) for m in search.found[index]],
                             find_matches(doc, 'paragraph'))

    def test_newer_search_stops_the_older_one(self):
        with search_document() as doc:
            search, queue = self.sliced(doc)
            old, new = [], []
            search.start([0, 1, 2], lambda index: find_matches(doc, 'para', [index]), lambda matches, done: old.append(done))
            queue.pop(0)()
            search.start([0, 1, 2], lambda index: find_matches(doc, 'paragraph', [index]),
                         lambda matches, done: new.append((matches, done)))
            while queue:
                queue.pop(0)()
            self.assertEqual(old, [False])
            self.assertEqual([done for _, done in new], [False, False, True])
            self.assertEqual(new[-1][0], find_matches(doc, 'paragraph'))
            search.start([0, 1, 2], lambda index: self.fail('searched after cancel'), lambda matches, done: self.fail('reported after cancel'))
            search.cancel()
            while queue:
                queue.pop(0)()
            self.assertFalse(search.running)

    def test_page_edited_during_a_sliced_search_is_searched_again(self):
        with search_document() as doc:
            search, queue = self.sliced(doc)
            last = []
            search.start([0, 1, 2], lambda index: find_matches(doc, 'paragraph', [index]),
                         lambda matches, done: last.append((matches, done)))
            queue.pop(0)()
            doc[0].insert_text((50, 600), 'one more paragraph')
            search.redo({0})
            while queue:
                queue.pop(0)()
            self.assertTrue(last[-1][1])
            self.assertEqual(len(last[-1][0]), 11)
            self.assertEqual(last[-1][0], find_matches(doc, 'paragraph'))

    def test_sliced_search_counts_pages_and_pauses_the_collector_for_each_slice(self):
        with search_document() as doc:
            search, queue = self.sliced(doc)
            collecting, progress = [], []
            def page(index):
                collecting.append(gc.isenabled())
                return find_matches(doc, 'paragraph', [index])
            self.assertTrue(gc.isenabled())
            search.start([0, 1, 2], page, lambda matches, done: progress.append(search.progress))
            while queue:
                queue.pop(0)()
                self.assertTrue(gc.isenabled())
            self.assertEqual(collecting, [False, False, False])
            self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])

    def test_direct_pixels_match_png_render(self):
        import io
        from PIL import Image
        with fitz.open() as doc:
            page = doc.new_page(width=100, height=100)
            page.draw_rect(page.rect, fill=(.2, .4, .6))
            pix = page.get_pixmap()
            self.assertEqual(pixmap_image(pix).tobytes(), Image.open(io.BytesIO(pix.tobytes('png'))).tobytes())

