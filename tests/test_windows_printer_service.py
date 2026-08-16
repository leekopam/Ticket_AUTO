from __future__ import annotations

from pathlib import Path
import sys
import unittest

from PIL import Image

from services.windows_printer_service import WindowsPrinterService


class WindowsPrinterServiceTest(unittest.TestCase):
    def test_requirements_include_pinned_pywin32_for_windows_printing(self) -> None:
        requirements = Path("requirements.txt").read_text(encoding="utf-8-sig")
        lines = {
            line.split("#", 1)[0].strip().lower()
            for line in requirements.splitlines()
            if line.split("#", 1)[0].strip()
        }

        self.assertIn('pywin32==311; platform_system == "windows"', lines)

    def test_win32_modules_are_available_on_windows_for_printer_listing(self) -> None:
        if sys.platform != "win32":
            self.skipTest("pywin32 printer enumeration is Windows-only")

        win32con, win32print, win32ui = WindowsPrinterService()._import_win32()

        self.assertTrue(hasattr(win32con, "HORZRES"))
        self.assertTrue(hasattr(win32print, "EnumPrinters"))
        self.assertTrue(hasattr(win32ui, "CreateDC"))

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
