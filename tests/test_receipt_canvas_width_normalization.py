from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.receipt_canvas_store import ReceiptCanvasStore


class ReceiptCanvasWidthNormalizationTest(unittest.TestCase):
    def test_load_layout_normalizes_stale_80_width_elements_for_58_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "layout_58.json"
            path.write_text(
                (
                    '{"version":1,"meta":{"name":"X","paper_width":"58","canvas_width_px":384,"canvas_height_px":900},'
                    '"elements":['
                    '{"id":"txt_1","type":"text","x":0,"y":0,"w":576,"h":40,"text_template":"x"},'
                    '{"id":"txt_2","type":"text","x":192,"y":50,"w":384,"h":40,"text_template":"y"}'
                    ']}'
                ),
                encoding="utf-8",
            )
            store = ReceiptCanvasStore(
                default_layout_path=str(path),
                asset_dir=str(Path(temp_dir) / "assets"),
            )

            loaded = store.load_layout(str(path))

            self.assertEqual(loaded.meta.paper_width, "58")
            self.assertEqual(loaded.meta.canvas_width_px, 384)
            self.assertTrue(all(element.w <= 384 for element in loaded.elements))
            self.assertTrue(all(element.x + element.w <= 384 for element in loaded.elements))


if __name__ == "__main__":
    unittest.main()
