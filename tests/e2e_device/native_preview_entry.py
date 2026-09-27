"""Windows Flet 미리보기 E2E용 작은 창."""
from __future__ import annotations

import base64
import io
import sys
import time
from pathlib import Path

import flet as ft
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from views.dashboard_flet_view import apply_camera_frame_state  # noqa: E402


def _frame(value: int) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (value, value, value)).save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def main(page: ft.Page) -> None:
    page.title = sys.argv[1]
    page.window.width = 260
    page.window.height = 260
    page.window.always_on_top = True
    page.padding = 0
    page.bgcolor = "#000000"
    image = ft.Image(width=200, height=200, visible=False, gapless_playback=True)
    page.add(image)
    frames = (_frame(180), _frame(220))

    def feed() -> None:
        for i in range(200):
            apply_camera_frame_state(image, frames[i % 2], is_web=page.web)
            image.update()
            time.sleep(0.05)

    page.run_thread(feed)


if __name__ == "__main__":
    ft.app(target=main)
