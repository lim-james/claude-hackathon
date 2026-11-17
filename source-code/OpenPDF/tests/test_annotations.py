import os
import tempfile
import unittest
import fitz
from PIL import Image, ImageChops
from core import PDF


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.pdf = PDF()
        page = self.pdf.doc.new_page(width=500, height=700)
        page.insert_text((40, 50), 'The quick brown fox jumps')
        self.pdf.doc.new_page(width=500, height=700)

    def tearDown(self):
        self.pdf.close()

    def quads(self, word='quick'):
        return self.pdf.doc[0].search_for(word, quads=True)

    def test_text_markup_kinds_survive_reopen(self):
        for kind, name in [('strikeout', 'StrikeOut'), ('underline', 'Underline'),
                           ('squiggly', 'Squiggly'), ('highlight', 'Highlight')]:
            with self.subTest(kind=kind):
                pdf = PDF()
                try:
                    page = pdf.doc.new_page(width=500, height=700)
                    page.insert_text((40, 50), 'The quick brown fox')
                    pdf.markup(0, page.search_for('quick', quads=True), kind, author='Tester')
                    pdf.restore(pdf.bytes())
                    reopened = pdf.doc[0]
                    annots = list(reopened.annots())
                    self.assertEqual(len(annots), 1)
                    self.assertEqual(annots[0].type[1], name)
                    self.assertEqual(annots[0].info.get('title'), 'Tester')
                finally:
                    pdf.close()

    def test_markup_requires_quads_and_valid_kind(self):
        with self.assertRaises(ValueError):
            self.pdf.markup(0, [], 'strikeout')
        with self.assertRaises(ValueError):
            self.pdf.markup(0, self.quads(), 'nonsense')

    def test_note_and_callout(self):
        self.pdf.note(0, fitz.Point(60, 60), 'A sticky note', author='Tester')
        self.pdf.callout(0, fitz.Rect(100, 200, 300, 260), 'A callout', author='Tester')
        self.pdf.restore(self.pdf.bytes())
        page = self.pdf.doc[0]
        kinds = sorted(a.type[1] for a in page.annots())
        self.assertEqual(kinds, ['FreeText', 'Text'])
        contents = sorted(a.info.get('content', '') for a in page.annots())
        self.assertIn('A sticky note', contents)
        self.assertIn('A callout', contents)

    def test_empty_note_or_callout_rejected(self):
        with self.assertRaises(ValueError):
            self.pdf.note(0, fitz.Point(60, 60), '   ')
        with self.assertRaises(ValueError):
            self.pdf.callout(0, fitz.Rect(100, 200, 300, 260), '')

    def test_shapes_including_arrow(self):
        rect = fitz.Rect(60, 300, 200, 360)
        expected = {'line': 'Line', 'arrow': 'Line', 'rectangle': 'Square', 'oval': 'Circle'}
        for kind, name in expected.items():
            with self.subTest(kind=kind):
                pdf = PDF()
                try:
                    pdf.doc.new_page(width=500, height=700)
                    pdf.shape(0, kind, rect, color=(1, 0, 0), width=2)
                    pdf.restore(pdf.bytes())
                    reopened = pdf.doc[0]
                    annots = list(reopened.annots())
                    self.assertEqual(len(annots), 1)
                    self.assertEqual(annots[0].type[1], name)
                finally:
                    pdf.close()
        with self.assertRaises(ValueError):
            self.pdf.shape(0, 'triangle', rect)

    def test_stamp_label_and_bounds(self):
        self.pdf.stamp(0, fitz.Rect(50, 350, 220, 395), 2, author='Tester')  # Confidential
        self.pdf.restore(self.pdf.bytes())
        page = self.pdf.doc[0]
        annot = next(iter(page.annots()))
        self.assertEqual(annot.type[1], 'Stamp')
        self.assertEqual(annot.info.get('content'), 'Confidential')
        for bad in (-1, len(PDF.STAMPS)):
            with self.assertRaises(ValueError):
                self.pdf.stamp(0, fitz.Rect(50, 350, 220, 395), bad)

    def test_annotations_enumeration_and_delete(self):
        self.pdf.markup(0, self.quads(), 'underline', author='A')
        self.pdf.note(1, fitz.Point(60, 60), 'note on page two', author='B')
        items = self.pdf.annotations()
        self.assertEqual(len(items), 2)
        self.assertEqual({i['page'] for i in items}, {0, 1})
        note = next(i for i in items if i['page'] == 1)
        self.assertEqual(note['text'], 'note on page two')
        self.assertEqual(note['author'], 'B')
        # delete the note, keep the markup
        self.assertTrue(self.pdf.delete_annotation(note['page'], note['xref']))
        self.pdf.restore(self.pdf.bytes())
        remaining = self.pdf.annotations()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]['kind'], 'Underline')
        # deleting a stale xref is a no-op, not an error
        self.assertFalse(self.pdf.delete_annotation(0, 999999))


