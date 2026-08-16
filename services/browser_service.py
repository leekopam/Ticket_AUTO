"""
Playwright 브라우저 서비스.

이 서비스는 브라우저 세션을 소유하며
인증 상태에 대한 단일 진실 공급원(Single Source of Truth) 역할을 합니다.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import unquote

logger = logging.getLogger(__name__)

from playwright.sync_api import (
    BrowserContext,
    Error,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
from project_paths import resolve_project_path


REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
MODERN_ORDER_NUMBER_PATTERN = r"[A-Z0-9]{10}_[A-Z0-9]{12}"
LEGACY_ORDER_NUMBER_PATTERN = r"\d{4,}_\d{4,}"
ORDER_NUMBER_CANDIDATE_PATTERN = rf"(?:{MODERN_ORDER_NUMBER_PATTERN}|{LEGACY_ORDER_NUMBER_PATTERN})"
LABELLED_ORDER_NUMBER_PATTERN = re.compile(
    rf"(?:주문번호|order\s*(?:number|no))\s*[:#]?\s*({ORDER_NUMBER_CANDIDATE_PATTERN})",
    re.IGNORECASE,
)
GENERIC_ORDER_NUMBER_PATTERN = re.compile(rf"\b{ORDER_NUMBER_CANDIDATE_PATTERN}\b")
LABELLED_NAME_PATTERN = re.compile(
    r"(?:주문자명|구매자명|수령자명)\s*[:：]?\s*(?:\n\s*)?([가-힣A-Za-z][^\n\r]{0,30})"
)
LABELLED_PHONE_PATTERN = re.compile(
    r"(?:주문자\s*(?:연락처)?|구매자\s*(?:연락처)?|수령자\s*(?:연락처)?|연락처|휴대폰)\s*[:：]?\s*(?:\n\s*)?(01[016789][-\s]?\d{3,4}[-\s]?\d{4})"
)
GENERIC_PHONE_PATTERN = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")


@dataclass
class BrowserResolveResult:
    ok: bool
    status_code: int = 0
    location: str = ""
    error_code: str = ""
    error_message: str = ""


@dataclass
class ReceiptClickResult:
    success: bool
    error_code: str = ""
    error_message: str = ""


@dataclass
class PageOrderDiscoveryResult:
    order_number: str = ""
    buyer_name: str = ""
    buyer_phone: str = ""
    url: str = ""


class BrowserService:
    _RECEIPT_PRE_CONFIRM_SETTLE_MS = 120
    _RECEIPT_POST_CONFIRM_SETTLE_MS = 250
    _LOGIN_PAGE_WAIT_UNTIL = "domcontentloaded"
    _AUTH_STORAGE_CLEAR_SCRIPT = "() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"

    def __init__(
        self,
        login_url: str = "https://witchform.com/w/login",
        user_data_dir: str = ".runtime/pw_profile",
        require_login_each_run: bool = True,
        headless: bool = False,
    ):
        self._login_url = login_url
        self._user_data_dir = str(resolve_project_path(user_data_dir))
        self._require_login_each_run = require_login_each_run
        self._headless = headless
        self._task_queue: queue.Queue = queue.Queue()
        self._current_page: Page | None = None
        self._auth_page: Page | None = None
        self._context: BrowserContext | None = None
        self._browser_type: Any | None = None
        self._context_recovery_failed = False
        self._worker_thread: threading.Thread | None = None
        self._is_running = False
        self._accepting_requests = False
        self._startup_error: Exception | None = None
        self._ready_event = threading.Event()
        self._lifecycle_lock = threading.Lock()

        # 영수증 클릭 흐름이 완료되었을 때 호출되는 콜백
        self._on_receipt_complete: Callable[[], None] | None = None

    def set_on_receipt_complete(self, callback: Callable[[], None]) -> None:
        self._on_receipt_complete = callback

    def start(self) -> None:
        self._ensure_lifecycle_state()
        with self._lifecycle_lock:
            if self._is_running:
                return
            if self._worker_thread and self._worker_thread.is_alive():
                raise RuntimeError("브라우저 워커가 아직 정리 중입니다.")

            self._task_queue = queue.Queue()
            self._is_running = True
            self._accepting_requests = True
            self._context_recovery_failed = False
            self._startup_error = None
            self._ready_event.clear()
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

        if not self._ready_event.wait(timeout=20):
            self._abort_start_attempt()
            raise RuntimeError("브라우저 워커 시작 시간 초과.")

        if self._startup_error is not None:
            self._abort_start_attempt()
            raise RuntimeError(f"브라우저 워커 실패: {self._startup_error}")

    def stop(self) -> None:
        self._ensure_lifecycle_state()
        with self._lifecycle_lock:
            worker = self._worker_thread
            if not self._is_running:
                if worker is not None:
                    worker.join(timeout=15)
                return
            self._is_running = False
            if self._accepting_requests:
                self._accepting_requests = False
                self._task_queue.put({"action": "quit"})
        if worker is not None:
            worker.join(timeout=15)

    @property
    def login_url(self) -> str:
        return self._login_url

    def open_page(
        self,
        url: str,
        timeout_sec: int = 25,
        *,
        preserve_current_page: bool = False,
    ) -> bool:
        task: dict[str, Any] = {"action": "open_page", "url": url}
        if preserve_current_page:
            task["preserve_current_page"] = True
        return bool(
            self._invoke_rpc(
                task,
                timeout_sec=timeout_sec,
            )
        )

    def click_receipt_button(self) -> ReceiptClickResult:
        return self._invoke_rpc({"action": "click_receipt"}, timeout_sec=30)

    def ensure_authenticated(self, timeout_sec: int = 180) -> bool:
        timeout_sec = max(1, timeout_sec)
        return bool(
            self._invoke_rpc(
                {"action": "ensure_authenticated", "timeout_sec": timeout_sec},
                timeout_sec=timeout_sec + 15,
            )
        )

    def wait_until_authenticated(self, timeout_sec: int = 600) -> bool:
        return self.ensure_authenticated(timeout_sec=timeout_sec)

    def clear_auth_state_for_domain(self) -> bool:
        return bool(self._invoke_rpc({"action": "clear_auth_state_for_domain"}, timeout_sec=20))

    def request_relogin(self) -> bool:
        """인증 상태를 강제로 초기화하고 사용자 조작을 위해 로그인 페이지를 열어둡니다."""
        return bool(self._invoke_rpc({"action": "request_relogin"}, timeout_sec=20))

    def resolve_qr_redirect(
        self,
        qr_url: str,
        timeout_ms: int = 8000,
    ) -> BrowserResolveResult:
        """Playwright 컨텍스트 세션 쿠키로만 QR 리다이렉트를 해석한다.

        외부 쿠키/헤더 주입을 허용하지 않는다.
        """
        timeout_ms = max(1000, timeout_ms)
        return self._invoke_rpc(
            {
                "action": "resolve_qr_redirect",
                "qr_url": qr_url,
                "timeout_ms": timeout_ms,
            },
            timeout_sec=max(10, int(timeout_ms / 1000) + 10),
        )

    def get_auth_cookie_snapshot(self) -> dict[str, str]:
        return self._invoke_rpc({"action": "get_auth_cookie_snapshot"}, timeout_sec=10)

    def replace_auth_cookie_snapshot(self, snapshot: dict[str, str]) -> bool:
        """현재 런타임 컨텍스트에 auth 관련 쿠키만 안전하게 다시 심는다."""
        return bool(
            self._invoke_rpc(
                {
                    "action": "replace_auth_cookie_snapshot",
                    "snapshot": dict(snapshot or {}),
                },
                timeout_sec=20,
            )
        )

    def discover_order_number_from_page(
        self,
        url: str,
        timeout_ms: int = 15000,
    ) -> str:
        return self.discover_order_context_from_page(
            url,
            timeout_ms=timeout_ms,
        ).order_number

    def discover_order_context_from_page(
        self,
        url: str,
        timeout_ms: int = 15000,
    ) -> PageOrderDiscoveryResult:
        timeout_ms = max(2000, timeout_ms)
        return self._invoke_rpc(
            {
                "action": "discover_order_context_from_page",
                "url": url,
                "timeout_ms": timeout_ms,
            },
            timeout_sec=max(15, int(timeout_ms / 1000) + 10),
        )

    def _invoke_rpc(self, task: dict[str, Any], timeout_sec: int) -> Any:
        response_queue: queue.Queue = queue.Queue(maxsize=1)
        task["response_queue"] = response_queue
        self._enqueue_task(task)

        try:
            response = response_queue.get(timeout=timeout_sec)
        except queue.Empty as exc:
            raise RuntimeError(f"작업 시간 초과: {task.get('action')}") from exc

        error = response.get("error")
        if error is not None:
            raise RuntimeError(error)

        return response.get("result")

    def _worker_loop(self) -> None:
        context: BrowserContext | None = None
        startup_started_at = time.perf_counter()
        sync_playwright_ms = 0.0
        launch_ms = 0.0
        initial_login_ms = 0.0
        clear_auth_ms = 0.0
        try:
            os.makedirs(self._user_data_dir, exist_ok=True)

            sync_started_at = time.perf_counter()
            with sync_playwright() as p:
                sync_playwright_ms = self._elapsed_ms(sync_started_at)
                self._browser_type = p.chromium
                launch_started_at = time.perf_counter()
                context = self._launch_persistent_context()
                launch_ms = self._elapsed_ms(launch_started_at)
                self._context = context

                if self._require_login_each_run:
                    clear_started_at = time.perf_counter()
                    self._clear_witchform_cookies()
                    clear_auth_ms += self._elapsed_ms(clear_started_at)

                initial_login_started_at = time.perf_counter()
                self._open_initial_witchform_page()
                initial_login_ms = self._elapsed_ms(initial_login_started_at)

                if self._require_login_each_run:
                    storage_clear_started_at = time.perf_counter()
                    self._clear_witchform_storage_on_auth_page()
                    clear_auth_ms += self._elapsed_ms(storage_clear_started_at)

                self._print_startup_timing(
                    total_ms=self._elapsed_ms(startup_started_at),
                    sync_playwright_ms=sync_playwright_ms,
                    launch_ms=launch_ms,
                    initial_login_ms=initial_login_ms,
                    clear_auth_ms=clear_auth_ms,
                )
                self._ready_event.set()

                while True:
                    try:
                        task = self._task_queue.get(timeout=0.5)
                        should_quit = task.get("action") == "quit"
                        self._dispatch_task(task)
                        if should_quit:
                            break
                    except queue.Empty:
                        continue
        except Exception as exc:
            self._startup_error = exc
            if not self._ready_event.is_set():
                self._ready_event.set()
            print(f"Playwright 시작 오류: {exc}")
        finally:
            if self._context is not None:
                try:
                    self._context.close()
                except Exception as exc:
                    self._warn(f"CONTEXT_CLOSE_FAILED: {exc}")
            self._finalize_worker_shutdown()

    def _dispatch_task(self, task: dict[str, Any]) -> None:
        action = task.get("action")
        response_queue: queue.Queue | None = task.get("response_queue")

        def finish(result: Any = None, error: str | None = None) -> None:
            if response_queue is not None:
                response_queue.put({"result": result, "error": error})

        try:
            if action == "quit":
                finish(True, None)
                return
            if action == "open_page":
                result = self._handle_open_page(
                    task["url"],
                    preserve_current_page=bool(task.get("preserve_current_page")),
                )
                finish(result, None)
                return
            if action == "click_receipt":
                result = self._handle_click_receipt()
                finish(result, None)
                return
            if action == "ensure_authenticated":
                result = self._handle_ensure_authenticated(task.get("timeout_sec", 180))
                finish(result, None)
                return
            if action == "clear_auth_state_for_domain":
                result = self._handle_clear_auth_state_for_domain()
                finish(result, None)
                return
            if action == "request_relogin":
                result = self._handle_request_relogin()
                finish(result, None)
                return
            if action == "resolve_qr_redirect":
                result = self._handle_resolve_qr_redirect(
                    task.get("qr_url", ""),
                    task.get("timeout_ms", 8000),
                )
                finish(result, None)
                return
            if action == "get_auth_cookie_snapshot":
                result = self._handle_get_auth_cookie_snapshot()
                finish(result, None)
                return
            if action == "replace_auth_cookie_snapshot":
                result = self._handle_replace_auth_cookie_snapshot(task.get("snapshot", {}))
                finish(result, None)
                return
            if action == "discover_order_context_from_page":
                result = self._handle_discover_order_context_from_page(
                    task.get("url", ""),
                    task.get("timeout_ms", 15000),
                )
                finish(result, None)
                return
            if action == "discover_order_number_from_page":
                result = self._handle_discover_order_number_from_page(
                    task.get("url", ""),
                    task.get("timeout_ms", 15000),
                )
                finish(result, None)
                return

            finish(None, f"Unknown task action: {action}")
        except Exception as exc:
            if response_queue is None:
                self._warn(f"TASK_FAILED action={action}: {exc}")
                return
            finish(None, str(exc))

    def _handle_open_page(
        self,
        url: str,
        *,
        preserve_current_page: bool = False,
        retry_closed_context: bool = True,
    ) -> bool:
        if getattr(self, "_context_recovery_failed", False):
            return False
        if not self._context:
            raise RuntimeError("브라우저 컨텍스트가 준비되지 않았습니다.")

        try:
            return self._open_page_with_current_context(url, preserve_current_page=preserve_current_page)
        except Error as exc:
            if not retry_closed_context or not self._is_closed_context_error(exc):
                raise
            try:
                self._replace_closed_context()
                return self._handle_open_page(
                    url,
                    preserve_current_page=preserve_current_page,
                    retry_closed_context=False,
                )
            except Exception as recovery_exc:
                self._context_recovery_failed = True
                self._warn(f"CLOSED_CONTEXT_RECOVERY_FAILED: {recovery_exc}")
                return False

    def _open_page_with_current_context(self, url: str, *, preserve_current_page: bool) -> bool:
        if not self._context:
            raise RuntimeError("브라우저 컨텍스트가 준비되지 않았습니다.")

        if not preserve_current_page:
            self._close_current_page()
        opened_page = self._context.new_page()
        try:
            opened_page.goto(url, timeout=15000)
        except Exception:
            try:
                opened_page.close()
            except Exception:
                pass
            raise
        if not preserve_current_page:
            self._current_page = opened_page
        print(f"PAGE_OPEN {url}")
        return True

    def _replace_closed_context(self) -> None:
        stale_context = self._context
        self._context = None
        self._current_page = None
        self._auth_page = None
        if stale_context is not None:
            try:
                stale_context.close()
            except Exception:
                pass
        self._context = self._launch_persistent_context()

    def _launch_persistent_context(self) -> BrowserContext:
        if self._browser_type is None:
            raise RuntimeError("브라우저 실행기가 준비되지 않았습니다.")
        return self._browser_type.launch_persistent_context(
            user_data_dir=self._user_data_dir,
            headless=self._headless,
        )

    @staticmethod
    def _is_closed_context_error(exc: Error) -> bool:
        return "context or browser has been closed" in str(exc).lower()

    def _handle_click_receipt(self) -> ReceiptClickResult:
        if not self._current_page or self._current_page.is_closed():
            message = "수령 완료 버튼을 누를 활성 주문 페이지가 없습니다."
            print(message)
            self._invoke_receipt_complete_callback()
            return ReceiptClickResult(
                success=False,
                error_code="NO_ACTIVE_PAGE",
                error_message=message,
            )

        try:
            self._current_page.click("text=수령 완료 처리", timeout=5000)
        except Exception as exc:
            # 버튼이 없으면 이미 수령완료 상태인지 확인
            if self._page_shows_already_received():
                print("RECEIPT_ALREADY_RECEIVED_ON_PAGE")
                self._close_current_page()
                self._invoke_receipt_complete_callback()
                return ReceiptClickResult(
                    success=False,
                    error_code="ALREADY_RECEIVED",
                    error_message="이미 수령완료 처리된 주문입니다.",
                )
            message = f"수령 완료 1차 버튼 클릭 실패: {exc}"
            print(message)
            try:
                body_text = (self._current_page.inner_text("body") or "")[:500]
                logger.warning("PRIMARY_CLICK_FAIL 페이지 본문(500자): %s", body_text)
            except Exception:
                pass
            self._invoke_receipt_complete_callback()
            return ReceiptClickResult(
                success=False,
                error_code="PRIMARY_CLICK_FAIL",
                error_message=message,
            )

        try:
            self._current_page.wait_for_timeout(self._RECEIPT_PRE_CONFIRM_SETTLE_MS)
        except Exception as exc:
            if self._page_shows_already_received():
                print("RECEIPT_ALREADY_RECEIVED_ON_PRE_CONFIRM_WAIT")
                self._close_current_page()
                self._invoke_receipt_complete_callback()
                return ReceiptClickResult(
                    success=False,
                    error_code="ALREADY_RECEIVED",
                    error_message="이미 수령완료 처리된 주문입니다.",
                )
            message = f"수령 완료 처리 확인 대기 실패: {exc}"
            print(message)
            self._invoke_receipt_complete_callback()
            return ReceiptClickResult(
                success=False,
                error_code="CONFIRM_CLICK_FAIL",
                error_message=message,
            )

        confirm_clicked = False
        for selector in (
            "button.modal-alert_statusUpdateBtn__RABK9",
            "text=확인",
            "text=수령 완료 처리",
        ):
            try:
                self._current_page.click(selector, timeout=3000)
                confirm_clicked = True
                break
            except Exception:
                continue

        if not confirm_clicked:
            if self._page_shows_already_received():
                print("RECEIPT_ALREADY_RECEIVED_ON_CONFIRM_CLICK")
                self._close_current_page()
                self._invoke_receipt_complete_callback()
                return ReceiptClickResult(
                    success=False,
                    error_code="ALREADY_RECEIVED",
                    error_message="이미 수령완료 처리된 주문입니다.",
                )
            message = "수령 완료 확인 팝업 버튼 클릭 실패"
            print(message)
            self._invoke_receipt_complete_callback()
            return ReceiptClickResult(
                success=False,
                error_code="CONFIRM_CLICK_FAIL",
                error_message=message,
            )

        try:
            self._current_page.wait_for_timeout(self._RECEIPT_POST_CONFIRM_SETTLE_MS)
        except Exception as exc:
            if self._page_shows_already_received():
                print("RECEIPT_ALREADY_RECEIVED_ON_POST_CONFIRM_WAIT")
                self._close_current_page()
                self._invoke_receipt_complete_callback()
                return ReceiptClickResult(
                    success=False,
                    error_code="ALREADY_RECEIVED",
                    error_message="이미 수령완료 처리된 주문입니다.",
                )
            print(f"Playwright 클릭 오류: {exc}")
            self._invoke_receipt_complete_callback()
            return ReceiptClickResult(
                success=False,
                error_code="CONFIRM_CLICK_FAIL",
                error_message=f"수령 완료 처리 후 페이지 상태 확인 실패: {exc}",
            )

        # 수령완료 처리 성공 후 거래종료 버튼이 있으면 클릭 시도 (없으면 조용히 스킵)
        try:
            self._current_page.click("text=거래종료", timeout=2000)
            print("CLOSE_DEAL_CLICKED")
            try:
                self._current_page.click("text=확인", timeout=2000)
            except Exception:
                pass
        except Exception:
            pass

        self._close_current_page()
        self._invoke_receipt_complete_callback()
        return ReceiptClickResult(success=True)

    def _handle_ensure_authenticated(self, timeout_sec: int) -> bool:
        timeout_sec = max(1, timeout_sec)
        print("AUTH_CHECK_START")

        if self._is_authenticated():
            print("AUTH_CHECK_OK")
            return True

        self._ensure_login_page_open()
        deadline = time.monotonic() + timeout_sec

        while time.monotonic() < deadline and self._is_running:
            if self._is_authenticated():
                print("AUTH_CHECK_OK")
                self._close_auth_page_if_possible()
                return True
            time.sleep(1.0)

        print("AUTH_REQUIRED")
        return False

    def _handle_resolve_qr_redirect(self, qr_url: str, timeout_ms: int) -> BrowserResolveResult:
        if not self._context:
            return BrowserResolveResult(
                ok=False,
                error_code="BROWSER_NOT_READY",
                error_message="브라우저 컨텍스트가 준비되지 않았습니다.",
            )

        request_options = self._build_qr_resolve_request_options(timeout_ms)
        forbidden_keys = {"cookies", "headers"} & set(request_options.keys())
        if forbidden_keys:
            return BrowserResolveResult(
                ok=False,
                error_code="REQUEST_OPTION_INVALID",
                error_message="QR 요청 옵션에 허용되지 않은 키가 포함되었습니다.",
            )

        try:
            response = self._context.request.get(
                qr_url,
                **request_options,
            )
        except PlaywrightTimeoutError:
            return BrowserResolveResult(
                ok=False,
                error_code="NETWORK_TIMEOUT",
                error_message="QR 리다이렉트 확인 요청 시간이 초과되었습니다.",
            )
        except Error as exc:
            return BrowserResolveResult(
                ok=False,
                error_code="NETWORK_ERROR",
                error_message=f"브라우저 요청 실패: {exc}",
            )
        except Exception as exc:
            return BrowserResolveResult(
                ok=False,
                error_code="NETWORK_ERROR",
                error_message=f"브라우저 요청 실패: {exc}",
            )

        status_code = response.status
        location = response.headers.get("location", "")
        final_url = (response.url or "").lower()

        if status_code in REDIRECT_STATUS_CODES and "/w/login" in (location or "").lower():
            return BrowserResolveResult(
                ok=False,
                status_code=status_code,
                location=location,
                error_code="AUTH_REQUIRED",
                error_message="로그인이 필요합니다.",
            )

        if status_code == 200:
            body = ""
            try:
                body = response.text()
            except Exception:
                body = ""

            if self._looks_like_login_page(final_url, body):
                return BrowserResolveResult(
                    ok=False,
                    status_code=status_code,
                    error_code="AUTH_REQUIRED",
                    error_message="로그인이 필요합니다.",
                )

        print(f"QR_RESOLVE_OK status={status_code} location={location}")
        return BrowserResolveResult(ok=True, status_code=status_code, location=location)

    @staticmethod
    def _build_qr_resolve_request_options(timeout_ms: int) -> dict[str, Any]:
        """QR 요청 고정 옵션을 생성한다. 외부 쿠키/헤더 주입은 금지한다."""
        return {
            "max_redirects": 0,
            "fail_on_status_code": False,
            "timeout": timeout_ms,
        }

    def _handle_get_auth_cookie_snapshot(self) -> dict[str, str]:
        if not self._context:
            return {}

        snapshot: dict[str, str] = {}
        for cookie in self._context.cookies(["https://witchform.com"]):
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            domain = cookie.get("domain", "")

            if not name or not value:
                continue
            if "witchform.com" not in domain:
                continue
            if name == "PHPSESSID" or self._is_auth_like_cookie_name(name):
                snapshot[name] = value

        if "PHPSESSID" in snapshot:
            print(f"COOKIE_SNAPSHOT PHPSESSID={self._mask_secret(snapshot['PHPSESSID'])}")
        else:
            print("COOKIE_SNAPSHOT PHPSESSID=<missing>")

        return snapshot

    def _handle_replace_auth_cookie_snapshot(self, snapshot: dict[str, str]) -> bool:
        if not self._context:
            return False

        for domain in (".witchform.com", "witchform.com"):
            try:
                self._context.clear_cookies(domain=domain)
            except Exception as exc:
                self._warn(f"REPLACE_AUTH_COOKIES_CLEAR_FAILED domain={domain}: {exc}")

        cookies_to_add: list[dict[str, Any]] = []
        for name, value in dict(snapshot or {}).items():
            cookie_name = str(name or "").strip()
            cookie_value = str(value or "").strip()
            if not cookie_name or not cookie_value:
                continue
            if cookie_name != "PHPSESSID" and not self._is_auth_like_cookie_name(cookie_name):
                continue
            cookies_to_add.append(
                {
                    "name": cookie_name,
                    "value": cookie_value,
                    "url": "https://witchform.com/",
                }
            )

        if not cookies_to_add:
            return True

        self._context.add_cookies(cookies_to_add)
        return True

    def _handle_discover_order_context_from_page(
        self,
        url: str,
        timeout_ms: int,
    ) -> PageOrderDiscoveryResult:
        if not self._context or not url:
            return PageOrderDiscoveryResult(url=url)

        request_result = self._discover_order_context_via_request(url, timeout_ms)
        if request_result.order_number:
            self._print_order_discovery_result(request_result)
            return request_result

        page = self._context.new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5000))
            except Exception:
                pass
            page.wait_for_timeout(800)

            current_url = page.url or url
            try:
                body_text = page.locator("body").inner_text(timeout=3000) or ""
            except Exception:
                body_text = ""
            try:
                html = page.content() or ""
            except Exception:
                html = ""

            order_number = self._extract_order_number_candidate(current_url, body_text, html)
            buyer_name, buyer_phone = self._extract_buyer_contact_candidate(body_text)
            result = PageOrderDiscoveryResult(
                order_number=order_number,
                buyer_name=buyer_name,
                buyer_phone=buyer_phone,
                url=current_url,
            )
            self._print_order_discovery_result(result)
            return result
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _handle_discover_order_number_from_page(self, url: str, timeout_ms: int) -> str:
        return self._handle_discover_order_context_from_page(url, timeout_ms).order_number

    @staticmethod
    def _extract_order_number_candidate(*texts: str) -> str:
        for text in texts:
            decoded = unquote(text or "")
            labelled = LABELLED_ORDER_NUMBER_PATTERN.search(decoded)
            if labelled:
                return labelled.group(1).upper()

        for text in texts:
            decoded = unquote(text or "")
            generic = GENERIC_ORDER_NUMBER_PATTERN.search(decoded)
            if generic:
                return generic.group(0).upper()

        return ""

    def _discover_order_context_via_request(
        self,
        url: str,
        timeout_ms: int,
    ) -> PageOrderDiscoveryResult:
        if not self._context:
            return PageOrderDiscoveryResult(url=url)

        try:
            response = self._context.request.get(
                url,
                max_redirects=5,
                fail_on_status_code=False,
                timeout=timeout_ms,
            )
        except Exception:
            return PageOrderDiscoveryResult(url=url)

        current_url = response.url or url
        try:
            html = response.text() or ""
        except Exception:
            html = ""

        return PageOrderDiscoveryResult(
            order_number=self._extract_order_number_candidate(current_url, html),
            url=current_url,
        )

    def _print_order_discovery_result(self, result: PageOrderDiscoveryResult) -> None:
        masked_name = self._mask_name(result.buyer_name)
        masked_phone = self._mask_phone(result.buyer_phone)
        print(
            "ORDER_DISCOVERY "
            f"order_number={result.order_number} "
            f"buyer_name={masked_name} "
            f"buyer_phone={masked_phone} "
            f"url={result.url}"
        )

    @staticmethod
    def _extract_buyer_contact_candidate(text: str) -> tuple[str, str]:
        decoded = unquote(text or "")

        name_match = LABELLED_NAME_PATTERN.search(decoded)
        buyer_name = name_match.group(1).strip() if name_match else ""

        phone_match = LABELLED_PHONE_PATTERN.search(decoded)
        if not phone_match:
            phone_match = GENERIC_PHONE_PATTERN.search(decoded)
        buyer_phone = phone_match.group(1).strip() if phone_match and phone_match.lastindex else (
            phone_match.group(0).strip() if phone_match else ""
        )

        return buyer_name, buyer_phone

    def _is_authenticated(self) -> bool:
        """PHPSESSID 쿠키 존재 여부로 인증 상태 판단.

        시작 시 쿠키를 전부 삭제하므로 PHPSESSID가 존재하면 이번 세션에서 로그인한 것.
        세션 만료 시 QR 처리 흐름에서 AUTH_REQUIRED로 감지되어 재로그인 유도.
        """
        if not self._context:
            return False

        for cookie in self._context.cookies(["https://witchform.com"]):
            if (
                cookie.get("name") == "PHPSESSID"
                and cookie.get("value")
                and "witchform.com" in cookie.get("domain", "")
            ):
                return True

        return False

    def _ensure_login_page_open(self) -> None:
        if not self._context:
            return
        if self._auth_page and not self._auth_page.is_closed():
            return

        reusable_page = None
        for page in self._context.pages:
            if page.is_closed():
                continue
            if self._current_page and page == self._current_page:
                continue
            reusable_page = page
            break

        self._auth_page = reusable_page or self._context.new_page()
        self._navigate_login_page(self._auth_page)

    def _open_initial_witchform_page(self) -> None:
        """윗치폼 로그인 페이지만 열고 시작 시 빈 탭을 제거합니다."""
        if not self._context:
            return

        pages = [page for page in self._context.pages if not page.is_closed()]
        if pages:
            target_page = pages[0]
        else:
            target_page = self._context.new_page()

        try:
            self._navigate_login_page(target_page)
        except Exception as exc:
            self._warn(f"INITIAL_LOGIN_GOTO_FAILED: {exc}")
        self._auth_page = target_page

        for page in list(self._context.pages):
            if page.is_closed() or page == target_page:
                continue
            try:
                page.close()
            except Exception as exc:
                self._warn(f"INITIAL_EXTRA_PAGE_CLOSE_FAILED: {exc}")

    def _handle_clear_auth_state_for_domain(self) -> bool:
        """현재 실행에 대한 윗치폼 인증 상태를 삭제(Clear)합니다."""
        if not self._context:
            return False

        self._clear_witchform_cookies()

        if self._auth_page and not self._auth_page.is_closed():
            target_page = self._auth_page
        elif self._context.pages:
            target_page = next((p for p in self._context.pages if not p.is_closed()), None)
        else:
            target_page = None

        if target_page is None:
            target_page = self._context.new_page()

        try:
            self._navigate_login_page(target_page)
        except Exception as exc:
            self._warn(f"AUTH_STATE_LOGIN_PREP_FAILED: {exc}")
        self._auth_page = target_page
        self._clear_witchform_storage_on_auth_page()

        for page in list(self._context.pages):
            if page.is_closed() or page == target_page:
                continue
            try:
                page.close()
            except Exception as exc:
                self._warn(f"AUTH_STATE_EXTRA_PAGE_CLOSE_FAILED: {exc}")

        return True

    def _navigate_login_page(self, page: Page) -> None:
        page.goto(
            self._login_url,
            timeout=15000,
            wait_until=self._LOGIN_PAGE_WAIT_UNTIL,
        )

    def _clear_witchform_cookies(self) -> None:
        if not self._context:
            return
        for domain in (".witchform.com", "witchform.com"):
            try:
                self._context.clear_cookies(domain=domain)
            except Exception as exc:
                self._warn(f"CLEAR_COOKIES_FAILED domain={domain}: {exc}")

    def _clear_witchform_storage_on_auth_page(self) -> None:
        if not self._auth_page or self._auth_page.is_closed():
            return
        try:
            self._auth_page.evaluate(self._AUTH_STORAGE_CLEAR_SCRIPT)
        except Exception as exc:
            self._warn(f"AUTH_STATE_STORAGE_CLEAR_FAILED: {exc}")

    @staticmethod
    def _elapsed_ms(started_at: float) -> float:
        return (time.perf_counter() - started_at) * 1000

    @staticmethod
    def _print_startup_timing(
        *,
        total_ms: float,
        sync_playwright_ms: float,
        launch_ms: float,
        initial_login_ms: float,
        clear_auth_ms: float,
    ) -> None:
        print(
            "BROWSER_STARTUP_TIMING "
            f"total_ms={total_ms:.1f} "
            f"sync_playwright_ms={sync_playwright_ms:.1f} "
            f"launch_ms={launch_ms:.1f} "
            f"initial_login_ms={initial_login_ms:.1f} "
            f"clear_auth_ms={clear_auth_ms:.1f}"
        )

    def _handle_request_relogin(self) -> bool:
        """런타임 중에 안전하게 호출할 수 있는 재로그인 트리거(Public runtime-safe relogin trigger)입니다."""
        ok = self._handle_clear_auth_state_for_domain()
        if not ok:
            return False
        self._ensure_login_page_open()
        return True

    def _close_auth_page_if_possible(self) -> None:
        if not self._auth_page or self._auth_page.is_closed():
            self._auth_page = None
            return
        try:
            self._auth_page.close()
        except Exception as exc:
            self._warn(f"AUTH_PAGE_CLOSE_FAILED: {exc}")
        self._auth_page = None

    @staticmethod
    def _looks_like_login_page(url: str, body: str) -> bool:
        lowered_url = (url or "").lower()
        if "/w/login" in lowered_url:
            return True

        lowered_body = (body or "").lower()
        markers = (
            'name="userid"',
            'name="password"',
            "type=\"password\"",
            "login",
        )
        matched = sum(marker in lowered_body for marker in markers)
        return matched >= 2

    @staticmethod
    def _is_auth_like_cookie_name(name: str) -> bool:
        lowered = name.lower()
        return "auth" in lowered or "token" in lowered or "sess" in lowered

    @staticmethod
    def _mask_secret(value: str) -> str:
        if len(value) <= 8:
            return "****"
        return f"{value[:4]}...{value[-4:]}"

    @staticmethod
    def _mask_name(value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        if len(text) == 1:
            return "*"
        if len(text) == 2:
            return f"{text[0]}*"
        return f"{text[0]}*{text[-1]}"

    @staticmethod
    def _mask_phone(value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        digits = "".join(ch for ch in text if ch.isdigit())
        if len(digits) == 11:
            return f"{digits[:3]}-****-{digits[-4:]}"
        if len(digits) == 10:
            return f"{digits[:3]}-***-{digits[-4:]}"
        if len(text) <= 4:
            return "****"
        return f"{text[:2]}***{text[-2:]}"

    def _page_shows_already_received(self) -> bool:
        """현재 페이지에 이미 수령완료 표시가 있는지 확인한다."""
        if not self._current_page or self._current_page.is_closed():
            return False
        try:
            body_text = self._current_page.locator("body").inner_text(timeout=2000) or ""
        except Exception:
            return False
        normalized = re.sub(r"\s+", "", body_text or "")
        positive_markers = (
            "이미수령완료",
            "이미처리",
            "중복스캔",
            "중복으로스캔",
            "수령완료되었습니다",
            "처리완료된주문",
            "이미처리된주문",
        )
        if any(marker in normalized for marker in positive_markers):
            return True
        return "수령완료" in normalized and "수령완료처리" not in normalized

    def _close_current_page(self) -> None:
        """수령완료 처리 후 구매자 정보 페이지를 닫는다."""
        if not self._current_page or self._current_page.is_closed():
            self._current_page = None
            return
        try:
            self._current_page.close()
        except Exception as exc:
            self._warn(f"ORDER_PAGE_CLOSE_FAILED: {exc}")
        self._current_page = None

    def _invoke_receipt_complete_callback(self) -> None:
        if self._on_receipt_complete:
            try:
                self._on_receipt_complete()
            except Exception as exc:
                self._warn(f"RECEIPT_COMPLETE_CALLBACK_FAILED: {exc}")

    def _abort_start_attempt(self) -> None:
        self._ensure_lifecycle_state()
        with self._lifecycle_lock:
            self._is_running = False
            if self._accepting_requests:
                self._accepting_requests = False
                self._task_queue.put({"action": "quit"})
        worker = self._worker_thread
        if worker is not None:
            try:
                worker.join(timeout=0.1)
            except Exception:
                pass
        if worker is None or not worker.is_alive():
            self._finalize_worker_shutdown()

    def _finalize_worker_shutdown(self) -> None:
        self._ensure_lifecycle_state()
        with self._lifecycle_lock:
            self._is_running = False
            self._accepting_requests = False
            self._worker_thread = None
            self._browser_type = None
            self._context_recovery_failed = False
            self._context = None
            self._current_page = None
            self._auth_page = None
        if not self._ready_event.is_set():
            self._ready_event.set()

    @staticmethod
    def _warn(message: str) -> None:
        print(f"BROWSER_WARN {message}")

    def _enqueue_task(self, task: dict[str, Any]) -> None:
        self._ensure_lifecycle_state()
        with self._lifecycle_lock:
            if not self._is_running or not self._accepting_requests:
                raise RuntimeError("BrowserService가 실행 중이지 않습니다.")
            self._task_queue.put(task)

    def _ensure_lifecycle_state(self) -> None:
        if not hasattr(self, "_lifecycle_lock"):
            self._lifecycle_lock = threading.Lock()
        if not hasattr(self, "_task_queue"):
            self._task_queue = queue.Queue()
        if not hasattr(self, "_ready_event"):
            self._ready_event = threading.Event()
        if not hasattr(self, "_accepting_requests"):
            self._accepting_requests = bool(getattr(self, "_is_running", False))
