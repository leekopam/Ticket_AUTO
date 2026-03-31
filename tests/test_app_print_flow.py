"""Main receipt flow tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from models.order_model import Order
from services.api_service import QRParseResult
from services.browser_service import BrowserResolveResult, ReceiptClickResult

import main as app_main


_USE_DEFAULT_LOAD_RESULT = object()


class _FakeScannerView:
    def __init__(self) -> None:
        self.auth_ready = True
        self.scanning_enabled = True
        self.status_message = ""
        self.started = False
        self.released = False

    def set_auth_ready(self, ready: bool) -> None:
        self.auth_ready = ready

    def set_scanning_enabled(self, enabled: bool) -> None:
        self.scanning_enabled = enabled

    def set_status_message(self, message: str) -> None:
        self.status_message = message

    def start(self) -> None:
        self.started = True

    def release(self) -> None:
        self.released = True

    def is_camera_ready(self) -> bool:
        return True

    def is_running(self) -> bool:
        return self.started and not self.released


class _FakeOrderView:
    def __init__(self) -> None:
        self.last_order: Order | None = None

    def show_or_update(self, order: Order) -> None:
        self.last_order = order


class _FakeOrderViewModel:
    def __init__(
        self,
        *,
        order: Order,
        click_result: ReceiptClickResult | None = None,
        load_result: Order | None | object = _USE_DEFAULT_LOAD_RESULT,
        open_ok: bool = True,
        mark_ok: bool = True,
        rollback_ok: bool = True,
    ) -> None:
        self.order = order
        self.click_result = click_result or ReceiptClickResult(success=True)
        self.load_result = order if load_result is _USE_DEFAULT_LOAD_RESULT else load_result
        self.open_ok = open_ok
        self.mark_ok = mark_ok
        self.rollback_ok = rollback_ok
        self.load_calls = 0
        self.open_calls = 0
        self.complete_calls = 0
        self.mark_calls = 0
        self.rollback_calls = 0

    def load_order(self, order_number: str, url: str, *, open_page: bool = True) -> Order | None:
        self.load_calls += 1
        if self.load_result is None:
            return None
        self.order.order_number = order_number
        self.order.url = url
        return self.order

    def open_current_order_page(self) -> bool:
        self.open_calls += 1
        return self.open_ok

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
    app._receipt_settings = SimpleNamespace(qr_scan_success_sound_path="")
    app._api_service = SimpleNamespace(
        parse_qr_redirect=lambda *_args, **_kwargs: QRParseResult(
            success=True,
            order_number="ORDER-001",
            full_url="https://example.com/order/1",
        )
    )
    app._order_listener = None
    app._status_listener = None
    app._stop_requested = False
    app._settings_store = SimpleNamespace(load=lambda: app._receipt_settings)
    app._audio_service = None
    app._ticket_debug_tools_service = SimpleNamespace(
        load_settings=lambda: SimpleNamespace(
            count_scan_success_as_processed=False,
            play_sound_for_duplicate_received_qr=False,
        ),
        should_count_scan_success_as_processed=lambda settings=None: bool(
            getattr(settings, "count_scan_success_as_processed", False)
        ),
        should_play_sound_for_duplicate_received_qr=lambda settings=None: bool(
            getattr(settings, "play_sound_for_duplicate_received_qr", False)
        ),
    )
    return app


class AppPrintFlowTest(unittest.TestCase):
    def test_success_flow_marks_prints_and_plays_sound(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)

        with (
            patch("main.print_order_receipt") as print_mock,
            patch.object(app, "_play_scan_success_sound") as sound_mock,
            patch.object(app, "_commit_scan_success_count") as commit_mock,
        ):
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(order_vm.complete_calls, 1)
        self.assertEqual(order_vm.mark_calls, 1)
        self.assertEqual(order_vm.rollback_calls, 0)
        print_mock.assert_called_once()
        sound_mock.assert_called_once_with(order, increment_count=True, persist_count=False)
        commit_mock.assert_called_once_with()
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

    def test_received_order_skips_click_print_and_is_silent_by_default(self) -> None:
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

        with patch("main.print_order_receipt") as print_mock, patch.object(app, "_play_scan_success_sound") as sound_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(order_vm.open_calls, 0)
        self.assertEqual(order_vm.complete_calls, 0)
        self.assertEqual(order_vm.mark_calls, 0)
        print_mock.assert_not_called()
        sound_mock.assert_not_called()
        self.assertEqual(app._state, app_main.AppState.READY)
        self.assertIs(app._order_view.last_order, order)
        self.assertIn("이미 수령완료", app._scanner_view.status_message)

    def test_load_order_missing_reports_error_without_side_effects(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order, load_result=None)
        app = _build_app_with_order_vm(order_vm)

        with patch("main.print_order_receipt") as print_mock, patch.object(app, "_play_scan_success_sound") as sound_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(app._state, app_main.AppState.ERROR)
        self.assertEqual(order_vm.open_calls, 0)
        self.assertEqual(order_vm.complete_calls, 0)
        self.assertEqual(order_vm.mark_calls, 0)
        print_mock.assert_not_called()
        sound_mock.assert_not_called()
        self.assertIn("주문번호를 찾을 수 없습니다", app._scanner_view.status_message)

    def test_already_received_click_result_marks_order_and_is_silent_by_default(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(
            order=order,
            click_result=ReceiptClickResult(
                success=False,
                error_code="ALREADY_RECEIVED",
                error_message="이미 수령완료 처리된 주문입니다.",
            ),
        )
        app = _build_app_with_order_vm(order_vm)

        with patch.object(app, "_play_scan_success_sound") as sound_mock, patch("main.print_order_receipt") as print_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(order_vm.complete_calls, 1)
        self.assertEqual(order_vm.mark_calls, 1)
        print_mock.assert_not_called()
        sound_mock.assert_not_called()
        self.assertEqual(app._state, app_main.AppState.READY)
        self.assertIn("이미 수령완료", app._scanner_view.status_message)

    def test_open_current_order_page_failure_reports_error_without_receipt_side_effects(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order, open_ok=False)
        app = _build_app_with_order_vm(order_vm)

        with patch("main.print_order_receipt") as print_mock, patch.object(app, "_play_scan_success_sound") as sound_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(app._state, app_main.AppState.ERROR)
        self.assertEqual(order_vm.open_calls, 1)
        self.assertEqual(order_vm.complete_calls, 0)
        self.assertEqual(order_vm.mark_calls, 0)
        print_mock.assert_not_called()
        sound_mock.assert_not_called()
        self.assertEqual(app._scanner_view.status_message, "주문 상세 페이지를 열 수 없습니다.")

    def test_click_failure_reports_generic_receipt_failure_status_message(self) -> None:
        order = Order(order_number="ORDER-001", name="TEST USER", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(
            order=order,
            click_result=ReceiptClickResult(
                success=False,
                error_code="PRIMARY_CLICK_FAIL",
                error_message="수령 완료 1차 버튼 클릭 실패: timeout",
            ),
        )
        app = _build_app_with_order_vm(order_vm)

        with patch.object(app, "_play_scan_success_sound") as sound_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(app._state, app_main.AppState.ERROR)
        self.assertIn("수령 완료 처리 상태를 확인하지 못했습니다", app._scanner_view.status_message)
        sound_mock.assert_called_once_with(order, increment_count=False)

    def test_mark_current_order_received_failure_stops_before_print_and_sound(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order, mark_ok=False)
        app = _build_app_with_order_vm(order_vm)

        with patch("main.print_order_receipt") as print_mock, patch.object(app, "_play_scan_success_sound") as sound_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(app._state, app_main.AppState.ERROR)
        self.assertEqual(order_vm.complete_calls, 1)
        self.assertEqual(order_vm.mark_calls, 1)
        print_mock.assert_not_called()
        sound_mock.assert_not_called()
        self.assertIn("수령확인 저장 실패", app._scanner_view.status_message)

    def test_browser_failure_uses_user_friendly_status_message(self) -> None:
        order = Order(order_number="ORDER-001", name="TEST USER", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)

        app._process_resolved_qr(
            "https://witchform.com/qrcode_link.php?a=1",
            BrowserResolveResult(
                ok=False,
                error_code="NETWORK_ERROR",
                error_message="브라우저 요청 실패: APIRequestContext.get: Target page closed",
            ),
            allow_auth_retry=False,
        )

        self.assertEqual(app._state, app_main.AppState.ERROR)
        self.assertEqual(
            app._scanner_view.status_message,
            "브라우저 요청을 확인하지 못했습니다. 다시 스캔해주세요.",
        )

    def test_success_flow_keeps_working_without_order_window(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)
        app._order_view = None

        with patch("main.print_order_receipt") as print_mock, patch.object(app, "_play_scan_success_sound") as sound_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(app._state, app_main.AppState.READY)
        self.assertEqual(order_vm.complete_calls, 1)
        self.assertEqual(order_vm.mark_calls, 1)
        print_mock.assert_called_once()
        sound_mock.assert_called_once_with(order, increment_count=True, persist_count=False)

    def test_listener_failures_are_logged_without_breaking_success_flow(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)

        def _broken_order_listener(_order: Order) -> None:
            raise RuntimeError("order callback boom")

        def _broken_status_listener(_state: str, _message: str) -> None:
            raise RuntimeError("status callback boom")

        app._order_listener = _broken_order_listener
        app._status_listener = _broken_status_listener

        with (
            self.assertLogs("main", level="WARNING") as captured,
            patch("main.print_order_receipt") as print_mock,
            patch.object(app, "_play_scan_success_sound") as sound_mock,
        ):
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(app._state, app_main.AppState.READY)
        print_mock.assert_called_once()
        sound_mock.assert_called_once_with(order, increment_count=True, persist_count=False)
        self.assertTrue(any("주문 콜백 처리 실패" in line for line in captured.output))
        self.assertTrue(any("상태 콜백 처리 실패" in line for line in captured.output))

    def test_network_timeout_retries_once_and_reprocesses_success(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)
        retry_result = BrowserResolveResult(ok=True, status_code=302, location="/orders/1")
        reprocessed: list[tuple[str, BrowserResolveResult, bool]] = []
        errors: list[str] = []

        app._browser_service = SimpleNamespace(resolve_qr_redirect=lambda _qr_url: retry_result)
        app._process_resolved_qr = lambda qr_url, browser_result, allow_auth_retry: reprocessed.append(
            (qr_url, browser_result, allow_auth_retry)
        )
        app._enter_error = lambda message, keep_auth_state=True: errors.append(message)

        app._handle_browser_failure(
            BrowserResolveResult(
                ok=False,
                error_code="NETWORK_TIMEOUT",
                error_message="timeout",
            ),
            "https://witchform.com/qrcode_link.php?a=1",
            allow_auth_retry=True,
        )

        self.assertEqual(errors, [])
        self.assertEqual(
            reprocessed,
            [("https://witchform.com/qrcode_link.php?a=1", retry_result, False)],
        )

    def test_network_timeout_reports_fixed_error_when_retry_also_fails(self) -> None:
        order = Order(order_number="ORDER-001", name="홍길동", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)
        errors: list[str] = []

        app._browser_service = SimpleNamespace(
            resolve_qr_redirect=lambda _qr_url: BrowserResolveResult(
                ok=False,
                error_code="NETWORK_TIMEOUT",
                error_message="timeout",
            )
        )
        app._enter_error = lambda message, keep_auth_state=True: errors.append(message)

        app._handle_browser_failure(
            BrowserResolveResult(
                ok=False,
                error_code="NETWORK_TIMEOUT",
                error_message="timeout",
            ),
            "https://witchform.com/qrcode_link.php?a=1",
            allow_auth_retry=True,
        )

        self.assertEqual(errors, ["네트워크 시간 초과가 발생했습니다"])


    def test_received_order_can_play_general_sound_when_debug_duplicate_sound_enabled(self) -> None:
        order = Order(
            order_number="ORDER-001",
            name="TEST USER",
            phone="010",
            seat="A-1",
            goods=[],
            received_at="2026-02-23 10:00:00",
        )
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)
        app._ticket_debug_tools_service = SimpleNamespace(
            load_settings=lambda: SimpleNamespace(
                count_scan_success_as_processed=False,
                play_sound_for_duplicate_received_qr=True,
            ),
            should_play_sound_for_duplicate_received_qr=lambda settings=None: bool(
                getattr(settings, "play_sound_for_duplicate_received_qr", False)
            ),
        )

        with patch("main.print_order_receipt") as print_mock, patch.object(app, "_play_scan_success_sound") as sound_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(order_vm.open_calls, 0)
        self.assertEqual(order_vm.complete_calls, 0)
        self.assertEqual(order_vm.mark_calls, 0)
        print_mock.assert_not_called()
        sound_mock.assert_called_once_with(order, increment_count=False, persist_count=True)
        self.assertEqual(app._state, app_main.AppState.READY)

    def test_received_order_can_increment_count_and_play_counted_sound_when_debug_count_enabled(self) -> None:
        order = Order(
            order_number="ORDER-001",
            name="TEST USER",
            phone="010",
            seat="A-1",
            goods=[],
            received_at="2026-02-23 10:00:00",
        )
        order_vm = _FakeOrderViewModel(order=order)
        app = _build_app_with_order_vm(order_vm)
        app._ticket_debug_tools_service = SimpleNamespace(
            load_settings=lambda: SimpleNamespace(
                count_scan_success_as_processed=True,
                play_sound_for_duplicate_received_qr=False,
            ),
            should_play_sound_for_duplicate_received_qr=lambda settings=None: bool(
                getattr(settings, "play_sound_for_duplicate_received_qr", False)
            ),
            should_count_scan_success_as_processed=lambda settings=None: bool(
                getattr(settings, "count_scan_success_as_processed", False)
            ),
        )

        with patch.object(app, "_commit_scan_success_count") as commit_mock, patch.object(
            app, "_play_scan_success_sound"
        ) as sound_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        sound_mock.assert_called_once_with(order, increment_count=True, persist_count=False)
        commit_mock.assert_called_once_with()
        self.assertEqual(app._state, app_main.AppState.READY)

    def test_already_received_click_result_can_play_general_sound_when_debug_duplicate_sound_enabled(self) -> None:
        order = Order(order_number="ORDER-001", name="TEST USER", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(
            order=order,
            click_result=ReceiptClickResult(
                success=False,
                error_code="ALREADY_RECEIVED",
                error_message="?대? ?섎졊?꾨즺 泥섎━??二쇰Ц?낅땲??",
            ),
        )
        app = _build_app_with_order_vm(order_vm)
        app._ticket_debug_tools_service = SimpleNamespace(
            load_settings=lambda: SimpleNamespace(
                count_scan_success_as_processed=False,
                play_sound_for_duplicate_received_qr=True,
            ),
            should_play_sound_for_duplicate_received_qr=lambda settings=None: bool(
                getattr(settings, "play_sound_for_duplicate_received_qr", False)
            ),
        )

        with patch.object(app, "_play_scan_success_sound") as sound_mock, patch("main.print_order_receipt") as print_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        self.assertEqual(order_vm.complete_calls, 1)
        self.assertEqual(order_vm.mark_calls, 1)
        print_mock.assert_not_called()
        sound_mock.assert_called_once_with(order, increment_count=False, persist_count=True)
        self.assertEqual(app._state, app_main.AppState.READY)

    def test_already_received_click_result_can_increment_count_and_play_counted_sound_when_debug_count_enabled(self) -> None:
        order = Order(order_number="ORDER-001", name="TEST USER", phone="010", seat="A-1", goods=[])
        order_vm = _FakeOrderViewModel(
            order=order,
            click_result=ReceiptClickResult(
                success=False,
                error_code="ALREADY_RECEIVED",
                error_message="이미 수령완료 처리된 주문입니다.",
            ),
        )
        app = _build_app_with_order_vm(order_vm)
        app._ticket_debug_tools_service = SimpleNamespace(
            load_settings=lambda: SimpleNamespace(
                count_scan_success_as_processed=True,
                play_sound_for_duplicate_received_qr=False,
            ),
            should_play_sound_for_duplicate_received_qr=lambda settings=None: bool(
                getattr(settings, "play_sound_for_duplicate_received_qr", False)
            ),
            should_count_scan_success_as_processed=lambda settings=None: bool(
                getattr(settings, "count_scan_success_as_processed", False)
            ),
        )

        with patch.object(app, "_commit_scan_success_count") as commit_mock, patch.object(
            app, "_play_scan_success_sound"
        ) as sound_mock, patch("main.print_order_receipt") as print_mock:
            app._process_resolved_qr(
                "https://witchform.com/qrcode_link.php?a=1",
                BrowserResolveResult(ok=True, status_code=302, location="/x"),
                allow_auth_retry=False,
            )

        print_mock.assert_not_called()
        sound_mock.assert_called_once_with(order, increment_count=True, persist_count=False)
        commit_mock.assert_called_once_with()
        self.assertEqual(app._state, app_main.AppState.READY)


if __name__ == "__main__":
    unittest.main()
