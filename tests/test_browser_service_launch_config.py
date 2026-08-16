from __future__ import annotations

import io
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import Error

from services.browser_service import BrowserService


class _FakeContext:
    def __init__(self, *, pages: list["_FakePage"] | None = None) -> None:
        self.closed = False
        self.events: list[tuple[str, object]] = []
        self.pages = list(pages or [])
        for page in self.pages:
            page.events = self.events
        self.clear_cookie_calls: list[str] = []
        self.new_page_calls = 0

    def close(self) -> None:
        self.closed = True

    def new_page(self) -> "_FakePage":
        self.new_page_calls += 1
        page = _FakePage(events=self.events)
        self.pages.append(page)
        return page

    def clear_cookies(self, *, domain: str) -> None:
        self.clear_cookie_calls.append(domain)
        self.events.append(("clear_cookies", domain))


class _FakePage:
    def __init__(self, *, closed: bool = False, events: list[tuple[str, object]] | None = None) -> None:
        self._closed = closed
        self.events = events if events is not None else []
        self.goto_calls: list[dict[str, object]] = []
        self.evaluate_calls: list[str] = []
        self.click_calls: list[tuple[object, ...]] = []
        self.close_calls = 0

    def is_closed(self) -> bool:
        return self._closed

    def goto(self, url: str, timeout: int, **kwargs: object) -> None:
        call = {"url": url, "timeout": timeout, **kwargs}
        self.goto_calls.append(call)
        self.events.append(("goto", call))

    def evaluate(self, script: str) -> None:
        self.evaluate_calls.append(script)
        self.events.append(("evaluate", script))

    def click(self, *args: object, **kwargs: object) -> None:
        self.click_calls.append((*args, kwargs))

    def close(self) -> None:
        self.close_calls += 1
        self._closed = True


