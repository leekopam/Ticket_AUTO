"""Receipt rendering and print pipeline."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from models.order_model import Order
from models.receipt_canvas_model import ReceiptCanvasDocument
from models.receipt_settings_model import ReceiptSettings
from services.printer_backend import PrinterBackend
from services.receipt_canvas_renderer import render_canvas_layout
from services.receipt_canvas_store import ReceiptCanvasStore
from services.receipt_context_builder import build_receipt_context
from services.receipt_renderer import ReceiptRenderer, RenderConfig
from services.receipt_template import TemplateElement, load_template
from services.windows_printer_service import WindowsPrinterService

logger = logging.getLogger(__name__)

DEFAULT_PRODUCT_TEMPLATE_PATH = "templates/product_receipt_layout.json"


@dataclass
class RenderedReceipt:
    """Final render output plus resolved context."""

    image: Image.Image
    context: dict[str, object]
    template: list[TemplateElement] | ReceiptCanvasDocument


def _render_receipt_for_template(
    template_path: str,
    context: dict[str, object],
    settings: ReceiptSettings,
) -> RenderedReceipt:
    suffix = Path(template_path).suffix.lower()

    if suffix == ".json":
        store = ReceiptCanvasStore()
        layout = store.load_layout(template_path)
        image = render_canvas_layout(
            layout,
            context=context,
            paper_width=settings.paper_width,
            margin_top=settings.margin_top,
            margin_bottom=settings.margin_bottom,
            dpi=settings.printer_dpi,
        )
        return RenderedReceipt(image=image, context=context, template=layout)

    template = load_template(template_path)
    renderer = ReceiptRenderer(RenderConfig(paper_width=settings.paper_width, dpi=settings.printer_dpi))
    image = renderer.render(template, context)
    return RenderedReceipt(image=image, context=context, template=template)


def render_order_receipt(
    order: Order,
    settings: ReceiptSettings,
    *,
    template_path: str | None = None,
) -> RenderedReceipt:
    context = build_receipt_context(order, settings)
    resolved_template_path = (template_path or settings.template_path).strip() or settings.template_path
    return _render_receipt_for_template(resolved_template_path, context, settings)


def print_order_receipt(
    order: Order,
    settings: ReceiptSettings,
    printer_service: PrinterBackend | None = None,
) -> int:
    """Print the main receipt and optionally an additional product receipt."""
    service = printer_service or WindowsPrinterService()

    rendered = render_order_receipt(order, settings)
    service.print_image(
        image=rendered.image,
        printer_name=settings.printer_name or None,
        job_name=f"Receipt_{order.order_number or 'Order'}",
    )
    logger.info("Main receipt printed: %s", order.order_number)

    total_copies = 1
    goods_lines = str(rendered.context.get("goods_lines", "") or "").strip()
    if settings.print_product_receipt and goods_lines:
        product_template_path = (
            str(settings.product_template_path or "").strip()
            or DEFAULT_PRODUCT_TEMPLATE_PATH
        )
        product_rendered = render_order_receipt(
            order,
            settings,
            template_path=product_template_path,
        )
        service.print_image(
            image=product_rendered.image,
            printer_name=settings.printer_name or None,
            job_name=f"ProductReceipt_{order.order_number or 'Order'}",
        )
        logger.info("Product receipt printed: %s", order.order_number)
        total_copies += 1

    return total_copies


def _re_render(context: dict[str, object], settings: ReceiptSettings) -> RenderedReceipt:
    """Render the current template again with a modified context."""
    return _render_receipt_for_template(settings.template_path, context, settings)


def print_test_receipt(
    settings: ReceiptSettings,
    printer_service: PrinterBackend | None = None,
) -> None:
    """Render and print a dummy order for template testing."""
    dummy_order = Order(
        order_number="TEST-ORDER-01",
        name="테스트 사용자",
        phone="010-2007-0831",
        seat="A-466\nB-467",
        goods=["티켓 상품 x2", "일반 상품 x1"],
    )
    print_order_receipt(dummy_order, settings, printer_service=printer_service)
