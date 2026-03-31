"""인증/쿠키 계약 회귀 테스트."""
from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from services.api_service import ApiService
from services.browser_service import BrowserService
from views.scanner_view import ScannerView


class AuthContractTest(unittest.TestCase):
    """Playwright 쿠키 단일 소스 계약이 유지되는지 검증한다."""

    def test_browser_service_default_requires_login_each_run(self) -> None:
        """BrowserService 기본 정책이 실행당 로그인 강제인지 확인한다."""
        signature = inspect.signature(BrowserService.__init__)
        default_value = signature.parameters["require_login_each_run"].default
        self.assertTrue(default_value)

    def test_browser_service_headless_defaults_to_false(self) -> None:
        """BrowserService 기본 브라우저 모드는 headed인지 확인한다."""
        signature = inspect.signature(BrowserService.__init__)
        self.assertIn("headless", signature.parameters)
        self.assertFalse(signature.parameters["headless"].default)

    def test_main_sets_require_login_each_run_true(self) -> None:
        """애플리케이션 초기화에서 정책 값을 명시적으로 고정하는지 확인한다."""
        source = Path("main.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)

        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "BrowserService":
                continue

            for keyword in node.keywords:
                if keyword.arg == "require_login_each_run":
                    found = isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                    break

        self.assertTrue(found, "main.py에서 require_login_each_run=True를 명시해야 합니다.")

    def test_main_has_no_session_model_dependency(self) -> None:
        """main.py에 SessionModel/session 백업 경로가 없는지 확인한다."""
        source = Path("main.py").read_text(encoding="utf-8-sig")
        self.assertNotIn("SessionModel", source)
        self.assertNotIn("_backup_session_cookie", source)
        self.assertNotIn("session.txt", source)

    def test_resolve_qr_redirect_does_not_accept_cookie_or_header_input(self) -> None:
        """외부에서 쿠키/헤더를 주입할 수 없는 시그니처인지 확인한다."""
        signature = inspect.signature(BrowserService.resolve_qr_redirect)
        self.assertNotIn("cookies", signature.parameters)
        self.assertNotIn("headers", signature.parameters)

    def test_resolve_request_options_are_fixed_and_safe(self) -> None:
        """QR 요청 옵션이 고정되어 있고 쿠키/헤더 키가 없는지 확인한다."""
        options = BrowserService._build_qr_resolve_request_options(timeout_ms=8000)
        self.assertEqual(
            set(options.keys()),
            {"max_redirects", "fail_on_status_code", "timeout"},
        )
        self.assertNotIn("cookies", options)
        self.assertNotIn("headers", options)

    def test_browser_service_exposes_runtime_safe_auth_cookie_replacement(self) -> None:
        """인증 쿠키 lifecycle smoke를 위한 런타임 안전 API가 존재하는지 확인한다."""
        signature = inspect.signature(BrowserService.replace_auth_cookie_snapshot)
        self.assertIn("snapshot", signature.parameters)

    def test_api_service_is_parser_only_without_session_methods(self) -> None:
        """ApiService에 세션 주입 메서드가 재도입되지 않았는지 확인한다."""
        self.assertFalse(hasattr(ApiService, "set_session"))
        service = ApiService()
        self.assertFalse(any("session" in key.lower() for key in vars(service).keys()))

    def test_scanner_supports_runtime_status_font_update(self) -> None:
        """ScannerView 폰트 변경 인터페이스가 존재하는지 확인한다."""
        init_sig = inspect.signature(ScannerView.__init__)
        self.assertIn("status_font_path", init_sig.parameters)
        self.assertIn("status_font_size", init_sig.parameters)
        self.assertIn("focus_mode", init_sig.parameters)
        self.assertIn("manual_focus_value", init_sig.parameters)
        self.assertTrue(hasattr(ScannerView, "set_status_font"))

    def test_main_passes_saved_scanner_focus_settings_to_scanner_view(self) -> None:
        """Application이 저장된 초점 설정값을 ScannerView 생성자에 전달하는지 확인한다."""
        source = Path("main.py").read_text(encoding="utf-8-sig")
        self.assertIn("focus_mode=self._receipt_settings.scanner_focus_mode", source)
        self.assertIn(
            "manual_focus_value=self._receipt_settings.scanner_manual_focus_value",
            source,
        )


if __name__ == "__main__":
    unittest.main()
