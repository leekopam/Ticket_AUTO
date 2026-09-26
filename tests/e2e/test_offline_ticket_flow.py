"""실제 외부 서비스와 장비를 사용하지 않는 티켓 처리 E2E 테스트."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main as app_main
from services.excel_service import ExcelService

from .support import (
    FakePrinterBackend,
    TEST_ORDER_NUMBER,
    TEST_QR_URL,
    build_offline_app,
    create_test_workbook,
    decode_generated_qr,
    record_metric,
)


class OfflineTicketFlowE2ETest(unittest.TestCase):
    def test_generated_qr_completes_order_and_captures_receipt_without_real_io(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "test_orders.xlsx"
            create_test_workbook(data_path)
            app, browser, scanner, sound = build_offline_app(data_path)
            printer = FakePrinterBackend()

            decoded_qr = decode_generated_qr(TEST_QR_URL)
            self.assertEqual(decoded_qr, TEST_QR_URL)

            with (
                patch("httpx.get") as http_get,
                patch(
                    "services.receipt_print_pipeline.WindowsPrinterService",
                    return_value=printer,
                ),
            ):
                app._process_qr(decoded_qr or "", allow_auth_retry=False)

            http_get.assert_not_called()
            saved_order = ExcelService(str(data_path)).find_order(TEST_ORDER_NUMBER)
            self.assertIsNotNone(saved_order)
            self.assertTrue(saved_order.is_received)
            self.assertEqual(saved_order.order_status, "거래종료")
            self.assertEqual(app._state, app_main.AppState.READY)
            self.assertEqual(scanner.status_message, "수령 완료 및 영수증 출력 완료")
            self.assertEqual(browser.calls, [])
            self.assertEqual(sound.success_count, 1)
            self.assertEqual(len(printer.jobs), 1)
            self.assertEqual(printer.jobs[0].job_name, f"Receipt_{TEST_ORDER_NUMBER}")
            self.assertGreater(printer.jobs[0].image.width, 0)
            self.assertGreater(printer.jobs[0].image.height, 0)
            self.assertLess(printer.jobs[0].image.convert("L").getextrema()[0], 255)

    def test_printer_failure_rolls_back_excel_state_without_real_printer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "test_orders.xlsx"
            create_test_workbook(data_path)
            app, _, scanner, sound = build_offline_app(data_path)
            printer = FakePrinterBackend(failure=RuntimeError("가상 프린터 실패"))

            decoded_qr = decode_generated_qr(TEST_QR_URL)
            self.assertEqual(decoded_qr, TEST_QR_URL)

            with (
                patch("httpx.get") as http_get,
                patch(
                    "services.receipt_print_pipeline.WindowsPrinterService",
                    return_value=printer,
                ),
            ):
                app._process_qr(decoded_qr or "", allow_auth_retry=False)

            http_get.assert_not_called()
            saved_order = ExcelService(str(data_path)).find_order(TEST_ORDER_NUMBER)
            self.assertIsNotNone(saved_order)
            self.assertFalse(saved_order.is_received)
            self.assertEqual(saved_order.order_status, "거래중")
            self.assertEqual(app._state, app_main.AppState.ERROR)
            self.assertIn("원복", scanner.status_message)
            self.assertEqual(sound.success_count, 0)
            self.assertEqual(len(printer.jobs), 1)
            record_metric("rollback_successes")


if __name__ == "__main__":
    unittest.main()
