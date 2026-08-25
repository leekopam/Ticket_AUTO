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
        self.focus_apply_calls: list[tuple[str, float | None]] = []
        self.camera_settings_open_calls = 0

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


    def apply_scanner_focus_settings(self, focus_mode: str, manual_focus_value: float | None) -> str:
        self.focus_apply_calls.append((focus_mode, manual_focus_value))
        return "카메라 초점 설정 저장 완료 (현재 런타임에 바로 적용됨)"

    def open_scanner_camera_settings(self) -> bool:
        self.camera_settings_open_calls += 1
        return True


class _FakeFactory:
    def __init__(self):
        self.instances: list[_FakeApplication] = []

    def __call__(self) -> _FakeApplication:
        app = _FakeApplication()
        self.instances.append(app)
        return app


class _FailingFactory:
    def __call__(self):
        raise RuntimeError("factory boom")


class _ExplodingListenerApplication:
    def set_camera_frame_listener(self, _listener) -> None:
        raise RuntimeError("frame listener boom")

    def set_camera_status_listener(self, _listener) -> None:
        raise RuntimeError("camera status boom")

    def set_order_listener(self, _listener) -> None:
        raise RuntimeError("order listener boom")


class _OptionalListenerExplodingApplication(_FakeApplication):
    def set_camera_frame_listener(self, _listener) -> None:
        raise RuntimeError("frame listener boom")

    def set_camera_status_listener(self, _listener) -> None:
        raise RuntimeError("camera status boom")

    def set_order_listener(self, _listener) -> None:
        raise RuntimeError("order listener boom")


class _RequestStopExplodingApplication(_FakeApplication):
    def request_stop(self):
        self.stop_calls += 1
        raise RuntimeError("stop boom")


class _RequestStopSignalsThenExplodesApplication(_FakeApplication):
    def request_stop(self):
        self.stop_calls += 1
        self.stop_event.set()
        raise RuntimeError("stop boom after signal")


