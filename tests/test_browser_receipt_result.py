from __future__ import annotations

import io
import inspect
import queue
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from models.order_model import Order
from services.browser_service import BrowserService, PageOrderDiscoveryResult, ReceiptClickResult
from viewmodels.order_viewmodel import OrderViewModel


class _FakePage:
    def __init__(
        self,
        *,
        closed: bool = False,
        close_error: Exception | None = None,
        goto_error: Exception | None = None,
        click_failures: dict[str, Exception] | None = None,
        click_sequences: dict[str, list[Exception | None]] | None = None,
        pre_confirm_wait_error: Exception | None = None,
        post_confirm_wait_error: Exception | None = None,
    ) -> None:
        self._closed = closed
        self._close_error = close_error
        self._goto_error = goto_error
        self._click_failures = click_failures or {}
        self._click_sequences = {selector: list(sequence) for selector, sequence in (click_sequences or {}).items()}
        self._pre_confirm_wait_error = pre_confirm_wait_error
        self._post_confirm_wait_error = post_confirm_wait_error
        self.click_calls: list[tuple[str, int]] = []
        self.goto_calls: list[tuple[str, int]] = []
        self.wait_calls: list[int] = []
        self.close_calls = 0

    def is_closed(self) -> bool:
        return self._closed

    def click(self, selector: str, timeout: int) -> None:
        self.click_calls.append((selector, timeout))
        sequence = self._click_sequences.get(selector)
        if sequence:
            failure = sequence.pop(0)
            if failure is not None:
                raise failure
        failure = self._click_failures.get(selector)
        if failure is not None:
            raise failure

    def wait_for_timeout(self, timeout_ms: int) -> None:
        self.wait_calls.append(timeout_ms)
        if timeout_ms == BrowserService._RECEIPT_PRE_CONFIRM_SETTLE_MS and self._pre_confirm_wait_error is not None:
            raise self._pre_confirm_wait_error
        if timeout_ms == BrowserService._RECEIPT_POST_CONFIRM_SETTLE_MS and self._post_confirm_wait_error is not None:
            raise self._post_confirm_wait_error

    def goto(self, url: str, timeout: int) -> None:
        self.goto_calls.append((url, timeout))
        if self._goto_error is not None:
            raise self._goto_error

    def close(self) -> None:
        self.close_calls += 1
        if self._close_error is not None:
            raise self._close_error
        self._closed = True


class _FakeResponse:
    def __init__(self, *, status: int, headers: dict[str, str] | None = None, url: str, body: str = "") -> None:
        self.status = status
        self.headers = headers or {}
        self.url = url
        self._body = body

    def text(self) -> str:
        return self._body


class _FakeRequest:
    def __init__(self, *, response: _FakeResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append((url, kwargs))
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


class _FakeContext:
    def __init__(
        self,
        request: _FakeRequest,
        *,
        pages: list[_FakePage] | None = None,
        new_pages: list[_FakePage] | None = None,
        cookies: list[dict[str, object]] | None = None,
    ) -> None:
        self.request = request
        self.pages = list(pages or [])
        self._new_pages = list(new_pages or [])
        self._cookies = [dict(cookie) for cookie in (cookies or [])]
        self.new_page_calls = 0
        self.clear_cookie_calls: list[str] = []
        self.add_cookie_calls: list[list[dict[str, object]]] = []

    def new_page(self) -> _FakePage:
        self.new_page_calls += 1
        if self._new_pages:
            page = self._new_pages.pop(0)
        else:
            page = _FakePage()
        self.pages.append(page)
        return page

    def cookies(self, _urls: list[str]) -> list[dict[str, object]]:
        return [dict(cookie) for cookie in self._cookies]

    def clear_cookies(self, *, domain: str) -> None:
        self.clear_cookie_calls.append(domain)
        normalized = domain.lstrip(".")
        self._cookies = [
            cookie
            for cookie in self._cookies
            if normalized not in str(cookie.get("domain", "")).lstrip(".")
        ]

    def add_cookies(self, cookies: list[dict[str, object]]) -> None:
        self.add_cookie_calls.append([dict(cookie) for cookie in cookies])
        for cookie in cookies:
            stored = dict(cookie)
            if "domain" not in stored:
                stored["domain"] = ".witchform.com"
            self._cookies.append(stored)


class _FakeEvent:
    def __init__(self, *, wait_result: bool = False) -> None:
        self._is_set = False
        self._wait_result = wait_result
        self.wait_calls: list[float] = []

    def clear(self) -> None:
        self._is_set = False

    def wait(self, timeout: float) -> bool:
        self.wait_calls.append(timeout)
        return self._wait_result or self._is_set

    def is_set(self) -> bool:
        return self._is_set

    def set(self) -> None:
        self._is_set = True


class _FakeThread:
    def __init__(self, *, on_start=None, alive: bool = False) -> None:
        self._on_start = on_start
        self._alive = alive
        self.join_calls: list[float | None] = []

    def start(self) -> None:
        if self._on_start is not None:
            self._on_start()

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)


