"""수령 완료 클릭 결과 계약 테스트."""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from services.browser_service import BrowserService, ReceiptClickResult


class BrowserReceiptResultContractTest(unittest.TestCase):
    def test_click_receipt_button_returns_structured_result(self) -> None:
        signature = inspect.signature(BrowserService.click_receipt_button)
        return_annotation = signature.return_annotation
        self.assertIn("ReceiptClickResult", str(return_annotation))

    def test_failure_codes_exist_in_browser_service_source(self) -> None:
        source = Path("services/browser_service.py").read_text(encoding="utf-8")
        self.assertIn("NO_ACTIVE_PAGE", source)
        self.assertIn("PRIMARY_CLICK_FAIL", source)
        self.assertIn("CONFIRM_CLICK_FAIL", source)

    def test_receipt_click_result_shape(self) -> None:
        result = ReceiptClickResult(success=False, error_code="CONFIRM_CLICK_FAIL", error_message="x")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "CONFIRM_CLICK_FAIL")
        self.assertEqual(result.error_message, "x")


if __name__ == "__main__":
    unittest.main()
