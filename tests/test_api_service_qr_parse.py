from __future__ import annotations

import unittest

from services.api_service import ApiService


class ApiServiceQrParseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ApiService()

    def test_parse_legacy_idx_and_path_suffix_format(self) -> None:
        result = self.service.parse_qr_redirect(
            "https://witchform.com/qrcode_link.php?a=1",
            302,
            "/w/myform/sellForm-history-detail/14872765?idx=980235",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.order_number, "980235_14872765")

    def test_parse_full_order_number_from_path_without_idx(self) -> None:
        result = self.service.parse_qr_redirect(
            "https://witchform.com/qrcode_link.php?a=1",
            302,
            "/w/myform/sellForm-history-detail/WFLM7QSDTC_69D53CU23685",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.order_number, "WFLM7QSDTC_69D53CU23685")

    def test_parse_full_order_number_from_query_without_idx(self) -> None:
        result = self.service.parse_qr_redirect(
            "https://witchform.com/qrcode_link.php?a=1",
            302,
            "/w/myform/sellForm-history-detail/14872765?order_no=WFLM7QSDTC_69D53CU23685",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.order_number, "WFLM7QSDTC_69D53CU23685")

    def test_parse_split_uuid_and_path_suffix_format(self) -> None:
        result = self.service.parse_qr_redirect(
            "https://witchform.com/qrcode_link.php?a=1",
            302,
            "/w/myform/sellForm-history-detail/69D1IASG67A8?uuid=WFLM7QSDTC&qrcode=QXRSRWc5bTEzMjJVY1RvYmxjNVd6QT09",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.order_number, "WFLM7QSDTC_69D1IASG67A8")

    def test_missing_idx_and_full_order_number_reports_clear_error(self) -> None:
        result = self.service.parse_qr_redirect(
            "https://witchform.com/qrcode_link.php?a=1",
            302,
            "/w/myform/sellForm-history-detail/14872765",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "ORDER_NUMBER_MISSING")


if __name__ == "__main__":
    unittest.main()
