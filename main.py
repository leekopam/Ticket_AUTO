"""
QR scanner based order receipt automation application.
"""
from __future__ import annotations

from models.session_model import SessionModel
from services.api_service import ApiService
from services.browser_service import BrowserResolveResult, BrowserService
from services.excel_service import ExcelService
from viewmodels.order_viewmodel import OrderViewModel
from views.error_view import ErrorView
from views.order_view import OrderView
from views.scanner_view import ScannerView


class Application:
    """Order receipt automation application."""

    def __init__(self):
        self._session = SessionModel()
        self._excel_service = ExcelService("data.xlsx")
        self._browser_service = BrowserService()
        self._api_service = ApiService()

        self._order_viewmodel = OrderViewModel(self._excel_service, self._browser_service)

        self._scanner_view = ScannerView()
        self._order_view = OrderView(self._order_viewmodel)
        self._error_view = ErrorView()

        self._browser_service.set_on_receipt_complete(self._on_receipt_complete)

    def _on_receipt_complete(self) -> None:
        print("수령완료 처리 완료 - 스캐너 재활성화")
        self._scanner_view.set_scanning_enabled(True)

    def run(self) -> None:
        try:
            self._browser_service.start()
            if not self._authenticate_with_retry():
                self._error_view.show("로그인 확인에 실패했습니다. 프로그램을 종료합니다.")
                return

            self._main_loop()
        finally:
            self._browser_service.stop()
            self._scanner_view.release()

    def _authenticate_with_retry(self) -> bool:
        try:
            first_attempt = self._browser_service.ensure_authenticated(timeout_sec=180)
        except Exception as exc:
            print(f"AUTH_CHECK_FAILED: {exc}")
            first_attempt = False

        if first_attempt:
            self._backup_session_cookie()
            return True

        self._error_view.show("로그인이 필요합니다. 브라우저에서 로그인한 후 창을 닫아주세요.")

        try:
            second_attempt = self._browser_service.ensure_authenticated(timeout_sec=180)
        except Exception as exc:
            print(f"AUTH_CHECK_FAILED: {exc}")
            second_attempt = False

        if second_attempt:
            self._backup_session_cookie()
            return True

        return False

    def _backup_session_cookie(self) -> None:
        snapshot = self._browser_service.get_auth_cookie_snapshot()
        session_id = snapshot.get("PHPSESSID", "").strip()
        if session_id:
            self._session.save_session(session_id)
            self._api_service.set_session(session_id)

    def _main_loop(self) -> None:
        while True:
            url = self._scanner_view.scan_qr()

            if not url:
                break

            self._scanner_view.set_scanning_enabled(False)
            self._process_qr(url, allow_auth_retry=True)

    def _process_qr(self, qr_url: str, allow_auth_retry: bool) -> None:
        try:
            browser_result = self._browser_service.resolve_qr_redirect(qr_url)
        except Exception as exc:
            self._error_view.show(f"브라우저 요청 실패: {exc}")
            self._scanner_view.set_scanning_enabled(True)
            return
        self._process_resolved_qr(qr_url, browser_result, allow_auth_retry)

    def _process_resolved_qr(
        self,
        qr_url: str,
        browser_result: BrowserResolveResult,
        allow_auth_retry: bool,
    ) -> None:
        if not browser_result.ok:
            self._handle_browser_failure(browser_result, qr_url, allow_auth_retry)
            return

        parse_result = self._api_service.parse_qr_redirect(
            qr_url,
            browser_result.status_code,
            browser_result.location,
        )

        if not parse_result.success:
            if parse_result.error_code == "AUTH_REQUIRED" and allow_auth_retry:
                self._recover_auth_and_retry(qr_url)
                return

            self._error_view.show(parse_result.error_message)
            self._scanner_view.set_scanning_enabled(True)
            return

        order = self._order_viewmodel.load_order(parse_result.order_number, parse_result.full_url)

        if order:
            self._order_view.show_or_update(order)
            return

        self._error_view.show("주문번호를 찾을 수 없습니다")
        self._scanner_view.set_scanning_enabled(True)

    def _handle_browser_failure(
        self,
        browser_result: BrowserResolveResult,
        qr_url: str,
        allow_auth_retry: bool,
    ) -> None:
        if browser_result.error_code == "AUTH_REQUIRED" and allow_auth_retry:
            self._recover_auth_and_retry(qr_url)
            return

        if browser_result.error_code == "NETWORK_TIMEOUT":
            try:
                retry_result = self._browser_service.resolve_qr_redirect(qr_url)
            except Exception as exc:
                self._error_view.show(f"브라우저 재시도 요청 실패: {exc}")
                self._scanner_view.set_scanning_enabled(True)
                return

            if retry_result.ok:
                self._process_resolved_qr(qr_url, retry_result, allow_auth_retry=False)
                return
            self._error_view.show("네트워크 시간 초과가 발생했습니다. 잠시 후 다시 시도해주세요.")
            self._scanner_view.set_scanning_enabled(True)
            return

        message = browser_result.error_message or "QR 처리 중 알 수 없는 브라우저 오류가 발생했습니다."
        self._error_view.show(message)
        self._scanner_view.set_scanning_enabled(True)

    def _recover_auth_and_retry(self, qr_url: str) -> None:
        self._scanner_view.set_scanning_enabled(True)
        self._error_view.show("세션이 만료되었습니다. 브라우저에서 다시 로그인해주세요.")

        try:
            is_authenticated = self._browser_service.ensure_authenticated(timeout_sec=180)
        except Exception as exc:
            self._error_view.show(f"로그인 확인 중 오류가 발생했습니다: {exc}")
            self._scanner_view.set_scanning_enabled(True)
            return

        if not is_authenticated:
            self._error_view.show("로그인 확인에 실패했습니다. QR 스캔을 다시 시도해주세요.")
            self._scanner_view.set_scanning_enabled(True)
            return

        self._backup_session_cookie()
        self._scanner_view.set_scanning_enabled(False)
        self._process_qr(qr_url, allow_auth_retry=False)


if __name__ == "__main__":
    app = Application()
    app.run()