class BrowserReceiptResultContractTest(unittest.TestCase):
    def _build_service(self) -> BrowserService:
        service = BrowserService.__new__(BrowserService)
        service._current_page = None
        service._context = None
        service._auth_page = None
        service._on_receipt_complete = None
        service._ready_event = threading.Event()
        service._is_running = True
        return service

    def test_click_receipt_button_returns_structured_result(self) -> None:
        signature = inspect.signature(BrowserService.click_receipt_button)
        return_annotation = signature.return_annotation
        self.assertIn("ReceiptClickResult", str(return_annotation))

    def test_failure_codes_exist_in_browser_service_source(self) -> None:
        source = Path("services/browser_service.py").read_text(encoding="utf-8")
        self.assertIn("NO_ACTIVE_PAGE", source)
        self.assertIn("PRIMARY_CLICK_FAIL", source)
        self.assertIn("CONFIRM_CLICK_FAIL", source)

    def test_receipt_click_result_shape(self) -> None:
        result = ReceiptClickResult(success=False, error_code="CONFIRM_CLICK_FAIL", error_message="x")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "CONFIRM_CLICK_FAIL")
        self.assertEqual(result.error_message, "x")

    def test_dispatch_task_warns_when_open_page_requested_without_browser_context(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._context = None
        service._current_page = None

        with redirect_stdout(io.StringIO()) as buffer:
            service._dispatch_task({"action": "open_page", "url": "https://example.com"})

        output = buffer.getvalue()
        self.assertIn("BROWSER_WARN TASK_FAILED action=open_page", output)
        self.assertIn("브라우저 컨텍스트가 준비되지 않았습니다.", output)

    def test_dispatch_task_returns_error_for_open_page_rpc_failure(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._context = None
        service._current_page = None
        response_queue: queue.Queue = queue.Queue(maxsize=1)

        service._dispatch_task(
            {
                "action": "open_page",
                "url": "https://example.com",
                "response_queue": response_queue,
            }
        )

        response = response_queue.get_nowait()
        self.assertIsNone(response["result"])
        self.assertEqual(response["error"], "브라우저 컨텍스트가 준비되지 않았습니다.")

    def test_handle_open_page_closes_previous_active_order_page(self) -> None:
        service = self._build_service()
        previous_page = _FakePage()
        next_page = _FakePage()
        service._current_page = previous_page
        service._context = _FakeContext(
            _FakeRequest(response=_FakeResponse(status=200, url="https://example.com")),
            new_pages=[next_page],
        )

        with redirect_stdout(io.StringIO()):
            service._handle_open_page("https://example.com/orders/1")

        self.assertEqual(previous_page.close_calls, 1)
        self.assertTrue(previous_page.is_closed())
        self.assertIs(service._current_page, next_page)
        self.assertEqual(next_page.goto_calls, [("https://example.com/orders/1", 15000)])

    def test_handle_open_page_preserves_active_order_page_for_independent_login(self) -> None:
        service = self._build_service()
        order_page = _FakePage()
        login_page = _FakePage()
        service._current_page = order_page
        service._context = _FakeContext(
            _FakeRequest(response=_FakeResponse(status=200, url="https://example.com")),
            new_pages=[login_page],
        )

        with redirect_stdout(io.StringIO()):
            service._handle_open_page("https://example.com/login", preserve_current_page=True)

        self.assertEqual(order_page.close_calls, 0)
        self.assertIs(service._current_page, order_page)
        self.assertEqual(login_page.goto_calls, [("https://example.com/login", 15000)])

    def test_handle_open_page_still_opens_new_page_when_previous_close_fails(self) -> None:
        service = self._build_service()
        previous_page = _FakePage(close_error=RuntimeError("close failed"))
        next_page = _FakePage()
        service._current_page = previous_page
        service._context = _FakeContext(
            _FakeRequest(response=_FakeResponse(status=200, url="https://example.com")),
            new_pages=[next_page],
        )

        with redirect_stdout(io.StringIO()) as buffer:
            service._handle_open_page("https://example.com/orders/2")

        output = buffer.getvalue()
        self.assertIn("BROWSER_WARN ORDER_PAGE_CLOSE_FAILED: close failed", output)
        self.assertEqual(previous_page.close_calls, 1)
        self.assertIs(service._current_page, next_page)
        self.assertEqual(next_page.goto_calls, [("https://example.com/orders/2", 15000)])

    def test_handle_open_page_cleans_up_failed_new_page_and_reraises(self) -> None:
        service = self._build_service()
        failing_page = _FakePage(goto_error=RuntimeError("goto failed"))
        service._current_page = None
        service._context = _FakeContext(
            _FakeRequest(response=_FakeResponse(status=200, url="https://example.com")),
            new_pages=[failing_page],
        )

        with self.assertRaisesRegex(RuntimeError, "goto failed"):
            service._handle_open_page("https://example.com/orders/3")

        self.assertEqual(failing_page.close_calls, 1)
        self.assertTrue(failing_page.is_closed())
        self.assertIsNone(service._current_page)

    def test_extract_order_number_candidate_from_labelled_text(self) -> None:
        result = BrowserService._extract_order_number_candidate(
            "주문번호: WFLM7QSDTC_69D53CU23685"
        )
        self.assertEqual(result, "WFLM7QSDTC_69D53CU23685")

    def test_extract_order_number_candidate_from_url(self) -> None:
        result = BrowserService._extract_order_number_candidate(
            "https://witchform.com/w/myform/sellForm-history-detail/WFLM7QSDTC_69D53CU23685"
        )
        self.assertEqual(result, "WFLM7QSDTC_69D53CU23685")

    def test_extract_order_number_candidate_keeps_legacy_numeric_format(self) -> None:
        result = BrowserService._extract_order_number_candidate(
            "https://witchform.com/w/myform/sellForm-history-detail/14872765?idx=980235",
            "주문번호: 980235_14872765",
        )
        self.assertEqual(result, "980235_14872765")

    def test_extract_order_number_candidate_rejects_html_class_names(self) -> None:
        result = BrowserService._extract_order_number_candidate(
            '<div class="video_slider">test</div>',
            "video_slider",
        )
        self.assertEqual(result, "")

    def test_extract_buyer_contact_candidate(self) -> None:
        buyer_name, buyer_phone = BrowserService._extract_buyer_contact_candidate(
            "주문자명\n홍영기\n주문자 연락처\n010-1234-5678"
        )
        self.assertEqual(buyer_name, "홍영기")
        self.assertEqual(buyer_phone, "010-1234-5678")

    def test_finalize_worker_shutdown_clears_runtime_state(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._is_running = True
        service._worker_thread = object()
        service._context = object()
        service._current_page = object()
        service._auth_page = object()
        service._ready_event = threading.Event()

        service._finalize_worker_shutdown()

        self.assertFalse(service._is_running)
        self.assertIsNone(service._worker_thread)
        self.assertIsNone(service._context)
        self.assertIsNone(service._current_page)
        self.assertIsNone(service._auth_page)
        self.assertTrue(service._ready_event.is_set())

    def test_start_timeout_resets_state_after_failed_boot_attempt(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._is_running = False
        service._startup_error = None
        service._ready_event = _FakeEvent(wait_result=False)
        service._worker_thread = None
        service._context = object()
        service._current_page = object()
        service._auth_page = object()

        fake_thread = _FakeThread(alive=False)

        with patch("services.browser_service.threading.Thread", return_value=fake_thread):
            with self.assertRaisesRegex(RuntimeError, "브라우저 워커 시작 시간 초과"):
                service.start()

        self.assertFalse(service._is_running)
        self.assertIsNone(service._worker_thread)
        self.assertIsNone(service._context)
        self.assertIsNone(service._current_page)
        self.assertIsNone(service._auth_page)
        self.assertTrue(service._ready_event.is_set())
        self.assertEqual(fake_thread.join_calls, [0.1])

    def test_start_startup_error_resets_state_after_failed_boot_attempt(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._is_running = False
        service._startup_error = None
        service._ready_event = _FakeEvent(wait_result=True)
        service._worker_thread = None
        service._context = object()
        service._current_page = object()
        service._auth_page = object()

        def _on_start() -> None:
            service._startup_error = RuntimeError("boom")
            service._ready_event.set()

        fake_thread = _FakeThread(on_start=_on_start, alive=False)

        with patch("services.browser_service.threading.Thread", return_value=fake_thread):
            with self.assertRaisesRegex(RuntimeError, "브라우저 워커 실패: boom"):
                service.start()

        self.assertFalse(service._is_running)
        self.assertIsNone(service._worker_thread)
        self.assertIsNone(service._context)
        self.assertIsNone(service._current_page)
        self.assertIsNone(service._auth_page)
        self.assertEqual(fake_thread.join_calls, [0.1])

    def test_start_rejects_new_worker_while_previous_worker_is_still_alive(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._is_running = False
        service._startup_error = None
        service._ready_event = _FakeEvent(wait_result=False)
        service._worker_thread = _FakeThread(alive=True)
        service._context = None
        service._current_page = None
        service._auth_page = None

        with self.assertRaisesRegex(RuntimeError, "브라우저 워커가 아직 정리 중입니다"):
            service.start()

    def test_stop_waits_for_worker_cleanup_even_when_running_flag_is_already_cleared(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._is_running = False
        service._worker_thread = _FakeThread(alive=True)
        service._task_queue = queue.Queue()

        service.stop()

        self.assertEqual(service._worker_thread.join_calls, [15])
        self.assertTrue(service._task_queue.empty())

    def test_stop_enqueues_quit_and_joins_worker_when_running(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._is_running = True
        service._worker_thread = _FakeThread(alive=True)
        service._task_queue = queue.Queue()

        service.stop()

        self.assertFalse(service._is_running)
        self.assertEqual(service._worker_thread.join_calls, [15])
        self.assertEqual(service._task_queue.get_nowait(), {"action": "quit"})

    def test_open_page_raises_when_service_is_not_running(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._is_running = False
        service._task_queue = queue.Queue()

        with self.assertRaisesRegex(RuntimeError, "BrowserService가 실행 중이지 않습니다."):
            service.open_page("https://example.com")

        self.assertTrue(service._task_queue.empty())

    def test_open_page_uses_bounded_rpc_when_service_is_running(self) -> None:
        service = BrowserService.__new__(BrowserService)
        service._is_running = True
        service._accepting_requests = True
        service._task_queue = queue.Queue()
        service._lifecycle_lock = threading.Lock()
        calls: list[tuple[dict[str, object], int]] = []

        def _fake_invoke_rpc(task: dict[str, object], timeout_sec: int) -> bool:
            calls.append((dict(task), timeout_sec))
            return True

        service._invoke_rpc = _fake_invoke_rpc

        result = service.open_page("https://example.com")

        self.assertTrue(result)
        self.assertEqual(
            calls,
            [({"action": "open_page", "url": "https://example.com"}, 25)],
        )

    def test_open_page_raises_worker_navigation_failure_to_caller(self) -> None:
        service = self._build_service()
        failing_page = _FakePage(goto_error=RuntimeError("goto failed"))
        service._context = _FakeContext(
            _FakeRequest(response=_FakeResponse(status=200, url="https://example.com")),
            new_pages=[failing_page],
        )
        service._accepting_requests = True
        service._task_queue = queue.Queue()
        service._lifecycle_lock = threading.Lock()

        def _worker_once() -> None:
            task = service._task_queue.get(timeout=1)
            service._dispatch_task(task)

        worker_thread = threading.Thread(target=_worker_once, daemon=True)
        worker_thread.start()

        raised: RuntimeError | None = None
        try:
            service.open_page("https://example.com/orders/3")
        except RuntimeError as exc:
            raised = exc

        worker_thread.join(timeout=1)
        self.assertFalse(worker_thread.is_alive())
        self.assertIsNotNone(raised)
        self.assertIn("goto failed", str(raised))
        self.assertEqual(failing_page.close_calls, 1)
        self.assertIsNone(service._current_page)

    def test_order_viewmodel_open_current_order_page_surfaces_browser_navigation_failure(self) -> None:
        service = self._build_service()
        failing_page = _FakePage(goto_error=RuntimeError("restricted page timeout"))
        service._context = _FakeContext(
            _FakeRequest(response=_FakeResponse(status=200, url="https://example.com")),
            new_pages=[failing_page],
        )
        service._accepting_requests = True
        service._task_queue = queue.Queue()
        service._lifecycle_lock = threading.Lock()
        order_viewmodel = OrderViewModel(SimpleNamespace(), service)
        order_viewmodel._current_order = Order(
            order_number="ORDER-001",
            name="Test",
            phone="010",
            seat="A-1",
            goods=[],
            url="https://example.com/orders/restricted",
        )

        def _worker_once() -> None:
            task = service._task_queue.get(timeout=1)
            service._dispatch_task(task)

        worker_thread = threading.Thread(target=_worker_once, daemon=True)
        worker_thread.start()

        raised: RuntimeError | None = None
        try:
            order_viewmodel.open_current_order_page()
        except RuntimeError as exc:
            raised = exc

        worker_thread.join(timeout=1)
        self.assertFalse(worker_thread.is_alive())
        self.assertIsNotNone(raised)
        self.assertIn("restricted page timeout", str(raised))

    def test_order_discovery_log_masks_buyer_contact(self) -> None:
        service = BrowserService.__new__(BrowserService)
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            service._print_order_discovery_result(
                PageOrderDiscoveryResult(
                    order_number="WFLM7QSDTC_69D53CU23685",
                    buyer_name="홍영기",
                    buyer_phone="010-1234-5678",
                    url="https://witchform.com/orders/1",
                )
            )

        output = buffer.getvalue()
        self.assertIn("buyer_name=홍*기", output)
        self.assertIn("buyer_phone=010-****-5678", output)
        self.assertNotIn("buyer_name=홍영기", output)
        self.assertNotIn("buyer_phone=010-1234-5678", output)

    def test_handle_get_auth_cookie_snapshot_filters_auth_like_witchform_cookies(self) -> None:
        service = self._build_service()
        service._context = _FakeContext(
            _FakeRequest(response=_FakeResponse(status=200, url="https://example.com")),
            cookies=[
                {"name": "PHPSESSID", "value": "sess1234567890", "domain": ".witchform.com"},
                {"name": "AUTH_TOKEN", "value": "token1234567890", "domain": ".witchform.com"},
                {"name": "theme", "value": "dark", "domain": ".witchform.com"},
                {"name": "PHPSESSID", "value": "other", "domain": ".example.com"},
            ],
        )

        with redirect_stdout(io.StringIO()):
            snapshot = service._handle_get_auth_cookie_snapshot()

        self.assertEqual(
            snapshot,
            {
                "PHPSESSID": "sess1234567890",
                "AUTH_TOKEN": "token1234567890",
            },
        )

    def test_handle_replace_auth_cookie_snapshot_replaces_auth_like_cookies_only(self) -> None:
        service = self._build_service()
        fake_context = _FakeContext(
            _FakeRequest(response=_FakeResponse(status=200, url="https://example.com")),
            cookies=[
                {"name": "PHPSESSID", "value": "old-session", "domain": ".witchform.com"},
                {"name": "theme", "value": "dark", "domain": ".witchform.com"},
            ],
        )
        service._context = fake_context

        replaced = service._handle_replace_auth_cookie_snapshot(
            {
                "PHPSESSID": "sess1234567890",
                "AUTH_TOKEN": "token1234567890",
                "theme": "light",
                "": "ignored",
            }
        )

        self.assertTrue(replaced)
        self.assertEqual(fake_context.clear_cookie_calls, [".witchform.com", "witchform.com"])
        self.assertEqual(len(fake_context.add_cookie_calls), 1)
        self.assertEqual(
            fake_context.add_cookie_calls[0],
            [
                {
                    "name": "PHPSESSID",
                    "value": "sess1234567890",
                    "url": "https://witchform.com/",
                },
                {
                    "name": "AUTH_TOKEN",
                    "value": "token1234567890",
                    "url": "https://witchform.com/",
                },
            ],
        )
        with redirect_stdout(io.StringIO()):
            snapshot = service._handle_get_auth_cookie_snapshot()
        self.assertEqual(
            snapshot,
            {
                "PHPSESSID": "sess1234567890",
                "AUTH_TOKEN": "token1234567890",
            },
        )

    def test_handle_click_receipt_reports_no_active_page(self) -> None:
        service = self._build_service()
        callback_calls: list[str] = []
        service._on_receipt_complete = lambda: callback_calls.append("done")

        result = service._handle_click_receipt()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "NO_ACTIVE_PAGE")
        self.assertEqual(callback_calls, ["done"])

    def test_handle_click_receipt_reports_primary_click_failure(self) -> None:
        service = self._build_service()
        callback_calls: list[str] = []
        service._on_receipt_complete = lambda: callback_calls.append("done")
        service._current_page = _FakePage(
            click_failures={"text=수령 완료 처리": RuntimeError("primary click failed")}
        )

        result = service._handle_click_receipt()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "PRIMARY_CLICK_FAIL")
        self.assertIn("primary click failed", result.error_message)
        self.assertEqual(callback_calls, ["done"])

    def test_handle_click_receipt_reports_confirm_click_failure(self) -> None:
        service = self._build_service()
        callback_calls: list[str] = []
        service._on_receipt_complete = lambda: callback_calls.append("done")
        service._current_page = _FakePage(
            click_failures={
                "button.modal-alert_statusUpdateBtn__RABK9": RuntimeError("not found"),
                "text=확인": RuntimeError("not found"),
            },
            click_sequences={
                "text=수령 완료 처리": [None, RuntimeError("confirm missing")],
            },
        )

        result = service._handle_click_receipt()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "CONFIRM_CLICK_FAIL")
        self.assertEqual(callback_calls, ["done"])

    def test_handle_click_receipt_treats_confirm_click_failure_as_already_received_when_page_matches(self) -> None:
        service = self._build_service()
        callback_calls: list[str] = []
        page = _FakePage()
        original_click = page.click

        def _click_with_confirm_failures(selector: str, timeout: int) -> None:
            if page.click_calls:
                page.click_calls.append((selector, timeout))
                raise RuntimeError("confirm missing")
            original_click(selector, timeout)

        page.click = _click_with_confirm_failures
        service._on_receipt_complete = lambda: callback_calls.append("done")
        service._current_page = page
        service._page_shows_already_received = lambda: True

        result = service._handle_click_receipt()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "ALREADY_RECEIVED")
        self.assertEqual(page.close_calls, 1)
        self.assertIsNone(service._current_page)
        self.assertEqual(callback_calls, ["done"])

    def test_handle_click_receipt_reports_pre_confirm_wait_failure_without_closing_page(self) -> None:
        service = self._build_service()
        callback_calls: list[str] = []
        page = _FakePage(pre_confirm_wait_error=RuntimeError("pre confirm wait failed"))
        service._on_receipt_complete = lambda: callback_calls.append("done")
        service._current_page = page

        result = service._handle_click_receipt()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "CONFIRM_CLICK_FAIL")
        self.assertIn("pre confirm wait failed", result.error_message)
        self.assertEqual(page.wait_calls, [BrowserService._RECEIPT_PRE_CONFIRM_SETTLE_MS])
        self.assertEqual(page.close_calls, 0)
        self.assertIs(service._current_page, page)
        self.assertEqual(callback_calls, ["done"])

    def test_handle_click_receipt_treats_pre_confirm_wait_failure_as_already_received_when_page_matches(self) -> None:
        service = self._build_service()
        callback_calls: list[str] = []
        page = _FakePage(pre_confirm_wait_error=RuntimeError("pre confirm wait failed"))
        service._on_receipt_complete = lambda: callback_calls.append("done")
        service._current_page = page
        service._page_shows_already_received = lambda: True

        result = service._handle_click_receipt()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "ALREADY_RECEIVED")
        self.assertEqual(page.close_calls, 1)
        self.assertIsNone(service._current_page)
        self.assertEqual(callback_calls, ["done"])

    def test_handle_click_receipt_reports_post_confirm_wait_failure(self) -> None:
        service = self._build_service()
        callback_calls: list[str] = []
        service._on_receipt_complete = lambda: callback_calls.append("done")
        service._current_page = _FakePage(post_confirm_wait_error=RuntimeError("wait failed"))

        result = service._handle_click_receipt()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "CONFIRM_CLICK_FAIL")
        self.assertIn("wait failed", result.error_message)
        self.assertEqual(callback_calls, ["done"])

    def test_handle_click_receipt_treats_post_confirm_wait_failure_as_already_received_when_page_matches(self) -> None:
        service = self._build_service()
        callback_calls: list[str] = []
        page = _FakePage(post_confirm_wait_error=RuntimeError("wait failed"))
        service._on_receipt_complete = lambda: callback_calls.append("done")
        service._current_page = page
        service._page_shows_already_received = lambda: True

        result = service._handle_click_receipt()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "ALREADY_RECEIVED")
        self.assertEqual(page.close_calls, 1)
        self.assertIsNone(service._current_page)
        self.assertEqual(callback_calls, ["done"])

    def test_handle_click_receipt_success_closes_page_and_invokes_callback_once(self) -> None:
        service = self._build_service()
        callback_calls: list[str] = []
        page = _FakePage()
        service._on_receipt_complete = lambda: callback_calls.append("done")
        service._current_page = page

        result = service._handle_click_receipt()

        self.assertTrue(result.success)
        self.assertIsNone(service._current_page)
        self.assertEqual(page.close_calls, 1)
        self.assertEqual(callback_calls, ["done"])

    def test_receipt_complete_callback_failure_does_not_override_failure_result(self) -> None:
        service = self._build_service()
        service._on_receipt_complete = lambda: (_ for _ in ()).throw(RuntimeError("callback boom"))

        with redirect_stdout(io.StringIO()) as buffer:
            result = service._handle_click_receipt()

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "NO_ACTIVE_PAGE")
        self.assertIn("RECEIPT_COMPLETE_CALLBACK_FAILED", buffer.getvalue())

    def test_receipt_complete_callback_failure_does_not_override_success_result(self) -> None:
        service = self._build_service()
        service._on_receipt_complete = lambda: (_ for _ in ()).throw(RuntimeError("callback boom"))
        service._current_page = _FakePage()

        with redirect_stdout(io.StringIO()) as buffer:
            result = service._handle_click_receipt()

        self.assertTrue(result.success)
        self.assertIn("RECEIPT_COMPLETE_CALLBACK_FAILED", buffer.getvalue())

    def test_handle_resolve_qr_redirect_reports_network_timeout(self) -> None:
        service = self._build_service()
        service._context = _FakeContext(
            _FakeRequest(error=PlaywrightTimeoutError("timed out"))
        )

        result = service._handle_resolve_qr_redirect(
            "https://witchform.com/qrcode_link.php?a=1",
            timeout_ms=8000,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "NETWORK_TIMEOUT")

    def test_handle_resolve_qr_redirect_reports_unexpected_request_failure_as_network_error(self) -> None:
        service = self._build_service()
        service._context = _FakeContext(
            _FakeRequest(error=RuntimeError("boom"))
        )

        result = service._handle_resolve_qr_redirect(
            "https://witchform.com/qrcode_link.php?a=1",
            timeout_ms=8000,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "NETWORK_ERROR")
        self.assertIn("boom", result.error_message)

    def test_handle_resolve_qr_redirect_detects_login_redirect(self) -> None:
        service = self._build_service()
        service._context = _FakeContext(
            _FakeRequest(
                response=_FakeResponse(
                    status=302,
                    headers={"location": "/w/login?next=/orders/1"},
                    url="https://witchform.com/qrcode_link.php?a=1",
                )
            )
        )

        result = service._handle_resolve_qr_redirect(
            "https://witchform.com/qrcode_link.php?a=1",
            timeout_ms=8000,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "AUTH_REQUIRED")
        self.assertEqual(result.location, "/w/login?next=/orders/1")

    def test_handle_resolve_qr_redirect_detects_login_page_html(self) -> None:
        service = self._build_service()
        service._context = _FakeContext(
            _FakeRequest(
                response=_FakeResponse(
                    status=200,
                    headers={},
                    url="https://witchform.com/orders/1",
                    body='<form><input name="userid"><input type="password">login</form>',
                )
            )
        )

        result = service._handle_resolve_qr_redirect(
            "https://witchform.com/qrcode_link.php?a=1",
            timeout_ms=8000,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "AUTH_REQUIRED")


if __name__ == "__main__":
    unittest.main()
