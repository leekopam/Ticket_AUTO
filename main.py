"""QR 스캐너 기반 주문 수령 자동화 애플리케이션."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from enum import Enum, auto
from typing import Callable

from models.receipt_settings_model import ReceiptSettings
from services.api_service import ApiService
from services.browser_service import BrowserResolveResult, BrowserService
from services.excel_service import ExcelService
from services.receipt_print_pipeline import print_order_receipt
from services.receipt_settings_store import ReceiptSettingsStore
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


StatusListener = Callable[[str, str], None]


class Application:
    """주문 수령 자동화 애플리케이션."""

    def __init__(self):
        self._state = AppState.AUTH_WAIT

        self._excel_service = ExcelService("data.xlsx")
        self._browser_service = BrowserService(require_login_each_run=True)
        self._api_service = ApiService()

        self._settings_store = ReceiptSettingsStore(".runtime/receipt_settings.json")
        self._receipt_settings: ReceiptSettings = self._settings_store.load()

        self._order_viewmodel = OrderViewModel(self._excel_service, self._browser_service)

        self._scanner_view = ScannerView()
        self._order_view = OrderView()

        self._last_qr_url = ""
        self._last_qr_timestamp = 0.0
        self._qr_repeat_cooldown_sec = 2.0

        self._status_listener: StatusListener | None = None
        self._stop_requested = False
        self._relogin_requested = False
        self._control_lock = threading.Lock()

    def set_status_listener(self, listener: StatusListener | None) -> None:
        """외부 대시보드에서 상태 이벤트를 구독한다."""
        self._status_listener = listener

    def request_stop(self) -> None:
        """외부에서 런타임 정지를 요청한다."""
        with self._control_lock:
            self._stop_requested = True

        self._emit_status("STOPPING", "런타임 중지 요청됨")
        try:
            self._scanner_view.release()
        except Exception:
            pass
        try:
            self._order_view.stop()
        except Exception:
            pass
        try:
            self._browser_service.stop()
        except Exception:
            pass

    def request_relogin(self) -> None:
        """외부에서 재로그인을 요청한다."""
        with self._control_lock:
            if self._stop_requested:
                return
            self._relogin_requested = True
        self._emit_status("RECOVERING", "재로그인 요청됨")

    def run(self) -> None:
        self._stop_requested = False
        self._relogin_requested = False
        self._emit_status("STARTING", "티켓 확인 런타임 시작 중")

        try:
            self._browser_service.start()
            self._order_view.start()
            self._scanner_view.start()

            if not self._scanner_view.is_running():
                self._enter_error("카메라를 열 수 없습니다. 장치를 확인해주세요.")
                return

            self._enter_auth_wait("브라우저에서 로그인이 필요합니다")
            if not self._wait_for_initial_login():
                return

            self._main_loop()
        finally:
            self._emit_status("STOPPING", "런타임 종료 중")
            self._order_view.stop()
            self._browser_service.stop()
            self._scanner_view.release()
            self._emit_status("STOPPED", "런타임 종료됨")

    def _is_stop_requested(self) -> bool:
        return bool(getattr(self, "_stop_requested", False))

    def _consume_relogin_requested(self) -> bool:
        if not getattr(self, "_relogin_requested", False):
            return False
        self._relogin_requested = False
        return True

    def _emit_status(self, state: str, message: str) -> None:
        listener = getattr(self, "_status_listener", None)
        if listener is None:
            return
        try:
            listener(state, message)
        except Exception:
            pass

    def _enter_auth_wait(self, message: str = "브라우저에서 로그인이 필요합니다") -> None:
        self._state = AppState.AUTH_WAIT
        self._scanner_view.set_auth_ready(False)
        self._scanner_view.set_scanning_enabled(True)
        self._scanner_view.set_status_message(message)
        self._emit_status(self._state.name, message)

    def _enter_ready(self, message: str = "준비됨") -> None:
        self._state = AppState.READY
        self._scanner_view.set_auth_ready(True)
        self._scanner_view.set_scanning_enabled(True)
        self._scanner_view.set_status_message(message)
        self._emit_status(self._state.name, message)

    def _enter_processing(self, message: str = "처리 중...") -> None:
        self._state = AppState.PROCESSING
        self._scanner_view.set_scanning_enabled(False)
        self._scanner_view.set_status_message(message)
        self._emit_status(self._state.name, message)

    def _enter_recovering(self, message: str = "로그인이 필요합니다 - 브라우저에서 로그인 완료하세요") -> None:
        self._state = AppState.RECOVERING
        self._scanner_view.set_auth_ready(False)
        self._scanner_view.set_scanning_enabled(True)
        self._scanner_view.set_status_message(message)
        self._emit_status(self._state.name, message)

    def _enter_error(self, message: str, keep_auth_state: bool = True) -> None:
        self._state = AppState.ERROR
        if not keep_auth_state:
            self._scanner_view.set_auth_ready(False)
        self._scanner_view.set_scanning_enabled(True)
        self._scanner_view.set_status_message(message)
        self._emit_status(self._state.name, message)

    def _wait_for_initial_login(self) -> bool:
        return self._wait_for_login(timeout_sec=0)

    def _wait_for_login(self, timeout_sec: int) -> bool:
        deadline = None if timeout_sec <= 0 else (time.monotonic() + timeout_sec)

        while self._scanner_view.is_running() and not self._is_stop_requested():
            if deadline is not None and time.monotonic() >= deadline:
                break

            try:
                is_authenticated = self._browser_service.wait_until_authenticated(timeout_sec=1)
            except Exception as exc:
                if self._is_stop_requested():
                    return False
                self._enter_error(f"로그인 확인 실패: {exc}")
                time.sleep(0.2)
                continue

            if is_authenticated:
                self._enter_ready()
                return True

        if not self._is_stop_requested():
            self._enter_auth_wait("로그인 대기 시간 초과")
        return False

    def _handle_relogin_request(self) -> None:
        if self._is_stop_requested():
            return

        self._enter_recovering("재로그인 요청됨 - 브라우저에서 로그인하세요")
        try:
            ok = self._browser_service.request_relogin()
        except Exception as exc:
            self._enter_error(f"재로그인 요청 실패: {exc}")
            return

        if not ok:
            self._enter_error("재로그인 요청 실패")
            return

        self._wait_for_login(timeout_sec=180)

    def _main_loop(self) -> None:
        while self._scanner_view.is_running() and not self._is_stop_requested():
            if self._consume_relogin_requested():
                self._handle_relogin_request()
                continue

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
        if self._is_stop_requested():
            return
        try:
            browser_result = self._browser_service.resolve_qr_redirect(qr_url)
        except Exception as exc:
            if not self._is_stop_requested():
                self._enter_error(f"브라우저 요청 실패: {exc}")
            return

        self._process_resolved_qr(qr_url, browser_result, allow_auth_retry)

    def _process_resolved_qr(
        self,
        qr_url: str,
        browser_result: BrowserResolveResult,
        allow_auth_retry: bool,
    ) -> None:
        if self._is_stop_requested():
            return

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

        order = self._order_viewmodel.load_order(
            parse_result.order_number,
            parse_result.full_url,
            open_page=False,
        )

        if not order:
            self._enter_error(f"주문번호를 찾을 수 없습니다: {parse_result.order_number}")
            return

        self._order_view.show_or_update(order)

        if order.is_received:
            self._enter_ready("이미 수령완료된 주문입니다 (주문정보만 표시)")
            return

        if not self._order_viewmodel.open_current_order_page():
            self._enter_error("주문 상세 페이지를 열 수 없습니다.")
            return

        click_result = self._order_viewmodel.complete_receipt()
        if not click_result.success:
            self._enter_error(
                f"{click_result.error_message or '수령 완료 처리 실패'} "
                f"(code={click_result.error_code or 'UNKNOWN'})"
            )
            return

        previous_received = order.received_at
        received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self._order_viewmodel.mark_current_order_received(received_at):
            self._enter_error("수령확인 저장 실패: 엑셀 파일 권한/잠금을 확인해주세요.")
            return

        try:
            # 설정 화면에서 변경된 최신 여백/템플릿 반영
            try:
                self._receipt_settings = self._settings_store.load()
            except Exception:
                pass
            print_order_receipt(order, self._receipt_settings)
        except Exception as exc:
            rollback_ok = self._order_viewmodel.rollback_current_order_received(previous_received)
            if rollback_ok:
                self._enter_error(f"영수증 출력 실패로 수령확인을 원복했습니다: {exc}")
            else:
                self._enter_error(
                    "영수증 출력 실패 및 수령확인 원복 실패 "
                    f"(수동 조치 필요, 주문번호={order.order_number}): {exc}"
                )
            return

        self._enter_ready("수령 완료 및 영수증 출력 완료")

    def _handle_browser_failure(
        self,
        browser_result: BrowserResolveResult,
        qr_url: str,
        allow_auth_retry: bool,
    ) -> None:
        if self._is_stop_requested():
            return

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
        if self._is_stop_requested():
            return

        print("AUTH_REQUIRED")
        self._enter_recovering("로그인이 필요합니다 - 브라우저에서 로그인 완료하세요")

        if not self._wait_for_login(timeout_sec=180):
            if not self._is_stop_requested():
                self._scanner_view.set_scanning_enabled(True)
            return

        self._enter_processing("로그인 확인 완료 - 다시 처리 중")
        self._process_qr(qr_url, allow_auth_retry=False)


if __name__ == "__main__":
    from views.dashboard_flet_view import run_dashboard_app

    run_dashboard_app()
