"""Canvas renderer tests."""
from __future__ import annotations

import unittest

from PIL import ImageChops

from models.receipt_canvas_model import (
    ReceiptCanvasDocument,
    ReceiptCanvasElement,
    ReceiptCanvasMeta,
)
from services.receipt_canvas_renderer import render_canvas_layout


def _non_white_bbox(img):
    background = img.copy()
    background.paste((255, 255, 255), [0, 0, img.width, img.height])
    diff = ImageChops.difference(img, background)
    return diff.getbbox()


class ReceiptCanvasRendererTest(unittest.TestCase):
    def test_text_alignment_left_center_right(self) -> None:
        base_meta = ReceiptCanvasMeta(
            name="Align",
            paper_width="80",
            canvas_width_px=576,
            canvas_height_px=200,
        )
        results: dict[str, int] = {}

        for align in ("left", "center", "right"):
            doc = ReceiptCanvasDocument(
                version=1,
                meta=base_meta,
                elements=[
                    ReceiptCanvasElement(
                        id=f"txt_{align}",
                        type="text",
                        x=20,
                        y=20,
                        w=300,
                        h=60,
                        align=align,  # type: ignore[arg-type]
                        text_template="ABCD",
                        font_size=26,
                    )
                ],
            )
            image = render_canvas_layout(doc, context={}, paper_width="80")
            bbox = _non_white_bbox(image)
            self.assertIsNotNone(bbox)
            assert bbox is not None
            results[align] = bbox[0]

        self.assertLess(results["left"], results["center"])
        self.assertLess(results["center"], results["right"])

    def test_qr_data_template_is_substituted(self) -> None:
        doc = ReceiptCanvasDocument(
            version=1,
            meta=ReceiptCanvasMeta(
                name="QR",
                paper_width="80",
                canvas_width_px=576,
                canvas_height_px=220,
            ),
            elements=[
                ReceiptCanvasElement(
                    id="qr_1",
                    type="qr",
                    x=30,
                    y=30,
                    w=120,
                    h=120,
                    align="left",
                    data_template="{{order_number}}",
                    box_size=3,
                )
            ],
        )
        image = render_canvas_layout(doc, context={"order_number": "ORDER-123"}, paper_width="80")
        self.assertIsNotNone(_non_white_bbox(image))

    def test_missing_image_is_skipped_without_crash(self) -> None:
        doc = ReceiptCanvasDocument(
            version=1,
            meta=ReceiptCanvasMeta(
                name="ImageSkip",
                paper_width="80",
                canvas_width_px=576,
                canvas_height_px=260,
            ),
            elements=[
                ReceiptCanvasElement(
                    id="img_1",
                    type="image",
                    x=20,
                    y=20,
                    w=120,
                    h=80,
                    asset_path=".runtime/receipt_assets/no_such_file.png",
                ),
                ReceiptCanvasElement(
                    id="txt_1",
                    type="text",
                    x=20,
                    y=130,
                    w=200,
                    h=50,
                    text_template="fallback text",
                ),
            ],
        )
        image = render_canvas_layout(doc, context={}, paper_width="80")
        self.assertIsNotNone(_non_white_bbox(image))


if __name__ == "__main__":
    unittest.main()
