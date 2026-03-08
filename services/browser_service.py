"""
Playwright browser service.

This service owns the browser session and is the single source of truth
for authentication state.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import unquote

from playwright.sync_api import (
    BrowserContext,
    Error,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


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
    def __init__(
        self,
        login_url: str = "https://witchform.com/w/login",
        user_data_dir: str = ".runtime/pw_profile",
        require_login_each_run: bool = True,
    ):
        self._login_url = login_url
        self._user_data_dir = user_data_dir
        self._require_login_each_run = require_login_each_run
        self._task_queue: queue.Queue = queue.Queue()
        self._current_page: Page | None = None
        self._auth_page: Page | None = None
        self._context: BrowserContext | None = None
        self._worker_thread: threading.Thread | None = None
        self._is_running = False
        self._startup_error: Exception | None = None
        self._ready_event = threading.Event()

        # callback fired when receipt click flow is finished
        self._on_receipt_complete: Callable[[], None] | None = None

    def set_on_receipt_complete(self, callback: Callable[[], None]) -> None:
        self._on_receipt_complete = callback

    def start(self) -> None:
        if self._is_running:
            return

        self._is_running = True
        self._startup_error = None
        self._ready_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        if not self._ready_event.wait(timeout=20):
            raise RuntimeError("Browser worker startup timed out.")

        if self._startup_error is not None:
            raise RuntimeError(f"Browser worker failed: {self._startup_error}")

    def stop(self) -> None:
        if not self._is_running:
            return
        self._is_running = False
        self._task_queue.put({"action": "quit"})
        if self._worker_thread:
            self._worker_thread.join(timeout=15)

    def open_page(self, url: str) -> None:
        self._task_queue.put({"action": "open_page", "url": url})

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
        """Force authentication reset and keep login page open for user interaction."""
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
        if not self._is_running:
            raise RuntimeError("BrowserService is not running.")

        response_queue: queue.Queue = queue.Queue(maxsize=1)
        task["response_queue"] = response_queue
        self._task_queue.put(task)

        try:
            response = response_queue.get(timeout=timeout_sec)
        except queue.Empty as exc:
            raise RuntimeError(f"Task timed out: {task.get('action')}") from exc

        error = response.get("error")
        if error is not None:
            raise RuntimeError(error)

        return response.get("result")

    def _worker_loop(self) -> None:
        context: BrowserContext | None = None
        try:
            os.makedirs(self._user_data_dir, exist_ok=True)

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self._user_data_dir,
                    headless=False,
                )
                self._context = context
                self._open_initial_witchform_page()
                if self._require_login_each_run:
                    self._handle_clear_auth_state_for_domain()
                self._ready_event.set()

                while self._is_running:
                    try:
                        task = self._task_queue.get(timeout=0.5)
                        self._dispatch_task(task)
                    except queue.Empty:
                        continue
        except Exception as exc:
            self._startup_error = exc
            if not self._ready_event.is_set():
                self._ready_event.set()
            print(f"Playwright 시작 오류: {exc}")
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception as exc:
                    self._warn(f"CONTEXT_CLOSE_FAILED: {exc}")
            self._context = None
            self._current_page = None
            self._auth_page = None
            if not self._ready_event.is_set():
                self._ready_event.set()

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
                self._handle_open_page(task["url"])
                finish(True, None)
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
            finish(None, str(exc))

    def _handle_open_page(self, url: str) -> None:
        if not self._context:
            return

        self._current_page = self._context.new_page()
        self._current_page.goto(url, timeout=15000)
        print(f"PAGE_OPEN {url}")

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
            # keep existing selectors to avoid UI regression
            self._current_page.click("text=수령 완료 처리", timeout=5000)
        except Exception as exc:
            message = f"수령 완료 1차 버튼 클릭 실패: {exc}"
            print(message)
            self._invoke_receipt_complete_callback()
            return ReceiptClickResult(
                success=False,
                error_code="PRIMARY_CLICK_FAIL",
                error_message=message,
            )

        self._current_page.wait_for_timeout(500)

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
            message = "수령 완료 확인 팝업 버튼 클릭 실패"
            print(message)
            self._invoke_receipt_complete_callback()
            return ReceiptClickResult(
                success=False,
                error_code="CONFIRM_CLICK_FAIL",
                error_message=message,
            )

        try:
            self._current_page.wait_for_timeout(1200)
        except Exception as exc:
            print(f"Playwright 클릭 오류: {exc}")
            self._invoke_receipt_complete_callback()
            return ReceiptClickResult(
                success=False,
                error_code="CONFIRM_CLICK_FAIL",
                error_message=f"수령 완료 처리 후 페이지 상태 확인 실패: {exc}",
            )

        # 수령완료 처리 성공 후 구매자 정보 페이지를 닫는다
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
        masked_phone = self._mask_secret(result.buyer_phone) if result.buyer_phone else ""
        print(
            "ORDER_DISCOVERY "
            f"order_number={result.order_number} "
            f"buyer_name={result.buyer_name} "
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
        self._auth_page.goto(self._login_url, timeout=15000)

    def _open_initial_witchform_page(self) -> None:
        """Open only the witchform login page and remove blank tabs on startup."""
        if not self._context:
            return

        pages = [page for page in self._context.pages if not page.is_closed()]
        if pages:
            target_page = pages[0]
        else:
            target_page = self._context.new_page()

        try:
            target_page.goto(self._login_url, timeout=15000)
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
        """Clear witchform auth state for this run."""
        if not self._context:
            return False

        for domain in (".witchform.com", "witchform.com"):
            try:
                self._context.clear_cookies(domain=domain)
            except Exception as exc:
                self._warn(f"CLEAR_COOKIES_FAILED domain={domain}: {exc}")

        if self._auth_page and not self._auth_page.is_closed():
            target_page = self._auth_page
        elif self._context.pages:
            target_page = next((p for p in self._context.pages if not p.is_closed()), None)
        else:
            target_page = None

        if target_page is None:
            target_page = self._context.new_page()

        try:
            target_page.goto(self._login_url, timeout=15000)
            target_page.evaluate(
                "() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"
            )
        except Exception as exc:
            self._warn(f"AUTH_STATE_LOGIN_PREP_FAILED: {exc}")
        self._auth_page = target_page

        for page in list(self._context.pages):
            if page.is_closed() or page == target_page:
                continue
            try:
                page.close()
            except Exception as exc:
                self._warn(f"AUTH_STATE_EXTRA_PAGE_CLOSE_FAILED: {exc}")

        return True

    def _handle_request_relogin(self) -> bool:
        """Public runtime-safe relogin trigger."""
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
            self._on_receipt_complete()

    @staticmethod
    def _warn(message: str) -> None:
        print(f"BROWSER_WARN {message}")
