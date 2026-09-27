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
from views.dashboard_flet_view import build_camera_preview_image, dispatch_camera_frame_update  # noqa: E402


def _frame(value: int) -> str:
    buffer = io.BytesIO()
    image = Image.effect_noise((640, 480), 100).convert("RGB")
    image.paste((value, value, value), (230, 150, 410, 330))
    image.save(buffer, format="JPEG", quality=70)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def main(page: ft.Page) -> None:
    page.title = sys.argv[1]
    page.window.width = 460
    page.window.height = 360
    page.window.always_on_top = True
    page.padding = 0
    page.bgcolor = "#000000"
    image = build_camera_preview_image()
    page.add(image)
    frames = (_frame(180), _frame(220))

    def feed() -> None:
        for i in range(200):
            dispatch_camera_frame_update(page, image, frames[i % 2])
            time.sleep(0.05)

    page.run_thread(feed)


if __name__ == "__main__":
    ft.app(target=main)
