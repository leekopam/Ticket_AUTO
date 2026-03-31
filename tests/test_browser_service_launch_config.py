from __future__ import annotations

import unittest
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
        service = BrowserService(
            user_data_dir=".runtime/test-profile",
            require_login_each_run=False,
        )

        launch_calls, context = self._run_worker_loop_with_service(service)

        self.assertEqual(len(launch_calls), 1)
        self.assertEqual(launch_calls[0]["user_data_dir"], ".runtime/test-profile")
        self.assertFalse(launch_calls[0]["headless"])
        self.assertTrue(context.closed)

    def test_worker_loop_uses_headless_mode_when_configured(self) -> None:
        service = BrowserService(
            user_data_dir=".runtime/test-profile",
            require_login_each_run=False,
            headless=True,
        )

        launch_calls, context = self._run_worker_loop_with_service(service)

        self.assertEqual(len(launch_calls), 1)
        self.assertTrue(launch_calls[0]["headless"])
        self.assertTrue(context.closed)
