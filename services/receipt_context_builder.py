"""Builds template context from order and settings."""
from __future__ import annotations

from models.order_model import Order
from models.receipt_settings_model import ReceiptSettings
from services.receipt_template import substitute


def build_receipt_context(order: Order, settings: ReceiptSettings) -> dict[str, object]:
    """Create rendering context for receipt template and QR payload."""
    goods_lines = "\n".join(order.goods) if order.goods else ""
    base_context: dict[str, object] = {
        "order_number": order.order_number,
        "buyer_name": order.name,
        "buyer_phone": order.phone,
        "seat": order.seat,
        "goods_lines": goods_lines,
        "url": order.url,
        "event_title": settings.event_title,
        "show_qr": settings.show_qr,
        "show_logo": settings.show_logo,
        "logo_path": settings.logo_path,
    }
    qr_payload = substitute(settings.qr_payload_template, base_context).strip()

    return {
        **base_context,
        "ticket_lines": order.seat,
        "etc_lines": "",
        "qr_payload": qr_payload,
    }
