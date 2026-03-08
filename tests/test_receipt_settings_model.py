"""ReceiptSettings model compatibility tests."""
from __future__ import annotations

import unittest

from models.receipt_settings_model import ReceiptSettings


class ReceiptSettingsModelTest(unittest.TestCase):
    def test_extended_paths_and_flags_round_trip_through_dict(self) -> None:
        settings = ReceiptSettings(
            qr_scan_success_sound_path="C:/sounds/scan.mp3",
            product_template_path="templates/product_receipt_layout.json",
            print_product_receipt=True,
        )
        restored = ReceiptSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.qr_scan_success_sound_path, "C:/sounds/scan.mp3")
        self.assertEqual(restored.product_template_path, "templates/product_receipt_layout.json")
        self.assertTrue(restored.print_product_receipt)


if __name__ == "__main__":
    unittest.main()
