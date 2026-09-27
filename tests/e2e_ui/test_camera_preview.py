"""실제 Flet 이미지 렌더링의 프레임 교체 회귀 검사."""
from __future__ import annotations

import base64
import io
import threading
import time

from PIL import Image

from e2e.support import record_metric
from e2e_ui.support import send_control_command, wait_for_button


def _frame(value: int, *, format: str = "JPEG") -> str:
    image = Image.new("RGB", (640, 480), (value, value, value))
    output = io.BytesIO()
    image.save(output, format=format, quality=70)
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_camera_preview_stays_visible_between_frames(page, flet_server) -> None:
    page.get_by_role("button", name="티켓 확인 시작", exact=True).click()
    wait_for_button(page, "중지", timeout_ms=20000)
    control_url = flet_server["control_url"]
    frames = [_frame(180), _frame(220)]
    send_control_command(control_url, {"cmd": "emit_frame", "png_b64": frames[0]})
    camera = page.get_by_label("카메라 미리보기")
    camera.wait_for(state="visible", timeout=20000)
    box = camera.bounding_box()
    assert box is not None
    x = int(box["x"] + box["width"] / 2)
    y = int(box["y"] + box["height"] / 2)

    def brightness() -> int:
        with Image.open(io.BytesIO(page.screenshot())) as shot:
            return sum(shot.getpixel((x, y))[:3]) // 3

    deadline = time.monotonic() + 5
    while brightness() < 100 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert brightness() >= 100, "첫 프레임이 표시되지 않음"

    stop = threading.Event()

    def feed() -> None:
        i = 0
        while not stop.is_set():
            send_control_command(control_url, {"cmd": "emit_frame", "png_b64": frames[i % 2]})
            i += 1
            time.sleep(0.05)

    feeder = threading.Thread(target=feed, daemon=True)
    feeder.start()
    try:
        samples = [brightness() for _ in range(60)]
    finally:
        stop.set()
        feeder.join(timeout=2)
    black_frames = sum(value < 100 for value in samples)
    record_metric("camera_preview_samples", len(samples))
    record_metric("camera_preview_black_frames", black_frames)
    assert black_frames == 0, f"프레임 교체 중 검은 화면 감지: {samples}"
    assert sum(160 <= value <= 200 for value in samples) >= 3, f"이전 프레임이 표시되지 않음: {samples}"
    assert sum(200 < value <= 240 for value in samples) >= 3, f"새 프레임이 표시되지 않음: {samples}"

    # 수동 데모는 PNG 프레임도 보내므로 기존 경로가 유지되는지 확인한다.
    send_control_command(control_url, {"cmd": "emit_frame", "png_b64": _frame(128, format="PNG")})
    deadline = time.monotonic() + 5
    while not 115 <= brightness() <= 145 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert 115 <= brightness() <= 145, "데모 PNG 프레임이 표시되지 않음"
