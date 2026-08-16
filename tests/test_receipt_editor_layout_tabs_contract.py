"""Receipt editor tab surface contract tests."""
from __future__ import annotations

import unittest
from pathlib import Path


class ReceiptEditorLayoutTabsContractTest(unittest.TestCase):
    def test_editor_exposes_receipt_and_product_receipt_controls(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn('ft.TextButton("영수증"', source)
        self.assertIn('ft.TextButton("상품 영수증"', source)
        self.assertIn('ft.Switch(', source)
        self.assertIn("상품 영수증 추가 출력", source)
        self.assertIn("QR 스캔 시 영수증 자동 출력", source)
        self.assertIn('getattr(settings_store.load(), "qr_scan_auto_print_enabled", True)', source)


if __name__ == "__main__":
    unittest.main()