class _FakePlaywrightContextManager:
    def __init__(
        self,
        launch_calls: list[dict[str, object]],
        context: _FakeContext | list[_FakeContext | Exception],
    ) -> None:
        self._launch_calls = launch_calls
        self._contexts = iter(context if isinstance(context, list) else [context])

    def __enter__(self) -> SimpleNamespace:
        def _launch_persistent_context(**kwargs: object) -> _FakeContext:
            self._launch_calls.append(kwargs)
            next_context = next(self._contexts)
            if isinstance(next_context, Exception):
                raise next_context
            return next_context

        return SimpleNamespace(
            chromium=SimpleNamespace(
                launch_persistent_context=_launch_persistent_context,
            )
        )

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class BrowserServiceLaunchConfigTest(unittest.TestCase):
    def _run_worker_loop_with_service(self, service: BrowserService) -> tuple[list[dict[str, object]], _FakeContext]:
        launch_calls: list[dict[str, object]] = []
        context = _FakeContext()
        service._task_queue.put({"action": "quit"})

        with (
            patch("services.browser_service.os.makedirs"),
            patch(
                "services.browser_service.sync_playwright",
                return_value=_FakePlaywrightContextManager(launch_calls, context),
            ),
            patch.object(BrowserService, "_open_initial_witchform_page", autospec=True),
        ):
            service._worker_loop()

        return launch_calls, context

    def _run_worker_loop_with_context(
        self,
        service: BrowserService,
        context: _FakeContext,
    ) -> list[dict[str, object]]:
        launch_calls: list[dict[str, object]] = []
        service._task_queue.put({"action": "quit"})

        with (
            patch("services.browser_service.os.makedirs"),
            patch(
                "services.browser_service.sync_playwright",
                return_value=_FakePlaywrightContextManager(launch_calls, context),
            ),
            redirect_stdout(io.StringIO()),
        ):
            service._worker_loop()

        return launch_calls

    def test_worker_loop_defaults_to_headed_browser_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with (
                patch("project_paths.PROJECT_ROOT", root),
                patch("project_paths.BUNDLE_ROOT", root),
            ):
                service = BrowserService(
                    user_data_dir=".runtime/test-profile",
                    require_login_each_run=False,
                )

            launch_calls, context = self._run_worker_loop_with_service(service)

            self.assertEqual(len(launch_calls), 1)
            self.assertEqual(launch_calls[0]["user_data_dir"], str(root / ".runtime" / "test-profile"))
            self.assertFalse(launch_calls[0]["headless"])
            self.assertTrue(context.closed)

    def test_worker_loop_uses_headless_mode_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with (
                patch("project_paths.PROJECT_ROOT", root),
                patch("project_paths.BUNDLE_ROOT", root),
            ):
                service = BrowserService(
                    user_data_dir=".runtime/test-profile",
                    require_login_each_run=False,
                    headless=True,
                )

            launch_calls, context = self._run_worker_loop_with_service(service)

            self.assertEqual(len(launch_calls), 1)
            self.assertEqual(launch_calls[0]["user_data_dir"], str(root / ".runtime" / "test-profile"))
            self.assertTrue(launch_calls[0]["headless"])
            self.assertTrue(context.closed)

    def test_login_page_navigation_uses_domcontentloaded_wait(self) -> None:
        service = BrowserService(require_login_each_run=False)
        auth_page = _FakePage()
        service._context = _FakeContext(pages=[auth_page])

        service._open_initial_witchform_page()
        service._auth_page = None
        service._ensure_login_page_open()
        service._handle_clear_auth_state_for_domain()

        for call in auth_page.goto_calls:
            self.assertEqual(call["url"], "https://witchform.com/w/login")
            self.assertEqual(call["timeout"], 15000)
            self.assertEqual(call.get("wait_until"), "domcontentloaded")

    def test_independent_login_opens_configured_url_without_order_or_receipt_actions(self) -> None:
        service = BrowserService(
            login_url="https://login.example.test/configured",
            require_login_each_run=False,
        )
        order_page = _FakePage()
        context = _FakeContext(pages=[order_page])
        service._context = context
        service._current_page = order_page

        with (
            patch.object(service, "resolve_qr_redirect") as resolve_qr_redirect,
            patch.object(service, "click_receipt_button") as click_receipt_button,
        ):
            service._handle_open_page(service.login_url, preserve_current_page=True)

        login_page = context.pages[-1]
        self.assertEqual(context.new_page_calls, 1)
        self.assertEqual(login_page.goto_calls, [{"url": service.login_url, "timeout": 15000}])
        self.assertEqual(order_page.close_calls, 0)
        resolve_qr_redirect.assert_not_called()
        click_receipt_button.assert_not_called()
        self.assertEqual(order_page.click_calls, [])
        self.assertEqual(login_page.click_calls, [])

    def test_open_page_replaces_closed_context_and_opens_configured_login_url(self) -> None:
        service = BrowserService(
            login_url="https://login.example.test/configured",
            require_login_each_run=False,
        )
        closed_context = _FakeContext()
        replacement_context = _FakeContext()
        launch_calls: list[dict[str, object]] = []

        with (
            patch("services.browser_service.os.makedirs"),
            patch(
                "services.browser_service.sync_playwright",
                return_value=_FakePlaywrightContextManager(launch_calls, [closed_context, replacement_context]),
            ),
            redirect_stdout(io.StringIO()),
        ):
            service.start()
            closed_context.close()
            with patch.object(
                closed_context,
                "new_page",
                side_effect=Error("Target page, context or browser has been closed"),
            ):
                self.assertTrue(service.open_page(service.login_url, preserve_current_page=True))
            self.assertIs(service._context, replacement_context)
            self.assertEqual(len(launch_calls), 2)
            self.assertEqual(
                replacement_context.pages[-1].goto_calls,
                [{"url": service.login_url, "timeout": 15000}],
            )
            service.stop()

    def test_open_page_keeps_valid_context_and_current_page(self) -> None:
        service = BrowserService(
            login_url="https://login.example.test/configured",
            require_login_each_run=False,
        )
        current_page = _FakePage()
        context = _FakeContext(pages=[current_page])
        launch_calls: list[dict[str, object]] = []

        with (
            patch("services.browser_service.os.makedirs"),
            patch(
                "services.browser_service.sync_playwright",
                return_value=_FakePlaywrightContextManager(launch_calls, context),
            ),
            redirect_stdout(io.StringIO()),
        ):
            service.start()
            service._current_page = current_page

            self.assertTrue(service.open_page(service.login_url, preserve_current_page=True))
            self.assertIs(service._context, context)
            self.assertEqual(len(launch_calls), 1)
            self.assertEqual(current_page.close_calls, 0)
            self.assertEqual(
                context.pages[-1].goto_calls,
                [{"url": service.login_url, "timeout": 15000}],
            )
            service.stop()

    def test_open_page_returns_false_when_closed_context_retry_fails(self) -> None:
        service = BrowserService(require_login_each_run=False)
        closed_context = _FakeContext()
        replacement_context = _FakeContext()
        launch_calls: list[dict[str, object]] = []

        with (
            patch("services.browser_service.os.makedirs"),
            patch(
                "services.browser_service.sync_playwright",
                return_value=_FakePlaywrightContextManager(launch_calls, [closed_context, replacement_context]),
            ),
            redirect_stdout(io.StringIO()),
        ):
            service.start()
            closed_context.close()
            with (
                patch.object(
                    closed_context,
                    "new_page",
                    side_effect=Error("Target page, context or browser has been closed"),
                ),
                patch.object(replacement_context, "new_page", side_effect=Error("retry failed")),
            ):
                self.assertFalse(service.open_page(service.login_url, preserve_current_page=True))
            self.assertEqual(len(launch_calls), 2)
            service.stop()

    def test_open_page_returns_false_when_closed_context_replacement_fails(self) -> None:
        service = BrowserService(require_login_each_run=False)
        closed_context = _FakeContext()
        launch_calls: list[dict[str, object]] = []

        with (
            patch("services.browser_service.os.makedirs"),
            patch(
                "services.browser_service.sync_playwright",
                return_value=_FakePlaywrightContextManager(
                    launch_calls,
                    [closed_context, Error("persistent profile is already in use")],
                ),
            ),
            redirect_stdout(io.StringIO()),
        ):
            service.start()
            closed_context.close()
            with patch.object(
                closed_context,
                "new_page",
                side_effect=Error("Target page, context or browser has been closed"),
            ):
                self.assertFalse(service.open_page(service.login_url, preserve_current_page=True))
            self.assertEqual(len(launch_calls), 2)
            service.stop()

    def test_queued_open_requests_share_one_closed_context_replacement(self) -> None:
        service = BrowserService(require_login_each_run=False)
        closed_context = _FakeContext()
        replacement_context = _FakeContext()
        launch_calls: list[dict[str, object]] = []
        results: list[bool] = []
        start = threading.Barrier(3)

        with (
            patch("services.browser_service.os.makedirs"),
            patch(
                "services.browser_service.sync_playwright",
                return_value=_FakePlaywrightContextManager(launch_calls, [closed_context, replacement_context]),
            ),
            redirect_stdout(io.StringIO()),
        ):
            service.start()
            closed_context.close()

            def _open() -> None:
                start.wait(timeout=1)
                results.append(service.open_page(service.login_url, preserve_current_page=True))

            with patch.object(
                closed_context,
                "new_page",
                side_effect=Error("Target page, context or browser has been closed"),
            ):
                threads = [threading.Thread(target=_open, daemon=True) for _ in range(2)]
                for thread in threads:
                    thread.start()
                start.wait(timeout=1)
                for thread in threads:
                    thread.join(timeout=2)

            self.assertEqual(results, [True, True])
            self.assertEqual(len(launch_calls), 2)
            self.assertEqual(replacement_context.new_page_calls, 2)
            service.stop()

    def test_worker_loop_force_login_clears_auth_before_single_startup_navigation(self) -> None:
        service = BrowserService(require_login_each_run=True)
        auth_page = _FakePage()
        context = _FakeContext(pages=[auth_page])

        self._run_worker_loop_with_context(service, context)

        self.assertEqual(context.clear_cookie_calls, [".witchform.com", "witchform.com"])
        goto_events = [event for event in context.events if event[0] == "goto"]
        self.assertEqual(len(goto_events), 1)
        clear_indexes = [index for index, event in enumerate(context.events) if event[0] == "clear_cookies"]
        goto_index = next(index for index, event in enumerate(context.events) if event[0] == "goto")
        self.assertTrue(all(index < goto_index for index in clear_indexes))
        self.assertEqual(auth_page.goto_calls[0].get("wait_until"), "domcontentloaded")
        self.assertEqual(len(auth_page.evaluate_calls), 1)

    def test_worker_loop_prints_non_secret_startup_timing_fields(self) -> None:
        service = BrowserService(require_login_each_run=True)
        context = _FakeContext(pages=[_FakePage()])
        launch_calls: list[dict[str, object]] = []
        service._task_queue.put({"action": "quit"})

        with (
            patch("services.browser_service.os.makedirs"),
            patch(
                "services.browser_service.sync_playwright",
                return_value=_FakePlaywrightContextManager(launch_calls, context),
            ),
            redirect_stdout(io.StringIO()) as buffer,
        ):
            service._worker_loop()

        output = buffer.getvalue()
        self.assertIn("BROWSER_STARTUP_TIMING", output)
        for field in ("total_ms", "sync_playwright_ms", "launch_ms", "initial_login_ms", "clear_auth_ms"):
            self.assertRegex(output, rf"{field}=\d+(?:\.\d+)?")
        self.assertNotIn("witchform.com/w/login", output)

    def test_stop_allows_queued_rpc_to_finish_before_worker_exits(self) -> None:
        service = BrowserService(require_login_each_run=False)
        service._is_running = True
        service._accepting_requests = True

        allow_dispatch = threading.Event()
        task_started = threading.Event()

        def _worker() -> None:
            while True:
                task = service._task_queue.get(timeout=1)
                should_quit = task.get("action") == "quit"
                if not should_quit:
                    task_started.set()
                    allow_dispatch.wait(timeout=1)
                service._dispatch_task(task)
                if should_quit:
                    break
            service._finalize_worker_shutdown()

        worker_thread = threading.Thread(target=_worker, daemon=True)
        service._worker_thread = worker_thread
        worker_thread.start()

        error_holder: dict[str, str] = {}

        def _invoke() -> None:
            try:
                service._invoke_rpc({"action": "unknown_task_for_stop_test"}, timeout_sec=1)
            except Exception as exc:
                error_holder["error"] = str(exc)

        rpc_thread = threading.Thread(target=_invoke, daemon=True)
        rpc_thread.start()

        self.assertTrue(task_started.wait(timeout=1.0))

        stop_thread = threading.Thread(target=service.stop, daemon=True)
        stop_thread.start()
        time.sleep(0.05)
        allow_dispatch.set()

        rpc_thread.join(timeout=2)
        stop_thread.join(timeout=2)
        worker_thread.join(timeout=2)

        self.assertFalse(rpc_thread.is_alive())
        self.assertFalse(stop_thread.is_alive())
        self.assertFalse(worker_thread.is_alive())
        self.assertEqual(error_holder.get("error"), "Unknown task action: unknown_task_for_stop_test")
