"""watchdog의 hang 감지 프리미티브를 실제 Win32 창으로 검증한다.

- 메시지를 펌프하지 않는 창은 5초 규칙으로 '응답 없음'이 된다(IsHungAppWindow TRUE).
- 같은 창에 WM_NULL 핑은 타임아웃한다.
- 별도 펌프 스레드의 창은 응답한다.
"""
from __future__ import annotations

import sys
import threading
import time
import unittest

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows 전용 검증"
)

if sys.platform == "win32":
    import win32api
    import win32con
    import win32gui

    from e2e_packaged.watchdog import (
        ResponsivenessWatchdog,
        is_window_hung,
        probe_window,
        sample_process,
    )


def _create_window(title: str) -> int:
    """호출 스레드 소유의 숨김 창을 만든다."""
    class_name = f"WatchdogTest_{title}"
    wc = win32gui.WNDCLASS()
    wc.lpszClassName = class_name
    wc.lpfnWndProc = {win32con.WM_NULL: lambda h, m, w, l: 0}
    wc.hInstance = win32api.GetModuleHandle(None)
    atom = win32gui.RegisterClass(wc)
    hwnd = win32gui.CreateWindow(
        atom, title, 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
    )
    win32gui.UpdateWindow(hwnd)
    return hwnd


@unittest.skipUnless(sys.platform == "win32", "Windows 전용")
class WatchdogPrimitiveTest(unittest.TestCase):
    def test_detects_unpumped_window_as_hung(self) -> None:
        hwnd = _create_window("hung")
        try:
            # 생성 직후에는 기동 처리로 간주돼 응답으로 볼 수 있다.
            # 펌프 없이 6초 경과하면 OS가 '응답 없음'으로 표시한다.
            deadline = time.monotonic() + 8.0
            while not is_window_hung(hwnd) and time.monotonic() < deadline:
                time.sleep(0.25)
            self.assertTrue(is_window_hung(hwnd), "미펌프 창이 hung으로 표시되지 않음")
            # 같은 스레드에서 보내는 SendMessage는 큐를 우회하므로
            # 다른 스레드에서 핑해야 실제 watchdog 조건과 동일해진다.
            probe_result: list[bool] = []
            probe_thread = threading.Thread(
                target=lambda: probe_result.append(probe_window(hwnd, 500))
            )
            probe_thread.start()
            probe_thread.join(timeout=5)
            self.assertTrue(probe_result, "핑 스레드가 반환하지 않음")
            self.assertFalse(probe_result[0], "미펌프 창에 WM_NULL 핑이 통과")
        finally:
            win32gui.DestroyWindow(hwnd)

    def test_probe_passes_for_pumped_window(self) -> None:
        hwnd_holder: list[int] = []
        stop = threading.Event()

        def _window_thread() -> None:
            hwnd = _create_window("pumped")
            hwnd_holder.append(hwnd)
            while not stop.is_set():
                win32gui.PumpWaitingMessages()
                time.sleep(0.02)
            win32gui.DestroyWindow(hwnd)

        thread = threading.Thread(target=_window_thread, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5.0
            while not hwnd_holder and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(hwnd_holder, "테스트 창 생성 실패")
            hwnd = hwnd_holder[0]
            time.sleep(0.5)
            self.assertTrue(probe_window(hwnd, 1000), "펌프 중인 창에 핑 실패")
            self.assertFalse(is_window_hung(hwnd), "펌프 중인 창이 hung으로 표시됨")
        finally:
            stop.set()
            thread.join(timeout=5)

    def test_sample_process_reports_current_process(self) -> None:
        import os

        sample = sample_process(os.getpid())
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertGreater(sample.rss_bytes, 0)
        self.assertGreater(sample.handle_count, 0)

    def test_watchdog_records_hung_event(self) -> None:
        import os

        hwnd = _create_window("watch")
        try:
            dog = ResponsivenessWatchdog(
                os.getpid(), hwnd=hwnd, interval_sec=0.2, probe_timeout_ms=300
            )
            dog.start()
            # 창 미펌프 상태로 감시 — 5초 규칙으로 hung 이벤트가 누적돼야 한다.
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline and dog.report.healthy:
                time.sleep(0.3)
            report = dog.stop()
            self.assertFalse(report.healthy, "watchdog이 hang을 감지하지 못함")
            self.assertTrue(
                any(e.kind == "hung" for e in report.events),
                f"hung 이벤트 없음: {report.summary_lines()}",
            )
        finally:
            win32gui.DestroyWindow(hwnd)
            dog.stop()

    def test_watchdog_healthy_for_pumped_window(self) -> None:
        import os

        hwnd_holder: list[int] = []
        stop = threading.Event()

        def _window_thread() -> None:
            hwnd = _create_window("pumped2")
            hwnd_holder.append(hwnd)
            while not stop.is_set():
                win32gui.PumpWaitingMessages()
                time.sleep(0.02)
            win32gui.DestroyWindow(hwnd)

        thread = threading.Thread(target=_window_thread, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5.0
            while not hwnd_holder and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(hwnd_holder)
            dog = ResponsivenessWatchdog(
                os.getpid(), hwnd=hwnd_holder[0], interval_sec=0.2, probe_timeout_ms=300
            )
            dog.start()
            time.sleep(3.0)
            report = dog.stop()
            self.assertTrue(
                report.healthy, f"정상 창에서 이벤트 발생: {report.summary_lines()}"
            )
            self.assertGreater(len(report.samples), 0)
        finally:
            stop.set()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