class _RecoverableErrorApplication(_FakeApplication):
    def run(self):
        if self.listener:
            self.listener("STARTING", "시작 중")
            self.listener("READY", "준비됨")
        self.running_event.set()
        if self.listener:
            self.listener("ERROR", "주문번호 조회 권한을 확인할 수 없습니다")
        while not self.stop_event.is_set():
            time.sleep(0.01)
        if self.listener:
            self.listener("STOPPED", "중지됨")


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

    def test_recoverable_app_error_keeps_live_runtime_stop_capable(self) -> None:
        app = _RecoverableErrorApplication()
        manager = TicketRuntimeManager(app_factory=lambda: app)
        events: list[tuple[str, str]] = []
        manager.subscribe(lambda state, message, _ts: events.append((state, message)))

        self.assertTrue(manager.start())
        self.assertTrue(
            _wait_until(
                lambda: ("RUNNING", "주문번호 조회 권한을 확인할 수 없습니다") in events
            )
        )

        self.assertTrue(manager.is_running)
        self.assertEqual(manager.state, "RUNNING")
        self.assertNotIn("ERROR", [state for state, _message in events])

        self.assertTrue(manager.stop())

    def test_failing_subscriber_is_logged_without_blocking_other_subscribers(self) -> None:
        factory = _FakeFactory()
        manager = TicketRuntimeManager(app_factory=factory)
        events: list[str] = []

        def _broken_callback(_state: str, _message: str, _timestamp: str) -> None:
            raise RuntimeError("callback boom")

        with self.assertLogs("services.ticket_runtime_manager", level="WARNING") as captured:
            manager.subscribe(_broken_callback)
            manager.subscribe(lambda state, _message, _ts: events.append(state))
            self.assertTrue(manager.start())
            self.assertTrue(_wait_until(lambda: manager.state in {"RUNNING", "STARTING"}))
            self.assertTrue(manager.stop())
            self.assertTrue(_wait_until(lambda: manager.state == "IDLE"))

        self.assertIn("IDLE", events)
        self.assertIn("RUNNING", events)
        self.assertTrue(
            any("상태 구독 초기 콜백 처리 실패" in line for line in captured.output)
        )
        self.assertTrue(any("상태 콜백 처리 실패" in line for line in captured.output))

    def test_unsubscribe_stops_future_runtime_events_for_callback(self) -> None:
        factory = _FakeFactory()
        manager = TicketRuntimeManager(app_factory=factory)
        events: list[str] = []

        def _callback(state: str, _message: str, _timestamp: str) -> None:
            events.append(state)

        manager.subscribe(_callback)
        manager.unsubscribe(_callback)

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.state in {"RUNNING", "STARTING"}))
        self.assertTrue(manager.stop())
        self.assertTrue(_wait_until(lambda: manager.state == "IDLE"))

        self.assertEqual(events, ["IDLE"])

    def test_duplicate_subscribe_does_not_duplicate_runtime_events(self) -> None:
        duplicate_manager = TicketRuntimeManager(app_factory=_FakeFactory())
        single_manager = TicketRuntimeManager(app_factory=_FakeFactory())
        duplicate_events: list[str] = []
        single_events: list[str] = []

        def _callback(state: str, _message: str, _timestamp: str) -> None:
            duplicate_events.append(state)

        duplicate_manager.subscribe(_callback)
        duplicate_manager.subscribe(_callback)
        single_manager.subscribe(lambda state, _message, _timestamp: single_events.append(state))

        self.assertTrue(duplicate_manager.start())
        self.assertTrue(_wait_until(lambda: duplicate_manager.state in {"RUNNING", "STARTING"}))
        self.assertTrue(duplicate_manager.stop())
        self.assertTrue(_wait_until(lambda: duplicate_manager.state == "IDLE"))

        self.assertTrue(single_manager.start())
        self.assertTrue(_wait_until(lambda: single_manager.state in {"RUNNING", "STARTING"}))
        self.assertTrue(single_manager.stop())
        self.assertTrue(_wait_until(lambda: single_manager.state == "IDLE"))

        self.assertEqual(duplicate_events, single_events)

    def test_start_failure_returns_false_and_enters_error_state(self) -> None:
        manager = TicketRuntimeManager(app_factory=_FailingFactory())
        events: list[tuple[str, str]] = []
        manager.subscribe(lambda state, message, _ts: events.append((state, message)))

        with self.assertLogs("services.ticket_runtime_manager", level="ERROR") as captured:
            self.assertFalse(manager.start())

        self.assertEqual(manager.state, "ERROR")
        self.assertFalse(manager.is_running)
        self.assertTrue(any(state == "ERROR" for state, _message in events))
        self.assertTrue(any("런타임 시작 실패: factory boom" in message for _state, message in events))
        self.assertTrue(any("런타임 초기화 실패" in line for line in captured.output))

    def test_stop_request_failure_without_signal_returns_false_and_enters_error_state(self) -> None:
        app = _RequestStopExplodingApplication()
        manager = TicketRuntimeManager(app_factory=lambda: app)
        events: list[tuple[str, str]] = []
        manager.subscribe(lambda state, message, _ts: events.append((state, message)))

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.is_running))

        with self.assertLogs("services.ticket_runtime_manager", level="ERROR") as captured:
            self.assertFalse(manager.stop(timeout_sec=0.1))

        self.assertEqual(manager.state, "ERROR")
        self.assertTrue(manager.is_running)
        self.assertTrue(any(state == "ERROR" for state, _message in events))
        self.assertTrue(any("stop boom" in message for _state, message in events))
        self.assertTrue(any("stop boom" in line for line in captured.output))

        app.stop_event.set()
        self.assertTrue(_wait_until(lambda: not manager.is_running))

    def test_stop_request_failure_after_signal_still_transitions_to_idle(self) -> None:
        app = _RequestStopSignalsThenExplodesApplication()
        manager = TicketRuntimeManager(app_factory=lambda: app)
        events: list[str] = []
        manager.subscribe(lambda state, _message, _ts: events.append(state))

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.is_running))

        with self.assertLogs("services.ticket_runtime_manager", level="ERROR") as captured:
            self.assertTrue(manager.stop(timeout_sec=0.1))

        self.assertFalse(manager.is_running)
        self.assertEqual(manager.state, "IDLE")
        self.assertIn("IDLE", events)
        self.assertNotIn("ERROR", events)
        self.assertTrue(any("stop boom after signal" in line for line in captured.output))

    def test_listener_forwarding_failures_are_logged_without_raising(self) -> None:
        manager = TicketRuntimeManager(app_factory=_FakeFactory())
        manager._app = _ExplodingListenerApplication()

        with self.assertLogs("services.ticket_runtime_manager", level="WARNING") as captured:
            manager.set_camera_frame_listener(lambda _frame: None)
            manager.set_camera_status_listener(lambda _message: None)
            manager.set_order_listener(lambda _order: None)

        self.assertTrue(any("카메라 프레임 리스너 전달 실패" in line for line in captured.output))
        self.assertTrue(any("카메라 상태 리스너 전달 실패" in line for line in captured.output))
        self.assertTrue(any("주문 리스너 전달 실패" in line for line in captured.output))

    def test_start_continues_when_optional_listener_attachment_fails(self) -> None:
        app = _OptionalListenerExplodingApplication()
        manager = TicketRuntimeManager(app_factory=lambda: app)
        events: list[str] = []
        manager.subscribe(lambda state, _message, _ts: events.append(state))
        manager.set_camera_frame_listener(lambda _frame: None)
        manager.set_camera_status_listener(lambda _message: None)
        manager.set_order_listener(lambda _order: None)

        with self.assertLogs("services.ticket_runtime_manager", level="WARNING") as captured:
            self.assertTrue(manager.start())
            self.assertTrue(_wait_until(lambda: manager.state in {"RUNNING", "STARTING"}))
            self.assertTrue(manager.stop())
            self.assertTrue(_wait_until(lambda: manager.state == "IDLE"))

        self.assertIn("RUNNING", events)
        self.assertIn("IDLE", events)
        self.assertTrue(any("카메라 프레임 리스너 전달 실패" in line for line in captured.output))
        self.assertTrue(any("카메라 상태 리스너 전달 실패" in line for line in captured.output))
        self.assertTrue(any("주문 리스너 전달 실패" in line for line in captured.output))

    def test_stop_request_failure_is_reported_without_raising(self) -> None:
        app = _RequestStopExplodingApplication()
        manager = TicketRuntimeManager(app_factory=lambda: app)

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.is_running))

        with self.assertLogs("services.ticket_runtime_manager", level="ERROR") as captured:
            self.assertFalse(manager.stop(timeout_sec=0.1))

        self.assertEqual(app.stop_calls, 1)
        self.assertEqual(manager.state, "ERROR")
        self.assertTrue(any("런타임 중지 요청 실패" in line for line in captured.output))

        app.stop_event.set()
        self.assertTrue(_wait_until(lambda: not manager.is_running))

    def test_stop_request_failure_can_still_finish_when_worker_exits(self) -> None:
        app = _RequestStopSignalsThenExplodesApplication()
        manager = TicketRuntimeManager(app_factory=lambda: app)

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.is_running))

        with self.assertLogs("services.ticket_runtime_manager", level="ERROR") as captured:
            self.assertTrue(manager.stop(timeout_sec=1.0))

        self.assertEqual(app.stop_calls, 1)
        self.assertEqual(manager.state, "IDLE")
        self.assertTrue(any("런타임 중지 요청 실패" in line for line in captured.output))


    def test_apply_scanner_focus_settings_forwards_to_running_app(self) -> None:
        app = _FakeApplication()
        manager = TicketRuntimeManager(app_factory=lambda: app)

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.is_running))

        result = manager.apply_scanner_focus_settings("manual", 8.5)

        self.assertEqual(result, "카메라 초점 설정 저장 완료 (현재 런타임에 바로 적용됨)")
        self.assertEqual(app.focus_apply_calls, [("manual", 8.5)])
        self.assertTrue(manager.stop())

    def test_apply_scanner_focus_settings_returns_deferred_message_when_idle(self) -> None:
        manager = TicketRuntimeManager(app_factory=_FakeFactory())

        result = manager.apply_scanner_focus_settings("manual", 8.5)

        self.assertEqual(result, "카메라 초점 설정 저장 완료 (다음 앱 시작 후 적용)")


    def test_get_scanner_focus_capability_forwards_to_running_app(self) -> None:
        app = _FakeApplication()
        capability = object()
        app.get_scanner_focus_capability = lambda: capability
        manager = TicketRuntimeManager(app_factory=lambda: app)

        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.is_running))

        self.assertIs(manager.get_scanner_focus_capability(), capability)
        self.assertTrue(manager.stop())

    def test_get_scanner_focus_capability_returns_none_when_idle(self) -> None:
        manager = TicketRuntimeManager(app_factory=_FakeFactory())
        self.assertIsNone(manager.get_scanner_focus_capability())

    def test_open_scanner_camera_settings_forwards_to_running_app(self) -> None:
        app = _FakeApplication()
        manager = TicketRuntimeManager(app_factory=lambda: app)
        self.assertTrue(manager.start())
        self.assertTrue(_wait_until(lambda: manager.is_running))

        self.assertTrue(manager.open_scanner_camera_settings())

        self.assertEqual(app.camera_settings_open_calls, 1)
        self.assertTrue(manager.stop())

    def test_open_scanner_camera_settings_returns_false_when_idle(self) -> None:
        manager = TicketRuntimeManager(app_factory=_FakeFactory())

        self.assertFalse(manager.open_scanner_camera_settings())


if __name__ == "__main__":
    unittest.main()
