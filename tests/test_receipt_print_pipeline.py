"""Receipt print pipeline template branching tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from models.order_model import Order
from models.receipt_canvas_model import (
    ReceiptCanvasDocument,
    ReceiptCanvasElement,
    ReceiptCanvasMeta,
)
from models.receipt_settings_model import ReceiptSettings
from services.receipt_canvas_store import ReceiptCanvasStore
from services.receipt_print_pipeline import render_order_receipt


def _dummy_order() -> Order:
    return Order(
        order_number="ORDER-001",
        name="홍길동",
        phone="010-0000-0000",
        seat="A-1",
        goods=["굿즈 x1"],
        url="https://example.com/order/1",
    )


class ReceiptPrintPipelineTest(unittest.TestCase):
    def test_json_template_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout_path = Path(temp_dir) / "layout.json"
            store = ReceiptCanvasStore(default_layout_path=str(layout_path), asset_dir=str(Path(temp_dir) / "assets"))
            doc = ReceiptCanvasDocument(
                version=1,
                meta=ReceiptCanvasMeta(
                    name="JSON",
                    paper_width="80",
                    canvas_width_px=576,
                    canvas_height_px=280,
                ),
                elements=[
                    ReceiptCanvasElement(
                        id="txt_1",
                        type="text",
                        x=20,
                        y=20,
                        w=260,
                        h=60,
                        text_template="주문번호: {{order_number}}",
                    )
                ],
            )
            store.save_layout(str(layout_path), doc)

            settings = ReceiptSettings(template_path=str(layout_path), paper_width="80")
            rendered = render_order_receipt(_dummy_order(), settings)

            self.assertEqual(rendered.image.width, 576)
            self.assertEqual(rendered.context["order_number"], "ORDER-001")

    def test_legacy_tpl_template_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tpl_path = Path(temp_dir) / "layout.tpl"
            tpl_path.write_text("[[CENTER]]{{order_number}}", encoding="utf-8")

            settings = ReceiptSettings(template_path=str(tpl_path), paper_width="58")
            rendered = render_order_receipt(_dummy_order(), settings)

            self.assertEqual(rendered.image.width, 384)
            self.assertEqual(rendered.context["order_number"], "ORDER-001")


if __name__ == "__main__":
    unittest.main()
