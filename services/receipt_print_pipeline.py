"""Receipt rendering and print pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from models.receipt_canvas_model import ReceiptCanvasDocument
from models.order_model import Order
from models.receipt_settings_model import ReceiptSettings
from services.printer_backend import PrinterBackend
from services.receipt_canvas_renderer import render_canvas_layout
from services.receipt_canvas_store import ReceiptCanvasStore
from services.receipt_context_builder import build_receipt_context
from services.receipt_renderer import ReceiptRenderer, RenderConfig
from services.receipt_template import TemplateElement, load_template
from services.windows_printer_service import WindowsPrinterService


@dataclass
class RenderedReceipt:
    """Final render output plus resolved context."""

    image: Image.Image
    context: dict[str, object]
    template: list[TemplateElement] | ReceiptCanvasDocument


def render_order_receipt(order: Order, settings: ReceiptSettings) -> RenderedReceipt:
    context = build_receipt_context(order, settings)
    suffix = Path(settings.template_path).suffix.lower()

    if suffix == ".json":
        store = ReceiptCanvasStore()
        layout = store.load_layout(settings.template_path)
        image = render_canvas_layout(
            layout,
            context=context,
            paper_width=settings.paper_width,
            margin_top=settings.margin_top,
            margin_bottom=settings.margin_bottom,
            dpi=settings.printer_dpi,
        )
        return RenderedReceipt(image=image, context=context, template=layout)

    template = load_template(settings.template_path)
    renderer = ReceiptRenderer(RenderConfig(paper_width=settings.paper_width, dpi=settings.printer_dpi))
    image = renderer.render(template, context)
    return RenderedReceipt(image=image, context=context, template=template)


def print_order_receipt(
    order: Order,
    settings: ReceiptSettings,
    printer_service: PrinterBackend | None = None,
) -> None:
    rendered = render_order_receipt(order, settings)
    service = printer_service or WindowsPrinterService()
    service.print_image(
        image=rendered.image,
        printer_name=settings.printer_name or None,
        job_name=f"Receipt_{order.order_number or 'Order'}",
    )

# For testing purposes: render and print a dummy receipt with hardcoded data.
def print_test_receipt(
    settings: ReceiptSettings,
    printer_service: PrinterBackend | None = None,
) -> None:
    dummy_order = Order(
        order_number="TEST-ORDER-01",
        name="하츠네 미쿠",
        phone="010-2007-0831",
        seat="토요일 A-466\n일요일 B-467",
        goods=["야채 주스 x200", "대파 x1"],
    )
    print_order_receipt(dummy_order, settings, printer_service=printer_service)
