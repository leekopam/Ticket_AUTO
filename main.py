"""QR 스캐너 기반 주문 수령 자동화 애플리케이션."""
from __future__ import annotations

import time
from enum import Enum, auto

from services.api_service import ApiService
from services.browser_service import BrowserResolveResult, BrowserService
from services.excel_service import ExcelService
from viewmodels.order_viewmodel import OrderViewModel
from views.order_view import OrderView
from views.scanner_view import ScannerView


class AppState(Enum):
    """애플리케이션 동작 상태."""

    AUTH_WAIT = auto()
    READY = auto()
    PROCESSING = auto()
    RECOVERING = auto()
    ERROR = auto()


class Application:
    """주문 수령 자동화 애플리케이션."""

    def __init__(self):
        self._state = AppState.AUTH_WAIT

        self._excel_service = ExcelService("data.xlsx")
        self._browser_service = BrowserService(require_login_each_run=True)
        self._api_service = ApiService()

        self._order_viewmodel = OrderViewModel(self._excel_service, self._browser_service)

        self._scanner_view = ScannerView()
        self._order_view = OrderView()

        self._last_qr_url = ""
        self._last_qr_timestamp = 0.0
        self._qr_repeat_cooldown_sec = 2.0

        self._browser_service.set_on_receipt_complete(self._on_receipt_complete)

    def _on_receipt_complete(self) -> None:
        print("수령완료 처리 완료 - 스캐너 재활성화")
        if self._scanner_view.is_auth_ready():
            self._enter_ready()
        else:
            self._enter_auth_wait("브라우저에서 로그인이 필요합니다")

    def run(self) -> None:
        try:
            self._browser_service.start()
            self._order_view.start()
            self._scanner_view.start()

            if not self._scanner_view.is_running():
                print("카메라를 열 수 없습니다. 장치를 확인해주세요.")
                return

            self._enter_auth_wait("브라우저에서 로그인이 필요합니다")
            if not self._wait_for_initial_login():
                return

            self._main_loop()
        finally:
            self._order_view.stop()
            self._browser_service.stop()
            self._scanner_view.release()

    def _enter_auth_wait(self, message: str = "브라우저에서 로그인이 필요합니다") -> None:
        self._state = AppState.AUTH_WAIT
        self._scanner_view.set_auth_ready(False)
        self._scanner_view.set_scanning_enabled(True)
        self._scanner_view.set_status_message(message)

    def _enter_ready(self, message: str = "준비됨") -> None:
        self._state = AppState.READY
        self._scanner_view.set_auth_ready(True)
        self._scanner_view.set_scanning_enabled(True)
        self._scanner_view.set_status_message(message)

    def _enter_processing(self, message: str = "처리 중...") -> None:
        self._state = AppState.PROCESSING
        self._scanner_view.set_scanning_enabled(False)
        self._scanner_view.set_status_message(message)

    def _enter_recovering(self, message: str = "로그인이 필요합니다 - 브라우저에서 로그인 완료하세요") -> None:
        self._state = AppState.RECOVERING
        self._scanner_view.set_auth_ready(False)
        self._scanner_view.set_scanning_enabled(True)
        self._scanner_view.set_status_message(message)

    def _enter_error(self, message: str, keep_auth_state: bool = True) -> None:
        self._state = AppState.ERROR
        if not keep_auth_state:
            self._scanner_view.set_auth_ready(False)
        self._scanner_view.set_scanning_enabled(True)
        self._scanner_view.set_status_message(message)

    def _wait_for_initial_login(self) -> bool:
        return self._wait_for_login(timeout_sec=0)

    def _wait_for_login(self, timeout_sec: int) -> bool:
        deadline = None if timeout_sec <= 0 else (time.monotonic() + timeout_sec)

        while self._scanner_view.is_running():
            if deadline is not None and time.monotonic() >= deadline:
                break

            try:
                is_authenticated = self._browser_service.wait_until_authenticated(timeout_sec=1)
            except Exception as exc:
                self._enter_error(f"로그인 확인 실패: {exc}")
                time.sleep(0.2)
                continue

            if is_authenticated:
                self._enter_ready()
                return True

        self._enter_auth_wait("로그인 대기 시간 초과")
        return False

    def _main_loop(self) -> None:
        while self._scanner_view.is_running():
            qr_url = self._scanner_view.get_next_qr(timeout_sec=0.1)
            if not qr_url:
                continue

            if self._is_duplicate_qr(qr_url):
                self._scanner_view.set_status_message("중복 QR 코드 무시됨")
                self._scanner_view.set_scanning_enabled(True)
                continue

            self._enter_processing()
            self._process_qr(qr_url, allow_auth_retry=True)

    def _is_duplicate_qr(self, qr_url: str) -> bool:
        now = time.monotonic()
        is_duplicate = (
            qr_url == self._last_qr_url
            and (now - self._last_qr_timestamp) < self._qr_repeat_cooldown_sec
        )
        self._last_qr_url = qr_url
        self._last_qr_timestamp = now
        return is_duplicate

    def _process_qr(self, qr_url: str, allow_auth_retry: bool) -> None:
        try:
            browser_result = self._browser_service.resolve_qr_redirect(qr_url)
        except Exception as exc:
            self._enter_error(f"브라우저 요청 실패: {exc}")
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
            print(
                "QR_PARSE_FAIL "
                f"code={parse_result.error_code} "
                f"status={browser_result.status_code}"
            )
            if parse_result.error_code == "AUTH_REQUIRED" and allow_auth_retry:
                self._recover_auth_and_retry(qr_url)
                return

            self._enter_error(parse_result.error_message)
            return

        order = self._order_viewmodel.load_order(parse_result.order_number, parse_result.full_url)

        if order:
            self._order_view.show_or_update(order)
            self._order_viewmodel.complete_receipt()
            self._state = AppState.PROCESSING
            self._scanner_view.set_status_message("티켓 창 열림 - 수령 완료 대기 중")
            return

        self._enter_error(f"주문번호를 찾을 수 없습니다: {parse_result.order_number}")

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
                self._enter_error(f"재시도 실패: {exc}")
                return

            if retry_result.ok:
                self._process_resolved_qr(qr_url, retry_result, allow_auth_retry=False)
                return

            self._enter_error("네트워크 시간 초과가 발생했습니다")
            return

        message = browser_result.error_message or "QR 처리 중 브라우저 오류가 발생했습니다"
        self._enter_error(message)

    def _recover_auth_and_retry(self, qr_url: str) -> None:
        print("AUTH_REQUIRED")
        self._enter_recovering("로그인이 필요합니다 - 브라우저에서 로그인 완료하세요")

        if not self._wait_for_login(timeout_sec=180):
            self._scanner_view.set_scanning_enabled(True)
            return

        self._enter_processing("로그인 확인 완료 - 다시 처리 중")
        self._process_qr(qr_url, allow_auth_retry=False)


if __name__ == "__main__":
    app = Application()
    app.run()
