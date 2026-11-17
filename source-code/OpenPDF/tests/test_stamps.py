import datetime
import unittest
import fitz
from PIL import Image, ImageChops
from core import PDF


class HeaderFooterBatesTests(unittest.TestCase):
    def make(self, pages=3):
        pdf = PDF()
        for _ in range(pages):
            pdf.doc.new_page(width=400, height=600)
        return pdf

    def page_text(self, pdf, index):
        return pdf.doc[index].get_text()

    def test_header_footer_tokens_expand(self):
        pdf = self.make(3)
        try:
            pdf.apply_header_footer({
                'header-left': 'Report',
                'header-right': '<date>',
                'footer-center': 'Page <page> of <pages>',
                'footer-right': '<filename>',
            }, filename='demo.pdf')
            pdf.restore(pdf.bytes())
            first = self.page_text(pdf, 0)
            self.assertIn('Report', first)
            self.assertIn(datetime.date.today().isoformat(), first)
            self.assertIn('Page 1 of 3', first)
            self.assertIn('demo.pdf', first)
            self.assertIn('Page 3 of 3', self.page_text(pdf, 2))
        finally:
            pdf.close()

    def test_header_footer_requires_content_and_respects_range(self):
        pdf = self.make(3)
        try:
            with self.assertRaises(ValueError):
                pdf.apply_header_footer({'header-left': '   '})
            count = pdf.apply_header_footer({'footer-center': 'only here'}, first=1, last=1)
            self.assertEqual(count, 1)
            self.assertNotIn('only here', self.page_text(pdf, 0))
            self.assertIn('only here', self.page_text(pdf, 1))
            self.assertNotIn('only here', self.page_text(pdf, 2))
        finally:
            pdf.close()

    def test_bates_sequence_and_padding(self):
        pdf = self.make(3)
        try:
            next_number = pdf.bates_number(prefix='ACME-', start=1, digits=5)
            pdf.restore(pdf.bytes())
            self.assertIn('ACME-00001', self.page_text(pdf, 0))
            self.assertIn('ACME-00002', self.page_text(pdf, 1))
            self.assertIn('ACME-00003', self.page_text(pdf, 2))
            self.assertEqual(next_number, 4)  # lets the next document continue
        finally:
            pdf.close()

    def test_bates_continues_across_documents(self):
        first = self.make(2)
        second = self.make(2)
        try:
            next_number = first.bates_number(prefix='D', start=1, digits=4)
            second.bates_number(prefix='D', start=next_number, digits=4)
            self.assertIn('D0003', self.page_text(second, 0))
            self.assertIn('D0004', self.page_text(second, 1))
        finally:
            first.close()
            second.close()

    def test_bates_validation(self):
        pdf = self.make(1)
        try:
            with self.assertRaises(ValueError):
                pdf.bates_number(digits=0)
            with self.assertRaises(ValueError):
                pdf.bates_number(position='middle-middle')
        finally:
            pdf.close()


def render(page, dpi=72):
    """The page as a viewer displays it."""
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes('RGB', (pix.width, pix.height), pix.samples)


def ink_box(image):
    """Bounding box of everything drawn on a white page, or None when it is blank."""
    return ImageChops.difference(image, Image.new('RGB', image.size, (255, 255, 255))).getbbox()


