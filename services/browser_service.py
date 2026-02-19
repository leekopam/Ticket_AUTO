"""
Playwright browser service.

This service owns the browser session and is the single source of truth
for authentication state.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from playwright.sync_api import (
    BrowserContext,
    Error,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


@dataclass
class BrowserResolveResult:
    ok: bool
    status_code: int = 0
    location: str = ""
    error_code: str = ""
    error_message: str = ""


class BrowserService:
    def __init__(
        self,
        login_url: str = "https://witchform.com/w/login",
        user_data_dir: str = ".runtime/pw_profile",
    ):
        self._login_url = login_url
        self._user_data_dir = user_data_dir
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
        self._is_running = False
        self._task_queue.put({"action": "quit"})
        if self._worker_thread:
            self._worker_thread.join(timeout=5)

    def open_page(self, url: str) -> None:
        self._task_queue.put({"action": "open_page", "url": url})

    def click_receipt_button(self) -> None:
        self._task_queue.put({"action": "click_receipt"})

    def ensure_authenticated(self, timeout_sec: int = 180) -> bool:
        timeout_sec = max(1, timeout_sec)
        return bool(
            self._invoke_rpc(
                {"action": "ensure_authenticated", "timeout_sec": timeout_sec},
                timeout_sec=timeout_sec + 15,
            )
        )

    def resolve_qr_redirect(
        self,
        qr_url: str,
        timeout_ms: int = 8000,
    ) -> BrowserResolveResult:
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
            print(f"Playwright startup error: {exc}")
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
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
                self._handle_click_receipt()
                finish(True, None)
                return
            if action == "ensure_authenticated":
                result = self._handle_ensure_authenticated(task.get("timeout_sec", 180))
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

            finish(None, f"Unknown task action: {action}")
        except Exception as exc:
            finish(None, str(exc))

    def _handle_open_page(self, url: str) -> None:
        if not self._context:
            return

        # close previous work page but keep auth page for login recovery
        if self._current_page and not self._current_page.is_closed():
            self._current_page.close()

        self._current_page = self._context.new_page()
        self._current_page.goto(url, timeout=15000)
        print(f"PAGE_OPEN {url}")

    def _handle_click_receipt(self) -> None:
        if not self._current_page or self._current_page.is_closed():
            print("No active order page to click receipt button.")
            self._invoke_receipt_complete_callback()
            return

        try:
            # keep existing selectors to avoid UI regression
            self._current_page.click("text=수령 완료 처리")
            self._current_page.wait_for_timeout(500)

            try:
                self._current_page.click(
                    "button.modal-alert_statusUpdateBtn__RABK9",
                    timeout=3000,
                )
            except Exception:
                try:
                    self._current_page.click("text=수령 완료 처리", timeout=1000)
                except Exception:
                    print("Could not find popup confirm button.")

            self._current_page.wait_for_timeout(1500)
            self._current_page.close()
            self._current_page = None
        except Exception as exc:
            print(f"Playwright click error: {exc}")

        self._invoke_receipt_complete_callback()

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
                error_message="Browser context is not ready.",
            )

        try:
            response = self._context.request.get(
                qr_url,
                max_redirects=0,
                fail_on_status_code=False,
                timeout=timeout_ms,
            )
        except PlaywrightTimeoutError:
            return BrowserResolveResult(
                ok=False,
                error_code="NETWORK_TIMEOUT",
                error_message="Request timed out while resolving QR redirect.",
            )
        except Error as exc:
            return BrowserResolveResult(
                ok=False,
                error_code="NETWORK_ERROR",
                error_message=f"Browser request failed: {exc}",
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
                error_message="Authentication required. Please log in.",
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
                    error_message="Authentication required. Please log in.",
                )

        print(f"QR_RESOLVE_OK status={status_code} location={location}")
        return BrowserResolveResult(ok=True, status_code=status_code, location=location)

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

    def _is_authenticated(self) -> bool:
        if not self._context:
            return False

        cookies = self._context.cookies(["https://witchform.com"])
        has_phpsessid = False
        for cookie in cookies:
            if (
                cookie.get("name") == "PHPSESSID"
                and cookie.get("value")
                and "witchform.com" in cookie.get("domain", "")
            ):
                has_phpsessid = True
                break

        if not has_phpsessid:
            return False

        try:
            response = self._context.request.get(
                self._login_url,
                max_redirects=0,
                fail_on_status_code=False,
                timeout=5000,
            )
        except Exception:
            return False

        status_code = response.status
        location = response.headers.get("location", "")

        if status_code in REDIRECT_STATUS_CODES:
            return "/w/login" not in (location or "").lower()

        if status_code == 200:
            final_url = (response.url or "").lower()
            body = ""
            try:
                body = response.text()
            except Exception:
                body = ""
            return not self._looks_like_login_page(final_url, body)

        return False

    def _ensure_login_page_open(self) -> None:
        if not self._context:
            return
        if self._auth_page and not self._auth_page.is_closed():
            return

        self._auth_page = self._context.new_page()
        self._auth_page.goto(self._login_url, timeout=15000)

    def _close_auth_page_if_possible(self) -> None:
        if not self._auth_page or self._auth_page.is_closed():
            self._auth_page = None
            return
        try:
            self._auth_page.close()
        except Exception:
            pass
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

    def _invoke_receipt_complete_callback(self) -> None:
        if self._on_receipt_complete:
            self._on_receipt_complete()
