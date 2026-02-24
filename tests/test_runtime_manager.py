"""TicketRuntimeManager behavior tests."""
from __future__ import annotations

import threading
import time
import unittest

from services.ticket_runtime_manager import TicketRuntimeManager


class _FakeApplication:
    def __init__(self):
        self.listener = None
        self.stop_event = threading.Event()
        self.running_event = threading.Event()
        self.stop_calls = 0
        self.relogin_calls = 0

    def set_status_listener(self, listener):
        self.listener = listener

    def run(self):
        if self.listener:
            self.listener("STARTING", "시작 중")
            self.listener("READY", "준비됨")
        self.running_event.set()
        while not self.stop_event.is_set():
            time.sleep(0.01)
        if self.listener:
            self.listener("STOPPED", "중지됨")

    def request_stop(self):
        self.stop_calls += 1
        self.stop_event.set()

    def request_relogin(self):
        self.relogin_calls += 1
        if self.listener:
            self.listener("RECOVERING", "재로그인")


class _FakeFactory:
    def __init__(self):
        self.instances: list[_FakeApplication] = []

    def __call__(self) -> _FakeApplication:
        app = _FakeApplication()
        self.instances.append(app)
        return app


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class RuntimeManagerTest(unittest.TestCase):
    def test_duplicate_start_only_runs_once(self) -> None:
        factory = _FakeFactory()
        manager = TicketRuntimeManager(app_factory=factory)

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.is_running))
        self.assertFalse(manager.start())
        self.assertEqual(len(factory.instances), 1)

        self.assertTrue(manager.stop())
        self.assertTrue(_wait_until(lambda: manager.state == "IDLE"))

    def test_stop_transitions_to_idle(self) -> None:
        factory = _FakeFactory()
        manager = TicketRuntimeManager(app_factory=factory)
        events: list[str] = []
        manager.subscribe(lambda state, _message, _ts: events.append(state))

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.state in {"RUNNING", "STARTING"}))
        self.assertTrue(manager.stop())
        self.assertTrue(_wait_until(lambda: manager.state == "IDLE"))
        self.assertIn("STOPPING", events)
        self.assertIn("IDLE", events)

    def test_relogin_emits_recovering_when_running(self) -> None:
        factory = _FakeFactory()
        manager = TicketRuntimeManager(app_factory=factory)
        events: list[str] = []
        manager.subscribe(lambda state, _message, _ts: events.append(state))

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.is_running))
        self.assertTrue(manager.relogin())
        self.assertTrue(_wait_until(lambda: "RECOVERING" in events))

        self.assertTrue(manager.stop())


if __name__ == "__main__":
    unittest.main()
