from __future__ import annotations

import unittest

from PIL import Image

from services.windows_printer_service import WindowsPrinterService


class WindowsPrinterServiceTest(unittest.TestCase):
    def test_fit_to_printer_width_does_not_upscale_narrow_image(self) -> None:
        image = Image.new("RGB", (384, 100), "white")

        fitted = WindowsPrinterService._fit_to_printer_width(image, 576)

        self.assertEqual(fitted.size, (384, 100))

    def test_fit_to_printer_width_downscales_wider_image(self) -> None:
        image = Image.new("RGB", (576, 100), "white")

        fitted = WindowsPrinterService._fit_to_printer_width(image, 384)

        self.assertEqual(fitted.width, 384)
        self.assertLess(fitted.height, 100)


if __name__ == "__main__":
    unittest.main()
