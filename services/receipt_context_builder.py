"""Builds template context from order and settings."""
from __future__ import annotations

import re

from models.order_model import Order
from models.receipt_settings_model import ReceiptSettings
from services.receipt_template import substitute

# "상품명 x수량" 패턴에서 수량 추출
_QTY_PATTERN = re.compile(r" x(\d+)$")


def split_goods_ticket(
    goods: list[str], ticket_names: set[str],
) -> tuple[list[str], list[str]]:
    """상품 목록을 일반상품/티켓으로 분리한다."""
    general: list[str] = []
    ticket: list[str] = []
    for item in goods:
        product_name = item.rsplit(" x", 1)[0] if " x" in item else item
        if product_name in ticket_names:
            ticket.append(item)
        else:
            general.append(item)
    return general, ticket


def parse_ticket_total_count(ticket_items: list[str]) -> int:
    """티켓 아이템 리스트에서 수량 합산 반환 (예: ["A x2", "B x1"] -> 3)"""
    total = 0
    for item in ticket_items:
        m = _QTY_PATTERN.search(item)
        total += int(m.group(1)) if m else 1
    return total


def build_receipt_context(order: Order, settings: ReceiptSettings) -> dict[str, object]:
    """Create rendering context for receipt template and QR payload."""
    # 티켓/일반상품 분리
    ticket_names = set(settings.ticket_product_names)
    general_items, ticket_items = split_goods_ticket(order.goods or [], ticket_names)

    goods_lines = "\n".join(general_items)
    ticket_lines = "\n".join(ticket_items)

    base_context: dict[str, object] = {
        "order_number": order.order_number,
        "buyer_name": order.name,
        "buyer_phone": order.phone,
        "seat": order.seat,
        "goods_lines": goods_lines,
        "ticket_lines": ticket_lines,
        "url": order.url,
        "event_title": settings.event_title,
        "show_qr": settings.show_qr,
        "show_logo": settings.show_logo,
        "logo_path": settings.logo_path,
    }
    qr_payload = substitute(settings.qr_payload_template, base_context).strip()

    return {
        **base_context,
        "etc_lines": "",
        "qr_payload": qr_payload,
    }
