"""실제 외부 서비스와 장비를 사용하지 않는 티켓 처리 E2E 테스트."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from openpyxl import Workbook

import main as app_main
from models.receipt_settings_model import ReceiptSettings
from services.api_service import ApiService
from services.excel_service import ExcelService
from services.qr_generator_service import generate_qr_image
from viewmodels.order_viewmodel import OrderViewModel
from views.scanner_view import ScannerView

from .support import (
    FakeBrowserService,
    FakePrinterBackend,
    FakeScannerView,
    MemoryReceiptSettingsStore,
    MemoryScanSuccessSoundService,
    OfflineDebugToolsService,
)


TEST_ORDER_NUMBER = "WFLM7QSDTC_69D53CU23685"
TEST_QR_URL = (
    "https://witchform.com/qrcode_link.php"
    f"?test_order={TEST_ORDER_NUMBER}"
)


def _create_test_workbook(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        [
            "주문번호",
            "주문자명",
            "주문자연락처",
            "좌석번호",
            "수령확인",
            "주문상태",
            "처리시간",
            "[상품1] 테스트 상품",
        ]
    )
    worksheet.append(
        [TEST_ORDER_NUMBER, "테스트 사용자", "010-0000-0000", "A-001", "", "거래중", "", 1]
    )
    workbook.save(path)
    workbook.close()


def _decode_generated_qr(payload: str) -> str | None:
    qr_image = generate_qr_image(payload, output_px=600)
    rgb_frame = np.asarray(qr_image.convert("RGB"))
    bgr_frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
    return ScannerView._decode_qr(bgr_frame)


def _build_offline_app(
    data_path: Path,
) -> tuple[
    app_main.Application,
    FakeBrowserService,
    FakeScannerView,
    MemoryScanSuccessSoundService,
]:
    excel_service = ExcelService(str(data_path))
    browser_service = FakeBrowserService()
    settings = ReceiptSettings(show_qr=False, qr_scan_auto_print_enabled=True)
    sound_service = MemoryScanSuccessSoundService()
    scanner_view = FakeScannerView()

    app = app_main.Application.__new__(app_main.Application)
    app._state = app_main.AppState.READY
    app._excel_service = excel_service
    app._browser_service = browser_service
    app._api_service = ApiService()
    app._order_viewmodel = OrderViewModel(excel_service, browser_service)
    app._receipt_settings = settings
    app._settings_store = MemoryReceiptSettingsStore(settings)
    app._scan_success_sound_service = sound_service
    app._audio_service = None
    app._ticket_debug_tools_service = OfflineDebugToolsService()
    app._scanner_view = scanner_view
    app._order_view = None
    app._order_listener = None
    app._status_listener = None
    app._stop_requested = False
    app._relogin_requested = False
    return app, browser_service, scanner_view, sound_service


class OfflineTicketFlowE2ETest(unittest.TestCase):
    def test_generated_qr_completes_order_and_captures_receipt_without_real_io(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "test_orders.xlsx"
            _create_test_workbook(data_path)
            app, browser, scanner, sound = _build_offline_app(data_path)
            printer = FakePrinterBackend()

            decoded_qr = _decode_generated_qr(TEST_QR_URL)
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
            _create_test_workbook(data_path)
            app, _, scanner, sound = _build_offline_app(data_path)
            printer = FakePrinterBackend(failure=RuntimeError("가상 프린터 실패"))

            decoded_qr = _decode_generated_qr(TEST_QR_URL)
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


if __name__ == "__main__":
    unittest.main()
