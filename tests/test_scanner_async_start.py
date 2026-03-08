"""ScannerView 비동기 카메라 초기화 및 Application._wait_for_camera 테스트."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from views.scanner_view import ScannerView


class _FakeCapture:
    """테스트용 가짜 VideoCapture."""

    def __init__(self, *, opened: bool = True, delay: float = 0.0):
        self._opened = opened
        self._delay = delay
        self.released = False
        self.set_calls: list[tuple[int, float]] = []

    def isOpened(self) -> bool:  # noqa: N802
        return self._opened

    def read(self) -> tuple[bool, np.ndarray | None]:
        frame = np.zeros((12, 12, 3), dtype=np.uint8)
        return True, frame

    def release(self) -> None:
        self.released = True

    def set(self, prop: int, value: float) -> bool:
        self.set_calls.append((prop, value))
        return True


class TestScannerAsyncStart(unittest.TestCase):
    """ScannerView.start()의 비동기 카메라 초기화를 검증한다."""

    def test_start_returns_immediately_without_blocking(self):
        """start() 호출 시 카메라 open을 기다리지 않고 즉시 반환한다."""
        scanner = ScannerView()

        # 카메라 open에 2초 걸리는 상황을 시뮬레이션
        original_open = ScannerView._open_camera_with_fallback

        def slow_open(camera_index):
            time.sleep(2.0)
            return _FakeCapture(), "DEFAULT"

        start_time = time.monotonic()
        with patch.object(ScannerView, "_open_camera_with_fallback", side_effect=slow_open):
            scanner.start()
        elapsed = time.monotonic() - start_time

        # start()는 0.5초 이내에 반환되어야 함 (블로킹 없음)
        self.assertTrue(elapsed < 0.5, f"start()가 {elapsed:.2f}초 소요 - 블로킹 발생")
        self.assertTrue(scanner.is_running())
        scanner.release()

    def test_is_camera_ready_false_initially_then_true_after_connection(self):
        """start() 직후 is_camera_ready()는 False, 카메라 연결 후 True로 전환된다."""
        fake_cap = _FakeCapture()
        connect_event = threading.Event()

        def delayed_open(camera_index):
            connect_event.wait(timeout=5)
            return fake_cap, "DEFAULT"

        scanner = ScannerView()
        with patch.object(ScannerView, "_open_camera_with_fallback", side_effect=delayed_open):
            scanner.start()

            # 연결 전에는 카메라가 준비되지 않음
            self.assertFalse(scanner.is_camera_ready())

            # 카메라 연결 신호 전송
            connect_event.set()
            # 캡처 루프가 연결을 처리할 시간 부여
            time.sleep(1.0)

            self.assertTrue(scanner.is_camera_ready())

        scanner.release()

    def test_start_sets_reconnecting_status(self):
        """start() 호출 시 '카메라 재연결 중' 상태를 설정한다."""
        scanner = ScannerView()
        status_messages: list[str | None] = []

        def on_status(msg: str | None) -> None:
            status_messages.append(msg)

        scanner.set_camera_status_listener(on_status)

        def stalling_open(camera_index):
            time.sleep(10)
            return None, None

        with patch.object(ScannerView, "_open_camera_with_fallback", side_effect=stalling_open):
            scanner.start()
            # start() 직후 '카메라 재연결 중' 상태가 리스너에 전달됨
            self.assertTrue(
                any("재연결" in (m or "") for m in status_messages),
                f"재연결 상태 메시지가 없음: {status_messages}",
            )
        scanner.release()


class TestWaitForCamera(unittest.TestCase):
    """Application._wait_for_camera() 타임아웃 동작을 검증한다."""

    def test_wait_for_camera_returns_true_when_camera_becomes_ready(self):
        """카메라가 대기 시간 내에 준비되면 True를 반환한다."""
        from main import Application

        app = Application.__new__(Application)
        app._stop_requested = False

        # is_camera_ready()가 0.3초 후 True를 반환하도록 mock
        ready_time = time.monotonic() + 0.3
        mock_scanner = type("MockScanner", (), {
            "is_camera_ready": lambda self: time.monotonic() >= ready_time,
        })()
        app._scanner_view = mock_scanner

        result = app._wait_for_camera(timeout_sec=5)
        self.assertTrue(result)

    def test_wait_for_camera_returns_false_on_timeout(self):
        """타임아웃 내에 카메라가 준비되지 않으면 False를 반환한다."""
        from main import Application

        app = Application.__new__(Application)
        app._stop_requested = False

        mock_scanner = type("MockScanner", (), {
            "is_camera_ready": lambda self: False,
        })()
        app._scanner_view = mock_scanner

        start = time.monotonic()
        result = app._wait_for_camera(timeout_sec=1)
        elapsed = time.monotonic() - start

        self.assertFalse(result)
        self.assertGreaterEqual(elapsed, 0.9)

    def test_wait_for_camera_returns_false_on_stop_request(self):
        """stop 요청 시 즉시 False를 반환한다."""
        from main import Application

        app = Application.__new__(Application)
        app._stop_requested = False

        mock_scanner = type("MockScanner", (), {
            "is_camera_ready": lambda self: False,
        })()
        app._scanner_view = mock_scanner

        # 0.3초 후 stop 요청 전송
        def request_stop():
            time.sleep(0.3)
            app._stop_requested = True

        threading.Thread(target=request_stop, daemon=True).start()

        start = time.monotonic()
        result = app._wait_for_camera(timeout_sec=10)
        elapsed = time.monotonic() - start

        self.assertFalse(result)
        # stop 요청 후 최대 0.5초(폴링 간격) 내에 반환
        self.assertLess(elapsed, 1.5)


if __name__ == "__main__":
    unittest.main()
