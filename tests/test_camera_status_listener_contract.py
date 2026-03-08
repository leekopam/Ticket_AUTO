"""Camera status listener forwarding tests."""
from __future__ import annotations

import threading
import time
import unittest

from services.ticket_runtime_manager import TicketRuntimeManager


class _FakeCameraStatusApplication:
    def __init__(self):
        self.listener = None
        self.camera_status_listener = None
        self.stop_event = threading.Event()
        self.running_event = threading.Event()

    def set_status_listener(self, listener):
        self.listener = listener

    def set_camera_status_listener(self, listener):
        self.camera_status_listener = listener

    def run(self):
        self.running_event.set()
        while not self.stop_event.is_set():
            time.sleep(0.01)

    def request_stop(self):
        self.stop_event.set()


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class CameraStatusListenerContractTest(unittest.TestCase):
    def test_runtime_manager_forwards_camera_status_listener_to_application(self) -> None:
        app_instances: list[_FakeCameraStatusApplication] = []

        def factory():
            app = _FakeCameraStatusApplication()
            app_instances.append(app)
            return app

        manager = TicketRuntimeManager(app_factory=factory)
        received: list[str | None] = []
        manager.set_camera_status_listener(lambda message: received.append(message))

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: bool(app_instances and app_instances[0].running_event.is_set())))
        self.assertIsNotNone(app_instances[0].camera_status_listener)

        app_instances[0].camera_status_listener("초점 보정 중")
        self.assertEqual(received, ["초점 보정 중"])

        self.assertTrue(manager.stop())


if __name__ == "__main__":
    unittest.main()
