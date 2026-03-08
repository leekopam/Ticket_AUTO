"""QR input validation regression tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import main as app_main


class _FakeScannerView:
    def __init__(self) -> None:
        self.status_message = ""
        self.scanning_enabled = True
        self.auth_ready = True

    def set_status_message(self, message: str) -> None:
        self.status_message = message

    def set_scanning_enabled(self, enabled: bool) -> None:
        self.scanning_enabled = enabled

    def set_auth_ready(self, ready: bool) -> None:
        self.auth_ready = ready


class _FailIfCalledBrowserService:
    def resolve_qr_redirect(self, _qr_url: str):
        raise AssertionError("browser request should not be called for invalid QR input")


class QRInputValidationTest(unittest.TestCase):
    def _build_app(self) -> app_main.Application:
        app = app_main.Application.__new__(app_main.Application)
        app._state = app_main.AppState.READY
        app._scanner_view = _FakeScannerView()
        app._browser_service = _FailIfCalledBrowserService()
        app._api_service = SimpleNamespace()
        app._status_listener = None
        app._stop_requested = False
        return app

    def test_invalid_url_is_rejected_before_browser_request(self) -> None:
        app = self._build_app()

        app._process_qr("not-a-url", allow_auth_retry=False)

        self.assertEqual(app._state, app_main.AppState.ERROR)
        self.assertEqual(app._scanner_view.status_message, "유효한 QR URL이 아닙니다.")

    def test_non_witchform_url_is_rejected_before_browser_request(self) -> None:
        app = self._build_app()

        app._process_qr("https://example.com/qr?id=1", allow_auth_retry=False)

        self.assertEqual(app._state, app_main.AppState.ERROR)
        self.assertEqual(app._scanner_view.status_message, "유효한 Witchform QR 코드가 아닙니다.")


if __name__ == "__main__":
    unittest.main()
