"""Application control state regression tests."""
from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

import main as app_main
from services.windows_camera_service import FocusApplyResult


class ApplicationControlStateTest(unittest.TestCase):
    def _build_app(self) -> app_main.Application:
        app = app_main.Application.__new__(app_main.Application)
        app._control_lock = threading.Lock()
        app._stop_requested = False
        app._relogin_requested = False
        app._status_listener = None
        return app

    def test_request_relogin_is_ignored_after_stop(self) -> None:
        app = self._build_app()

        app.request_stop()
        app.request_relogin()

        self.assertTrue(app._is_stop_requested())
        self.assertFalse(app._consume_relogin_requested())

    def test_consume_relogin_requested_only_once(self) -> None:
        app = self._build_app()

        app.request_relogin()

        self.assertTrue(app._consume_relogin_requested())
        self.assertFalse(app._consume_relogin_requested())

    def test_request_stop_clears_pending_relogin(self) -> None:
        app = self._build_app()

        app.request_relogin()
        app.request_stop()

        self.assertTrue(app._is_stop_requested())
        self.assertFalse(app._consume_relogin_requested())

    def test_request_relogin_works_without_prebuilt_control_lock(self) -> None:
        app = app_main.Application.__new__(app_main.Application)
        app._stop_requested = False
        app._relogin_requested = False
        app._status_listener = None

        app.request_relogin()

        self.assertTrue(hasattr(app, "_control_lock"))
        self.assertTrue(app._consume_relogin_requested())

    def test_concurrent_stop_request_wins_over_relogin(self) -> None:
        app = self._build_app()
        start_barrier = threading.Barrier(3)

        def _request_stop_many_times() -> None:
            start_barrier.wait()
            for _ in range(200):
                app.request_stop()

        def _request_relogin_many_times() -> None:
            start_barrier.wait()
            for _ in range(200):
                app.request_relogin()

        stop_thread = threading.Thread(target=_request_stop_many_times)
        relogin_thread = threading.Thread(target=_request_relogin_many_times)
        stop_thread.start()
        relogin_thread.start()
        start_barrier.wait()
        stop_thread.join()
        relogin_thread.join()

        self.assertTrue(app._is_stop_requested())
        self.assertFalse(app._consume_relogin_requested())

    def test_wait_for_login_returns_false_without_timeout_state_when_stopped(self) -> None:
        app = self._build_app()
        transitions: list[str] = []

        def _wait_until_authenticated(timeout_sec: int = 1) -> bool:
            app.request_stop()
            return False

        app._browser_service = SimpleNamespace(wait_until_authenticated=_wait_until_authenticated)
        app._enter_ready = lambda *args, **kwargs: transitions.append("ready")
        app._enter_auth_wait = lambda *args, **kwargs: transitions.append("auth_wait")
        app._enter_error = lambda *args, **kwargs: transitions.append("error")

        result = app._wait_for_login(timeout_sec=5)

        self.assertFalse(result)
        self.assertEqual(transitions, [])

    def test_customer_log_mask_helpers_hide_name_and_phone(self) -> None:
        self.assertEqual(app_main._mask_name("홍영기"), "홍*기")
        self.assertEqual(app_main._mask_name("이"), "*")
        self.assertEqual(app_main._mask_phone("010-1234-5678"), "010-****-5678")
        self.assertEqual(app_main._mask_phone("0101234567"), "010-***-4567")


    def test_apply_scanner_focus_settings_updates_live_scanner_and_receipt_settings(self) -> None:
        app = self._build_app()
        focus_calls: list[tuple[str, object]] = []

        class _FakeScanner:
            def is_camera_ready(self) -> bool:
                return True

            def get_focus_capability(self):
                return SimpleNamespace(manual_focus_supported=True)

            def apply_focus_settings(self, mode, value):
                focus_calls.append((mode, value))
                return True

        app._scanner_view = _FakeScanner()
        app._receipt_settings = SimpleNamespace(
            scanner_focus_mode="auto",
            scanner_manual_focus_value=None,
        )

        message = app.apply_scanner_focus_settings("manual", 8.5)

        self.assertEqual(message, "카메라 초점 설정 저장 완료 (현재 런타임에 바로 적용됨)")
        self.assertEqual(focus_calls, [("manual", 8.5)])
        self.assertEqual(app._receipt_settings.scanner_focus_mode, "manual")
        self.assertEqual(app._receipt_settings.scanner_manual_focus_value, 8.5)

    def test_apply_scanner_focus_settings_defers_when_scanner_is_unavailable(self) -> None:
        app = self._build_app()
        app._scanner_view = None
        app._receipt_settings = SimpleNamespace(
            scanner_focus_mode="auto",
            scanner_manual_focus_value=None,
        )

        message = app.apply_scanner_focus_settings("manual", 8.5)

        self.assertEqual(message, "카메라 초점 설정 저장 완료 (다음 앱 시작 후 적용)")
        self.assertEqual(app._receipt_settings.scanner_focus_mode, "manual")
        self.assertEqual(app._receipt_settings.scanner_manual_focus_value, 8.5)

    def test_apply_scanner_focus_settings_keeps_auto_when_manual_value_is_missing(self) -> None:
        app = self._build_app()
        focus_calls: list[tuple[str, object]] = []

        class _FakeScanner:
            def is_camera_ready(self) -> bool:
                return True

            def get_focus_capability(self):
                return SimpleNamespace(manual_focus_supported=True)

            def apply_focus_settings(self, mode, value):
                focus_calls.append((mode, value))
                return True

        app._scanner_view = _FakeScanner()
        app._receipt_settings = SimpleNamespace(
            scanner_focus_mode="manual",
            scanner_manual_focus_value=8.5,
        )

        message = app.apply_scanner_focus_settings("manual", None)

        self.assertEqual(message, "수동 초점 값이 없어 자동 초점으로 유지됩니다.")
        self.assertEqual(focus_calls, [("auto", None)])
        self.assertEqual(app._receipt_settings.scanner_focus_mode, "auto")
        self.assertIsNone(app._receipt_settings.scanner_manual_focus_value)

    def test_apply_scanner_focus_settings_keeps_auto_when_manual_focus_is_unsupported(self) -> None:
        app = self._build_app()
        focus_calls: list[tuple[str, object]] = []

        class _FakeScanner:
            def is_camera_ready(self) -> bool:
                return True

            def get_focus_capability(self):
                return SimpleNamespace(manual_focus_supported=False)

            def apply_focus_settings(self, mode, value):
                focus_calls.append((mode, value))
                return True

        app._scanner_view = _FakeScanner()
        app._receipt_settings = SimpleNamespace(
            scanner_focus_mode="manual",
            scanner_manual_focus_value=8.5,
        )

        message = app.apply_scanner_focus_settings("manual", 8.5)

        self.assertEqual(message, "현재 카메라가 수동 초점을 지원하지 않아 자동 초점으로 유지됩니다.")
        self.assertEqual(focus_calls, [("auto", None)])
        self.assertEqual(app._receipt_settings.scanner_focus_mode, "auto")
        self.assertIsNone(app._receipt_settings.scanner_manual_focus_value)

    def test_apply_scanner_focus_settings_preserves_manual_choice_after_transient_failure(self) -> None:
        app = self._build_app()

        class _FakeScanner:
            def is_camera_ready(self) -> bool:
                return True

            def get_focus_capability(self):
                return SimpleNamespace(manual_focus_supported=None)

            def apply_focus_settings(self, _mode, _value):
                return FocusApplyResult(status="failed")

        app._scanner_view = _FakeScanner()
        app._receipt_settings = SimpleNamespace(
            scanner_focus_mode="auto",
            scanner_manual_focus_value=None,
        )

        message = app.apply_scanner_focus_settings("manual", 8.5)

        self.assertEqual(
            message,
            "수동 초점을 적용하지 못했습니다. 다시 시도하거나 카메라 고급 설정을 확인하세요.",
        )
        self.assertEqual(app._receipt_settings.scanner_focus_mode, "manual")
        self.assertEqual(app._receipt_settings.scanner_manual_focus_value, 8.5)

    def test_apply_scanner_focus_settings_rejects_non_finite_manual_value(self) -> None:
        app = self._build_app()
        focus_calls: list[tuple[str, object]] = []
        app._scanner_view = SimpleNamespace(
            is_camera_ready=lambda: True,
            get_focus_capability=lambda: SimpleNamespace(manual_focus_supported=True),
            apply_focus_settings=lambda mode, value: focus_calls.append((mode, value)) or True,
        )
        app._receipt_settings = SimpleNamespace(
            scanner_focus_mode="manual",
            scanner_manual_focus_value=8.5,
        )

        message = app.apply_scanner_focus_settings("manual", float("nan"))

        self.assertEqual(message, "수동 초점 값이 올바르지 않아 자동 초점으로 유지됩니다.")
        self.assertEqual(focus_calls, [("auto", None)])
        self.assertEqual(app._receipt_settings.scanner_focus_mode, "auto")
        self.assertIsNone(app._receipt_settings.scanner_manual_focus_value)

    def test_get_scanner_focus_capability_proxies_to_scanner(self) -> None:
        app = self._build_app()
        capability = SimpleNamespace(manual_focus_supported=False)
        app._scanner_view = SimpleNamespace(get_focus_capability=lambda: capability)

        self.assertIs(app.get_scanner_focus_capability(), capability)

    def test_get_scanner_focus_capability_returns_none_without_scanner(self) -> None:
        app = self._build_app()
        app._scanner_view = None

        self.assertIsNone(app.get_scanner_focus_capability())

    def test_open_scanner_camera_settings_proxies_to_scanner(self) -> None:
        app = self._build_app()
        app._scanner_view = SimpleNamespace(open_camera_settings=lambda: True)

        self.assertTrue(app.open_scanner_camera_settings())

    def test_open_scanner_camera_settings_returns_false_without_scanner(self) -> None:
        app = self._build_app()
        app._scanner_view = None

        self.assertFalse(app.open_scanner_camera_settings())


if __name__ == "__main__":
    unittest.main()
