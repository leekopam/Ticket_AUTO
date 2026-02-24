"""메인 앱의 수령완료/출력 플로우 분기 테스트."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from models.order_model import Order
from services.api_service import QRParseResult
from services.browser_service import BrowserResolveResult, ReceiptClickResult

import main as app_main


class _FakeScannerView:
    def __init__(self):
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
    def __init__(self):
        self.last_order: Order | None = None

    def show_or_update(self, order: Order) -> None:
        self.last_order = order


class _FakeOrderViewModel:
    def __init__(
        self,
        *,
        order: Order,
        click_result: ReceiptClickResult | None = None,
        mark_ok: bool = True,
        rollback_ok: bool = True,
    ):
        self.order = order
        self.click_result = click_result or ReceiptClickResult(success=True)
        self.mark_ok = mark_ok
        self.rollback_ok = rollback_ok

        self.load_calls = 0
        self.open_calls = 0
        self.complete_calls = 0
        self.mark_calls = 0
        self.rollback_calls = 0

    def load_order(self, order_number: str, url: str, *, open_page: bool = True) -> Order | None:
        self.load_calls += 1
        self.order.order_number = order_number
        self.order.url = url
        return self.order

    def open_current_order_page(self) -> bool:
        self.open_calls += 1
        return True

    def complete_receipt(self) -> ReceiptClickResult:
        self.complete_calls += 1
        return self.click_result

    def mark_current_order_received(self, timestamp_str: str | None = None) -> bool:
        self.mark_calls += 1
        if self.mark_ok:
            self.order.received_at = timestamp_str or ""
        return self.mark_ok

    def rollback_current_order_received(self, previous_value: str) -> bool:
        self.rollback_calls += 1
        if self.rollback_ok:
            self.order.received_at = previous_value
        return self.rollback_ok


def _build_app_with_order_vm(order_vm: _FakeOrderViewModel):
    app = app_main.Application.__new__(app_main.Application)
    app._state = app_main.AppState.READY
    app._scanner_view = _FakeScannerView()
    app._order_view = _FakeOrderView()
    app._order_viewmodel = order_vm
    app._receipt_settings = SimpleNamespace()
    app._api_service = SimpleNamespace(
        parse_qr_redirect=lambda *_args, **_kwargs: QRParseResult(
            success=True,
            order_number="ORDER-001",
            full_url="https://example.com/order/1",
        )
    )
    return app


class AppPrintFlowTest(unittest.TestCase):
    def test_success_flow_marks_and_prints(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)

        with patch("main.print_order_receipt") as print_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(order_vm.complete_calls, 1)
        self.assertEqual(order_vm.mark_calls, 1)
        self.assertEqual(order_vm.rollback_calls, 0)
        print_mock.assert_called_once()
        self.assertEqual(app._state, app_main.AppState.READY)

    def test_print_failure_rolls_back_mark(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order, rollback_ok=True)
        app = _build_app_with_order_vm(order_vm)

        with patch("main.print_order_receipt", side_effect=RuntimeError("printer down")):
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(order_vm.mark_calls, 1)
        self.assertEqual(order_vm.rollback_calls, 1)
        self.assertEqual(app._state, app_main.AppState.ERROR)
        self.assertIn("원복", app._scanner_view.status_message)

    def test_received_order_skips_click_and_print(self) -> None:
        order = Order(
            order_number="ORDER-001",
            name="홍길동",
            phone="010",
            seat="A-1",
            goods=[],
            received_at="2026-02-23 10:00:00",
        )
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)

        with patch("main.print_order_receipt") as print_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(order_vm.open_calls, 0)
        self.assertEqual(order_vm.complete_calls, 0)
        self.assertEqual(order_vm.mark_calls, 0)
        print_mock.assert_not_called()
        self.assertEqual(app._state, app_main.AppState.READY)
        self.assertIn("이미 수령완료", app._scanner_view.status_message)


if __name__ == "__main__":
    unittest.main()
