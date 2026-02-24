"""Pure helpers for canvas editor state and coordinate conversion."""
from __future__ import annotations

from models.receipt_canvas_model import ReceiptCanvasElement


def clamp_element_position(
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    canvas_w: int,
    canvas_h: int,
) -> tuple[int, int]:
    max_x = max(0, canvas_w - max(1, w))
    max_y = max(0, canvas_h - max(1, h))
    return max(0, min(int(x), max_x)), max(0, min(int(y), max_y))


def real_to_preview(value: float, *, real_width: int, preview_width: int) -> int:
    if real_width <= 0:
        return int(value)
    scale = preview_width / real_width
    return int(round(value * scale))


def preview_to_real(value: float, *, real_width: int, preview_width: int) -> int:
    if preview_width <= 0:
        return int(value)
    scale = real_width / preview_width
    return int(round(value * scale))


def remove_element_by_id(
    elements: list[ReceiptCanvasElement],
    element_id: str,
) -> list[ReceiptCanvasElement]:
    return [element for element in elements if element.id != element_id]


def update_element_in_list(
    elements: list[ReceiptCanvasElement],
    updated: ReceiptCanvasElement,
) -> list[ReceiptCanvasElement]:
    return [updated if element.id == updated.id else element for element in elements]
