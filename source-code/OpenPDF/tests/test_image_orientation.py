import tempfile
import unittest
from pathlib import Path
import fitz
from PIL import Image
from core import images_to_pdf


class ImageOrientationTests(unittest.TestCase):
    def test_image_pdf_preserves_exif_display_orientation(self):
        red, green, blue, yellow = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)
        orientations = {
            None: (red, green, blue, yellow),
            1: (red, green, blue, yellow),
            2: (green, red, yellow, blue),
            3: (yellow, blue, green, red),
            4: (blue, yellow, red, green),
            5: (red, blue, green, yellow),
            6: (blue, red, yellow, green),
            7: (yellow, green, blue, red),
            8: (green, yellow, red, blue),
        }
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / 'photo.jpg', Path(folder) / 'photo.pdf'
            for orientation, colors in orientations.items():
                with self.subTest(orientation=orientation):
                    image = Image.new('RGB', (80, 40))
                    for color, box in zip((red, green, blue, yellow),
                                          ((0, 0, 40, 20), (40, 0, 80, 20),
                                           (0, 20, 40, 40), (40, 20, 80, 40))):
                        image.paste(color, box)
                    exif = Image.Exif()
                    if orientation is not None:
                        exif[274] = orientation
                    image.save(source, quality=95, subsampling=0, exif=exif)

                    images_to_pdf([source], output)

                    with fitz.open(output) as doc:
                        page = doc[0]
                        self.assertAlmostEqual(page.rect.width / page.rect.height,
                                               .5 if orientation in (5, 6, 7, 8) else 2)
                        pix = page.get_pixmap()
                        for (x, y), expected in zip(((.25, .25), (.75, .25), (.25, .75), (.75, .75)), colors):
                            actual = pix.pixel(int(pix.width * x), int(pix.height * y))
                            for channel, target in zip(actual, expected):
                                self.assertAlmostEqual(channel, target, delta=5)
