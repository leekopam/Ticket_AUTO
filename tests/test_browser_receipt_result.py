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

    def test_extract_order_number_candidate_from_labelled_text(self) -> None:
        result = BrowserService._extract_order_number_candidate(
            "주문번호: WFLM7QSDTC_69D53CU23685"
        )
        self.assertEqual(result, "WFLM7QSDTC_69D53CU23685")

    def test_extract_order_number_candidate_from_url(self) -> None:
        result = BrowserService._extract_order_number_candidate(
            "https://witchform.com/w/myform/sellForm-history-detail/WFLM7QSDTC_69D53CU23685"
        )
        self.assertEqual(result, "WFLM7QSDTC_69D53CU23685")

    def test_extract_order_number_candidate_keeps_legacy_numeric_format(self) -> None:
        result = BrowserService._extract_order_number_candidate(
            "https://witchform.com/w/myform/sellForm-history-detail/14872765?idx=980235",
            "주문번호: 980235_14872765",
        )
        self.assertEqual(result, "980235_14872765")

    def test_extract_order_number_candidate_rejects_html_class_names(self) -> None:
        result = BrowserService._extract_order_number_candidate(
            '<div class="video_slider">test</div>',
            "video_slider",
        )
        self.assertEqual(result, "")

    def test_extract_buyer_contact_candidate(self) -> None:
        buyer_name, buyer_phone = BrowserService._extract_buyer_contact_candidate(
            "주문자명\n홍영기\n주문자 연락처\n010-1234-5678"
        )
        self.assertEqual(buyer_name, "홍영기")
        self.assertEqual(buyer_phone, "010-1234-5678")


if __name__ == "__main__":
    unittest.main()
