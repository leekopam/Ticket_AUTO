"""Canvas editor state helper contract tests."""
from __future__ import annotations

import unittest

from models.receipt_canvas_model import ReceiptCanvasElement
from services.receipt_canvas_editor_state import (
    clamp_element_position,
    preview_to_real,
    real_to_preview,
    remove_element_by_id,
    update_element_in_list,
)


class SettingsCanvasContractTest(unittest.TestCase):
    def test_clamp_element_position_keeps_element_inside_canvas(self) -> None:
        x, y = clamp_element_position(
            x=600,
            y=1000,
            w=120,
            h=80,
            canvas_w=576,
            canvas_h=900,
        )
        self.assertEqual(x, 456)
        self.assertEqual(y, 820)

    def test_preview_real_coordinate_conversion(self) -> None:
        real = preview_to_real(42, real_width=576, preview_width=420)
        preview = real_to_preview(real, real_width=576, preview_width=420)
        self.assertGreaterEqual(preview, 41)
        self.assertLessEqual(preview, 43)

    def test_update_and_remove_element_reducer_helpers(self) -> None:
        original = [
            ReceiptCanvasElement(id="a", type="text", x=0, y=0, w=100, h=30),
            ReceiptCanvasElement(id="b", type="qr", x=10, y=10, w=80, h=80),
        ]
        updated = update_element_in_list(
            original,
            ReceiptCanvasElement(id="b", type="qr", x=20, y=30, w=80, h=80),
        )
        self.assertEqual(updated[1].x, 20)
        self.assertEqual(updated[1].y, 30)

        removed = remove_element_by_id(updated, "a")
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0].id, "b")


if __name__ == "__main__":
    unittest.main()
