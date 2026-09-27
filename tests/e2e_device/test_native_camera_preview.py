"""Windows Flet 렌더러에서 카메라 프레임이 검게 비지 않는지 확인한다."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from e2e.support import record_metric


def test_native_camera_preview_stays_visible(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows 네이티브 Flet 전용")
    import win32con
    import win32gui
    from PIL import ImageGrab

    title = f"Ticket Camera Native E2E {uuid4().hex}"
    log_path = tmp_path / "native-preview.log"
    entry = Path(__file__).with_name("native_preview_entry.py")
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(entry), title], stdout=log, stderr=subprocess.STDOUT)
        hwnd = 0
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not hwnd:
                hwnd = win32gui.FindWindow(None, title)
                if process.poll() is not None:
                    pytest.fail(f"Flet 창 종료: {log_path.read_text(encoding='utf-8', errors='replace')[-1000:]}")
                time.sleep(0.1)
            assert hwnd, "Windows Flet 테스트 창이 열리지 않음"
            x, y = win32gui.ClientToScreen(hwnd, (0, 0))

            def brightness() -> int:
                with ImageGrab.grab(bbox=(x + 200, y + 150, x + 201, y + 151)) as image:
                    return image.getpixel((0, 0))[0]

            deadline = time.monotonic() + 5
            while brightness() < 150 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert brightness() >= 150, "네이티브 첫 프레임이 표시되지 않음"
            samples = [brightness() for _ in range(80)]
            black_frames = sum(value < 100 for value in samples)
            record_metric("camera_preview_samples", len(samples))
            record_metric("camera_preview_black_frames", black_frames)
            assert black_frames == 0, f"네이티브 프레임 교체 중 검은 화면: {samples}"
            assert sum(160 <= value <= 200 for value in samples) >= 3, f"이전 프레임 정지: {samples}"
            assert sum(200 < value <= 240 for value in samples) >= 3, f"새 프레임 정지: {samples}"
        finally:
            if hwnd and win32gui.IsWindow(hwnd):
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
