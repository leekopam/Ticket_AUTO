from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main as app_main
from models.order_model import Order
from services.api_service import QRParseResult
from services.browser_service import BrowserResolveResult, ReceiptClickResult


class _FakeScannerView:
    def __init__(self) -> None:
        self.auth_ready = True
        self.scanning_enabled = True
        self.status_message = ""

    def set_auth_ready(self, ready: bool) -> None:
        self.auth_ready = ready

    def set_scanning_enabled(self, enabled: bool) -> None:
        self.scanning_enabled = enabled

    def set_status_message(self, message: str) -> None:
        self.status_message = message


class _FakeOrderView:
    def __init__(self) -> None:
        self.last_order: Order | None = None

    def show_or_update(self, order: Order) -> None:
        self.last_order = order


class _FakeOrderViewModel:
    def __init__(self, order: Order) -> None:
        self.order = order
        self.load_calls: list[tuple[str, str, bool]] = []
        self.open_calls = 0
        self.complete_calls = 0
        self.mark_calls = 0

    def load_order(self, order_number: str, url: str, *, open_page: bool = True) -> Order | None:
        self.load_calls.append((order_number, url, open_page))
        self.order.order_number = order_number
        self.order.url = url
        return self.order

    def open_current_order_page(self) -> bool:
        self.open_calls += 1
        return True

    def complete_receipt(self) -> ReceiptClickResult:
        self.complete_calls += 1
        return ReceiptClickResult(success=True)

    def mark_current_order_received(self, timestamp_str: str | None = None) -> bool:
        self.mark_calls += 1
        self.order.received_at = timestamp_str or ""
        return True

    def rollback_current_order_received(self, previous_value: str) -> bool:
        self.order.received_at = previous_value
        return True


def _build_app(
    *,
    parse_result: QRParseResult,
    discovered_order_number: str = "",
    discovery_context: object | None = None,
    excel_matches: list[Order] | None = None,
) -> tuple[app_main.Application, _FakeOrderViewModel]:
    app = app_main.Application.__new__(app_main.Application)
    app._state = app_main.AppState.READY
    app._scanner_view = _FakeScannerView()
    app._order_view = _FakeOrderView()
    app._receipt_settings = SimpleNamespace()
    app._api_service = SimpleNamespace(
        WITCHFORM_BASE="https://witchform.com",
        parse_qr_redirect=lambda *_args, **_kwargs: parse_result,
    )

    browser_service = SimpleNamespace(
        discover_order_number_from_page=lambda *_args, **_kwargs: discovered_order_number,
    )
    if discovery_context is not None:
        browser_service.discover_order_context_from_page = lambda *_args, **_kwargs: discovery_context
    app._browser_service = browser_service
    app._excel_service = SimpleNamespace(
        find_orders_by_customer=lambda *_args, **_kwargs: list(excel_matches or []),
    )

    order_vm = _FakeOrderViewModel(
        Order(order_number="", name="Test", phone="010", seat="A-1", goods=[])
    )
    app._order_viewmodel = order_vm
    return app, order_vm


class QROrderNumberRecoveryTest(unittest.TestCase):
    def test_missing_order_number_uses_browser_page_fallback(self) -> None:
        app, order_vm = _build_app(
            parse_result=QRParseResult(
                success=False,
                error_code="ORDER_NUMBER_MISSING",
                error_message="주문번호를 QR 리다이렉트에서 찾을 수 없습니다.",
            ),
            discovered_order_number="WFLM7QSDTC_69D53CU23685",
        )

        with patch("main.print_order_receipt") as print_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(
                    ok=True,
                    status_code=302,
                    location="/w/myform/sellForm-history-detail/14872765",
                ),
                allow_auth_retry=False,
            )

        self.assertEqual(
            order_vm.load_calls,
            [
                (
                    "WFLM7QSDTC_69D53CU23685",
                    "https://witchform.com/w/myform/sellForm-history-detail/14872765",
                    False,
                )
            ],
        )
        self.assertEqual(order_vm.complete_calls, 1)
        self.assertEqual(order_vm.mark_calls, 1)
        self.assertEqual(app._state, app_main.AppState.READY)
        print_mock.assert_called_once()

    def test_missing_order_number_reports_original_error_when_fallback_fails(self) -> None:
        app, order_vm = _build_app(
            parse_result=QRParseResult(
                success=False,
                error_code="ORDER_NUMBER_MISSING",
                error_message="주문번호를 QR 리다이렉트에서 찾을 수 없습니다.",
            ),
            discovered_order_number="",
        )

        with patch("main.print_order_receipt") as print_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(
                    ok=True,
                    status_code=302,
                    location="/w/myform/sellForm-history-detail/14872765",
                ),
                allow_auth_retry=False,
            )

        self.assertEqual(order_vm.load_calls, [])
        self.assertEqual(app._state, app_main.AppState.ERROR)
        self.assertIn("주문번호를 QR 리다이렉트에서 찾을 수 없습니다.", app._scanner_view.status_message)
        print_mock.assert_not_called()

    def test_missing_order_number_can_recover_from_customer_match(self) -> None:
        matched_order = Order(
            order_number="WFLM7QSDTC_69D53CU23685",
            name="홍영기",
            phone="010-1234-5678",
            seat="A-1",
            goods=[],
        )
        app, order_vm = _build_app(
            parse_result=QRParseResult(
                success=False,
                error_code="ORDER_NUMBER_MISSING",
                error_message="주문번호를 QR 리다이렉트에서 찾을 수 없습니다.",
            ),
            discovery_context=SimpleNamespace(
                order_number="",
                buyer_name="홍영기",
                buyer_phone="010-1234-5678",
                url="https://witchform.com/w/myform/sellForm-history-detail/14872765",
            ),
            excel_matches=[matched_order],
        )

        with patch("main.print_order_receipt") as print_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(
                    ok=True,
                    status_code=302,
                    location="/w/myform/sellForm-history-detail/14872765",
                ),
                allow_auth_retry=False,
            )

        self.assertEqual(
            order_vm.load_calls,
            [
                (
                    "WFLM7QSDTC_69D53CU23685",
                    "https://witchform.com/w/myform/sellForm-history-detail/14872765",
                    False,
                )
            ],
        )
        self.assertEqual(app._state, app_main.AppState.READY)
        print_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
