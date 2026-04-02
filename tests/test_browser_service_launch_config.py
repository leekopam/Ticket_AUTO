from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.browser_service import BrowserService


class _FakeContext:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakePlaywrightContextManager:
    def __init__(self, launch_calls: list[dict[str, object]], context: _FakeContext) -> None:
        self._launch_calls = launch_calls
        self._context = context

    def __enter__(self) -> SimpleNamespace:
        def _launch_persistent_context(**kwargs: object) -> _FakeContext:
            self._launch_calls.append(kwargs)
            return self._context

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