class RotatedStampTests(unittest.TestCase):
    """Stamps keep their slot and read upright on rotated pages (issue #35)."""
    ROTATIONS = (0, 90, 180, 270)

    def stamped(self, rotations, stamp):
        pdf = PDF()
        for rotation in rotations:
            pdf.doc.new_page().set_rotation(rotation)  # blank A4, rotated as the Rotate tool does
        self.addCleanup(pdf.close)
        result = stamp(pdf)
        pdf.restore(pdf.bytes())  # what a saved file contains
        return pdf, result

    def placement(self, page):
        """Ink distances to the displayed page's edges, ink size, and the ink itself."""
        image = render(page)
        box = ink_box(image)
        self.assertIsNotNone(box, f'nothing visible on a page rotated {page.rotation}')
        left, top, right, bottom = box
        # Compare glyphs at 144 dpi: centred slots move by half a point between portrait
        # and landscape, which is a whole pixel there and so leaves antialiasing alone.
        sharp = render(page, dpi=144)
        return {'left': left, 'top': top, 'right': image.width - right,
                'bottom': image.height - bottom, 'width': right - left,
                'height': bottom - top, 'ink': sharp.crop(ink_box(sharp)).convert('L')}

    def assert_same_placement(self, got, want, anchors, label):
        for edge in anchors:
            self.assertAlmostEqual(got[edge], want[edge], delta=2, msg=f'{label}: {edge} edge')
        for side in ('width', 'height'):
            self.assertAlmostEqual(got[side], want[side], delta=1, msg=f'{label}: {side}')
        self.assertEqual(got['ink'].size, want['ink'].size, f'{label}: ink size at 144 dpi')
        changed = sum(1 for value in ImageChops.difference(got['ink'], want['ink']).getdata()
                      if value > 32)
        pixels = want['ink'].width * want['ink'].height
        self.assertLess(changed / pixels, .02, f'{label}: text is not upright')

    def anchors(self, band, align):
        vertical = 'top' if band in ('header', 'top') else 'bottom'
        return (vertical,) if align == 'center' else (vertical, align)

    def centre_offset(self, pdf):
        box = ink_box(render(pdf.doc[0]))
        return (box[0] + box[2]) / 2 - pdf.doc[0].rect.width / 2

    def check_slots(self, slots, stamp):
        for slot in slots:
            band, align = slot.split('-')
            # One single-page document per rotation, so every page carries the same label.
            pages = {rotation: self.stamped((rotation,), lambda p: stamp(p, slot))[0]
                     for rotation in self.ROTATIONS}
            want = self.placement(pages[0].doc[0])
            for rotation, pdf in pages.items():
                label = f'{slot} at {rotation} degrees'
                self.assert_same_placement(self.placement(pdf.doc[0]), want,
                                           self.anchors(band, align), label)
                if align == 'center':
                    self.assertAlmostEqual(self.centre_offset(pdf), self.centre_offset(pages[0]),
                                           delta=2, msg=f'{label}: not centred')

    def test_every_header_footer_slot_at_every_rotation(self):
        self.check_slots(PDF.HEADER_FOOTER_SLOTS,
                         lambda pdf, slot: pdf.apply_header_footer({slot: 'Stamp'}))

    def test_every_bates_position_at_every_rotation(self):
        self.check_slots(PDF.BATES_POSITIONS,
                         lambda pdf, slot: pdf.bates_number(prefix='ACME', position=slot))

    def test_mixed_rotations_in_one_call_keep_the_bates_sequence(self):
        pdf, next_number = self.stamped(self.ROTATIONS,
                                        lambda p: p.bates_number(prefix='ACME-', start=7))
        self.assertEqual(next_number, 7 + len(self.ROTATIONS))
        for index, rotation in enumerate(self.ROTATIONS):
            page = pdf.doc[index]
            self.assertIn(f'ACME-{7 + index:06d}', page.get_text())
            box = ink_box(render(page))
            self.assertIsNotNone(box, f'no Bates label on the page rotated {rotation}')
            self.assertAlmostEqual(page.rect.width - box[2], 38, delta=2, msg=f'{rotation}: right')
            self.assertAlmostEqual(page.rect.height - box[3], 35, delta=2, msg=f'{rotation}: bottom')

    def test_unrotated_output_is_unchanged(self):
        cases = {
            (36, 28, 64, 36): lambda p: p.apply_header_footer({'header-left': 'HEAD'}),
            (532, 798, 559, 807): lambda p: p.apply_header_footer({'footer-right': 'FOOT'}),
            (496, 798, 557, 807): lambda p: p.bates_number(prefix='ACME'),
            (298, 814, 315, 823): lambda p: p.number_pages(),
        }
        for box, stamp in cases.items():
            pdf, _ = self.stamped((0,), stamp)
            self.assertEqual(ink_box(render(pdf.doc[0])), box)


if __name__ == '__main__':
    unittest.main()
