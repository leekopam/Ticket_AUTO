"""영수증 미리보기 base64 변환 테스트."""
from __future__ import annotations

import base64
import unittest

from PIL import Image

from models.order_model import Order
from models.receipt_settings_model import ReceiptSettings
from project_paths import RESOURCE_RECEIPT_TEMPLATE_FILE
from services.receipt_print_pipeline import pil_image_to_base64, render_receipt_preview_base64


class PilImageToBase64Test(unittest.TestCase):
    """pil_image_to_base64 왕복 변환 정합성 검증."""

    def test_roundtrip_png(self) -> None:
        img = Image.new("RGB", (100, 200), color=(255, 0, 0))
        b64 = pil_image_to_base64(img)
        raw = base64.b64decode(b64)
        # PNG 시그니처 확인
        self.assertTrue(raw[:8] == b"\x89PNG\r\n\x1a\n")

    def test_decoded_image_matches_dimensions(self) -> None:
        img = Image.new("RGB", (384, 600), color=(0, 0, 0))
        b64 = pil_image_to_base64(img)
        from io import BytesIO
        decoded = Image.open(BytesIO(base64.b64decode(b64)))
        self.assertEqual(decoded.size, (384, 600))


class RenderReceiptPreviewBase64Test(unittest.TestCase):
    """render_receipt_preview_base64 반환값 검증."""

    def _make_settings(self, **overrides) -> ReceiptSettings:
        defaults = dict(
            printer_name="",
            paper_width="58",
            template_path=RESOURCE_RECEIPT_TEMPLATE_FILE.as_posix(),
        )
        defaults.update(overrides)
        return ReceiptSettings(**defaults)

    def test_returns_single_item_when_product_receipt_off(self) -> None:
        order = Order(
            order_number="PREVIEW-TEST-01",
            name="미리보기 테스트",
            phone="010-0000-0000",
            seat="A-1",
            goods=["테스트 상품 x1"],
        )
        settings = self._make_settings(print_product_receipt=False)
        items = render_receipt_preview_base64(order, settings)
        self.assertIsInstance(items, list)
        self.assertEqual(len(items), 1)
        label, b64 = items[0]
        self.assertEqual(label, "영수증")
        raw = base64.b64decode(b64)
        self.assertTrue(raw[:8] == b"\x89PNG\r\n\x1a\n")

    def test_returns_two_items_when_product_receipt_on_and_goods_exist(self) -> None:
        order = Order(
            order_number="PREVIEW-TEST-02",
            name="미리보기 테스트",
            phone="010-0000-0000",
            seat="A-1",
            goods=["티켓 x1", "일반 상품 x2"],
        )
        settings = self._make_settings(
            print_product_receipt=True,
            ticket_product_names=["티켓"],
        )
        items = render_receipt_preview_base64(order, settings)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0][0], "영수증")
        self.assertEqual(items[1][0], "상품 영수증")
        for _, b64 in items:
            raw = base64.b64decode(b64)
            self.assertTrue(raw[:8] == b"\x89PNG\r\n\x1a\n")

    def test_returns_single_item_when_product_receipt_on_but_no_goods(self) -> None:
        order = Order(
            order_number="PREVIEW-TEST-03",
            name="미리보기 테스트",
            phone="010-0000-0000",
            seat="A-1",
            goods=["티켓 x1"],
        )
        settings = self._make_settings(
            print_product_receipt=True,
            ticket_product_names=["티켓"],
        )
        items = render_receipt_preview_base64(order, settings)
        # 일반 상품이 없으므로 goods_lines가 비어 상품 영수증 미포함
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
