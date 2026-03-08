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
from services.receipt_print_pipeline import print_order_receipt, render_order_receipt


class _PrinterStub:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    def print_image(self, image, printer_name: str | None, job_name: str) -> None:
        self.jobs.append(job_name)


def _canvas_doc(title: str) -> ReceiptCanvasDocument:
    return ReceiptCanvasDocument(
        version=1,
        meta=ReceiptCanvasMeta(
            name=title,
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
                text_template=title + "\n{{order_number}}",
            )
        ],
    )


def _dummy_order(*, goods: list[str] | None = None) -> Order:
    return Order(
        order_number="ORDER-001",
        name="홍길동",
        phone="010-0000-0000",
        seat="A-1",
        goods=goods or ["일반 상품 x1"],
        url="https://example.com/order/1",
    )


class ReceiptPrintPipelineTest(unittest.TestCase):
    def test_json_template_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout_path = Path(temp_dir) / "layout.json"
            store = ReceiptCanvasStore(default_layout_path=str(layout_path), asset_dir=str(Path(temp_dir) / "assets"))
            store.save_layout(str(layout_path), _canvas_doc("JSON"))

            settings = ReceiptSettings(template_path=str(layout_path), paper_width="80")
            rendered = render_order_receipt(_dummy_order(), settings)

            self.assertEqual(rendered.image.width, 851)
            self.assertEqual(rendered.context["order_number"], "ORDER-001")

    def test_legacy_tpl_template_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tpl_path = Path(temp_dir) / "layout.tpl"
            tpl_path.write_text("[[CENTER]]{{order_number}}", encoding="utf-8")

            settings = ReceiptSettings(template_path=str(tpl_path), paper_width="58")
            rendered = render_order_receipt(_dummy_order(), settings)

            self.assertEqual(rendered.image.width, 567)
            self.assertEqual(rendered.context["order_number"], "ORDER-001")

    def test_product_receipt_is_printed_when_option_is_enabled_and_goods_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            receipt_path = Path(temp_dir) / "receipt.json"
            product_path = Path(temp_dir) / "product.json"
            store = ReceiptCanvasStore(default_layout_path=str(receipt_path), asset_dir=str(Path(temp_dir) / "assets"))
            store.save_layout(str(receipt_path), _canvas_doc("메인 영수증"))
            store.save_layout(str(product_path), _canvas_doc("상품 영수증"))

            settings = ReceiptSettings(
                template_path=str(receipt_path),
                product_template_path=str(product_path),
                paper_width="80",
                print_product_receipt=True,
            )
            printer = _PrinterStub()

            copies = print_order_receipt(_dummy_order(goods=["일반 상품 x1"]), settings, printer_service=printer)

            self.assertEqual(copies, 2)
            self.assertEqual(printer.jobs, ["Receipt_ORDER-001", "ProductReceipt_ORDER-001"])

    def test_product_receipt_is_skipped_when_no_general_goods_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            receipt_path = Path(temp_dir) / "receipt.json"
            product_path = Path(temp_dir) / "product.json"
            store = ReceiptCanvasStore(default_layout_path=str(receipt_path), asset_dir=str(Path(temp_dir) / "assets"))
            store.save_layout(str(receipt_path), _canvas_doc("메인 영수증"))
            store.save_layout(str(product_path), _canvas_doc("상품 영수증"))

            settings = ReceiptSettings(
                template_path=str(receipt_path),
                product_template_path=str(product_path),
                paper_width="80",
                print_product_receipt=True,
                ticket_product_names=["티켓 상품"],
            )
            printer = _PrinterStub()

            copies = print_order_receipt(_dummy_order(goods=["티켓 상품 x2"]), settings, printer_service=printer)

            self.assertEqual(copies, 1)
            self.assertEqual(printer.jobs, ["Receipt_ORDER-001"])


if __name__ == "__main__":
    unittest.main()
