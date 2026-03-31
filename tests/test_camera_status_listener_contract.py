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


class _FailingListenerApplication(_FakeCameraStatusApplication):
    def set_camera_frame_listener(self, listener):
        raise RuntimeError("frame listener boom")

    def set_camera_status_listener(self, listener):
        raise RuntimeError("camera status listener boom")

    def set_order_listener(self, listener):
        raise RuntimeError("order listener boom")


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

    def test_runtime_manager_logs_listener_forwarding_failures_without_raising(self) -> None:
        app_instances: list[_FailingListenerApplication] = []

        def factory():
            app = _FailingListenerApplication()
            app_instances.append(app)
            return app

        manager = TicketRuntimeManager(app_factory=factory)

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: bool(app_instances and app_instances[0].running_event.is_set())))

        with self.assertLogs("services.ticket_runtime_manager", level="WARNING") as captured:
            manager.set_camera_frame_listener(lambda _frame: None)
            manager.set_camera_status_listener(lambda _message: None)
            manager.set_order_listener(lambda _order: None)

        self.assertTrue(any("카메라 프레임 리스너 전달 실패" in line for line in captured.output))
        self.assertTrue(any("카메라 상태 리스너 전달 실패" in line for line in captured.output))
        self.assertTrue(any("주문 리스너 전달 실패" in line for line in captured.output))
        self.assertTrue(manager.stop())


if __name__ == "__main__":
    unittest.main()