if __name__ == '__main__':
    unittest.main()


class RotatedPlacementTests(unittest.TestCase):
    """Objects placed in a dragged area land there upright on a page of any
    /Rotate and any crop or media box (issues #125 and #137). Each case renders
    the saved file and compares the area with the same object on a plain page."""

    ROTATIONS = (0, 90, 180, 270)
    # PDF-space MediaBox and CropBox; None leaves the page's default.
    BOXES = {'origin': (None, None),
             'media offset': ((40, 30, 652, 822), None),
             'crop offset': (None, (20, 42, 560, 742)),
             'both offset': ((40, 30, 652, 822), (70, 90, 600, 800))}
    AREA = fitz.Rect(60, 120, 360, 200)  # dragged on the page as shown
    LATER = fitz.Rect(400, 400, 420, 420)
    NOTE = 'Please check the total of this invoice against the purchase order before paying it'  # wraps

    @classmethod
    def setUpClass(cls):
        # An image whose four quadrants differ, so a turned or mirrored copy shows.
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 16), False)
        for rect, colour in (((0, 0, 30, 8), (220, 0, 0)), ((30, 0, 60, 8), (0, 160, 0)),
                             ((0, 8, 30, 16), (0, 0, 220)), ((30, 8, 60, 16), (240, 200, 0))):
            pix.set_rect(fitz.IRect(rect), colour)
        cls.png = pix.tobytes('png')
        cls.folder = tempfile.TemporaryDirectory()
        cls.image = os.path.join(cls.folder.name, 'quadrants.png')
        with open(cls.image, 'wb') as handle:
            handle.write(cls.png)
        cls.reference = {}

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    PLACE = {
        'image': lambda pdf, rect, case: pdf.add_image(0, rect, case.image),
        'signature': lambda pdf, rect, case: pdf.add_signature(0, rect, case.png),
        'stamp': lambda pdf, rect, case: pdf.stamp(0, rect, 0),
        'text box': lambda pdf, rect, case: pdf.callout(0, rect, case.NOTE),
        'arrow': lambda pdf, rect, case: pdf.shape(0, 'arrow', rect),
        'line': lambda pdf, rect, case: pdf.shape(0, 'line', rect),
        'rectangle': lambda pdf, rect, case: pdf.shape(0, 'rectangle', rect),
        'oval': lambda pdf, rect, case: pdf.shape(0, 'oval', rect),
    }

    def page(self, pdf, rotation, boxes):
        page = pdf.doc.new_page(width=612, height=792)
        for key, box in zip(('MediaBox', 'CropBox'), boxes):
            if box:
                pdf.doc.xref_set_key(page.xref, key, '[%g %g %g %g]' % box)
        page.set_rotation(rotation)
        return pdf.doc[page.number]

    @staticmethod
    def render(page):
        pix = page.get_pixmap(alpha=False)
        return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)

    def placed(self, kind, rotation, boxes):
        """Render of the saved page after placing ``kind`` in AREA, as app.rect() passes it."""
        pdf = PDF()
        try:
            page = self.page(pdf, rotation, boxes)
            self.PLACE[kind](pdf, (self.AREA & page.rect) * page.derotation_matrix, self)
            # A later annotation in the same session must leave the first as drawn.
            pdf.shape(0, 'rectangle', self.LATER * page.derotation_matrix)
            pdf.restore(pdf.bytes())
            return self.render(pdf.doc[0]), pdf.doc.tobytes()
        finally:
            pdf.close()

    def area(self, image):
        box = ImageChops.invert(image.convert('L')).getbbox()
        return box, image.crop(tuple(self.AREA.irect + (-4, -4, 4, 4)))

    def test_every_object_at_every_rotation_and_box(self):
        for kind in self.PLACE:
            reference_box, reference = self.area(self.placed(kind, 0, (None, None))[0])
            self.assertIsNotNone(reference_box, kind)
            for name, boxes in self.BOXES.items():
                for rotation in self.ROTATIONS:
                    with self.subTest(kind=kind, boxes=name, rotation=rotation):
                        box, crop = self.area(self.placed(kind, rotation, boxes)[0])
                        self.assertIsNotNone(box, 'nothing drawn')
                        for got, want in zip(box, reference_box):
                            self.assertAlmostEqual(got, want, delta=2, msg=f'drawn at {box}, expected {reference_box}')
                        diff = ImageChops.difference(crop, reference).convert('L')
                        mean = sum(i * n for i, n in enumerate(diff.histogram())) / (crop.width * crop.height)
                        self.assertLess(mean, 6, 'content differs from the upright reference')

    def test_arrow_runs_from_top_left_to_bottom_right_as_shown(self):
        for name, boxes in self.BOXES.items():
            for rotation in self.ROTATIONS:
                with self.subTest(boxes=name, rotation=rotation):
                    _, data = self.placed('arrow', rotation, boxes)
                    doc = fitz.open('pdf', data)
                    page = doc[0]
                    start, end = (fitz.Point(v) * page.rotation_matrix for v in next(page.annots()).vertices)
                    self.assertAlmostEqual(start.distance_to(self.AREA.tl), 0, delta=0.01)
                    self.assertAlmostEqual(end.distance_to(self.AREA.br), 0, delta=0.01)
                    doc.close()

    def test_rotated_annotations_are_listed_where_drawn_and_delete(self):
        pdf = PDF()
        try:
            page = pdf.doc.new_page(width=612, height=792)
            page.set_rotation(90)
            page = pdf.doc[0]
            rect = self.AREA * page.derotation_matrix
            pdf.stamp(0, rect, 0)
            pdf.callout(0, rect, 'Total')
            pdf.restore(pdf.bytes())
            items = pdf.annotations()
            self.assertEqual([item['kind'] for item in items], ['Stamp', 'FreeText'])
            for item in items:  # app.reveal_rect() outlines rect * rotation_matrix
                shown = fitz.Rect(item['rect']) * pdf.doc[0].rotation_matrix
                self.assertTrue(fitz.Rect(self.AREA + (-1, -1, 1, 1)).contains(shown), (item['kind'], shown))
                self.assertGreater(shown.width, 290, item['kind'])
            self.assertTrue(pdf.delete_annotation(0, items[0]['xref']))
            self.assertEqual(len(pdf.annotations()), 1)
        finally:
            pdf.close()

    def test_undo_and_redo_keep_a_rotated_stamp_and_image(self):
        # app.change() snapshots before each edit; undo and redo restore them.
        for kind in ('stamp', 'image'):
            for rotation in (90, 270):
                with self.subTest(kind=kind, rotation=rotation):
                    pdf = PDF()
                    try:
                        page = self.page(pdf, rotation, self.BOXES['both offset'])
                        self.PLACE[kind](pdf, self.AREA * page.derotation_matrix, self)
                        placed = self.render(pdf.doc[0])
                        before_arrow = pdf.snapshot()
                        pdf.shape(0, 'arrow', fitz.Rect(400, 300, 500, 350) * pdf.doc[0].derotation_matrix)
                        after_arrow = pdf.snapshot()
                        pdf.restore(before_arrow)  # undo the arrow
                        self.assertIsNone(ImageChops.difference(self.render(pdf.doc[0]), placed).getbbox())
                        pdf.restore(after_arrow)  # redo it
                        _, area = self.area(self.render(pdf.doc[0]))
                        _, want = self.area(placed)
                        self.assertIsNone(ImageChops.difference(area, want).getbbox())
                    finally:
                        pdf.close()
