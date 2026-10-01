"""처리 현황 조회 탭 — 수령 처리된 주문을 처리 순서대로 보고 상세를 확인한다.

레이아웃은 상단 행사 집계(처리 완료/미처리 손님·상품별 미수령) +
좌측 처리 목록(검색 가능한 표) + 우측 선택 건 상세의 구조다.
주 데이터는 주문 시트의 수령확인/주문상태 컬럼이며, _operations 시트는
처리 단말(device_id) 보강용으로만 조인한다 — PC 스캔 처리는 _operations에
기록되지 않으므로 목록 소스로 쓰면 PC 처리 건이 누락된다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Mapping

import flet as ft

from models.order_model import Order
from services.device_presence import DeviceLike, operator_label
from views.dashboard_flet_view import (
    ACCENT_PRIMARY,
    ACCENT_PRIMARY_BORDER,
    ACCENT_PRIMARY_DEEP,
    ACCENT_PRIMARY_SOFT,
    ICONS,
    split_order_goods,
)

# api_v1_server.RECONCILE_MARKER와 동일 값 — 주문 시트 주문상태에 기록되는 표기.
RECONCILE_STATUS = "확인필요"

# 상품 문자열의 수량 접미사 — "상품명 x2" / "상품명 ×2"
_QTY_SUFFIX_RE = re.compile(r"^(?P<name>.*?)\s*[x×](?P<qty>\d+)\s*$", re.IGNORECASE)

# 처리 단말 칩 색상 — 기기별 고정 배정 (해시 기반, 검색 결과에 따라 바뀌지 않음)
_DEVICE_PALETTE: tuple[tuple[str, str], ...] = (
    ("#DCFCE7", "#166534"),
    ("#DBEAFE", "#1E40AF"),
    ("#F3E8FF", "#6B21A8"),
    ("#FFEDD5", "#9A3412"),
    ("#FCE7F3", "#9D174D"),
    ("#E0E7FF", "#3730A3"),
)
_PC_CHIP_COLORS = ("#EEF1F4", "#4B5A6E")

# 상단 집계 카드 높이 — OD .dashboard-grid의 stretch(가장 높은 카드에 맞춤)와 동일하게
# 상품 카드 내부(패딩 32 + 헤더 24 + 간격 16 + 그리드 영역)에 맞춰 고정한다.
GOODS_COUNTS_HEIGHT = 96
DASHBOARD_CARD_HEIGHT = GOODS_COUNTS_HEIGHT + 32 + 24 + 16  # 168


def parse_goods_item(item: str) -> tuple[str, int]:
    """'상품명 xN'을 (상품명, 수량)으로 분리한다. 수량 접미사가 없으면 1."""
    match = _QTY_SUFFIX_RE.match((item or "").strip())
    if match:
        return match.group("name").strip(), int(match.group("qty"))
    return (item or "").strip(), 1


def device_chip_colors(device_text: str) -> tuple[str, str]:
    """기기 표기에 대한 (배경, 글자) 색상을 안정적으로 반환한다."""
    text = (device_text or "").strip()
    if not text:
        return _PC_CHIP_COLORS
    if text == "PC":
        return _PC_CHIP_COLORS
    index = 0
    for ch in text:
        index = (index * 31 + ord(ch)) % len(_DEVICE_PALETTE)
    return _DEVICE_PALETTE[index]


@dataclass(frozen=True)
class WorkLogRowState:
    """목록 행 한 줄에 필요한 표시 상태."""

    order_number: str
    seq: int
    name: str
    phone: str
    time_text: str
    ticket_items: tuple[str, ...]
    goods_items: tuple[str, ...]
    device_text: str
    device_bgcolor: str
    device_color: str
    row_bgcolor: str
    is_selected: bool


@dataclass(frozen=True)
class WorkLogDetailState:
    """우측 상세 패널에 필요한 표시 상태."""

    order_number: str
    name: str
    phone: str
    seat: str
    time_text: str
    device_text: str
    device_bgcolor: str
    device_color: str
    ticket_items: tuple[str, ...]
    goods_items: tuple[str, ...]
    visible: bool


@dataclass(frozen=True)
class GoodsRemainingState:
    """상품별 미수령 집계 한 항목."""

    name: str
    remaining: int


@dataclass(frozen=True)
class WorkLogViewState:
    """처리 현황 조회 탭 전체 표시 상태."""

    rows: tuple[WorkLogRowState, ...]
    detail: WorkLogDetailState | None
    completed_count: int
    pending_count: int
    goods_remaining: tuple[GoodsRemainingState, ...]
    empty_text: str


def _ops_record_order_id(record: Mapping[str, str]) -> str:
    """작업 레코드의 주문번호를 읽는다 — 구형 기록은 result_json에만 남아 있다."""
    order_id = str(record.get("order_id") or "").strip()
    if order_id:
        return order_id.upper()
    try:
        result = json.loads(str(record.get("result_json") or "{}"))
    except (ValueError, TypeError):
        return ""
    return str(result.get("order_id") or "").strip().upper()


def build_ops_index(operations: list[dict[str, str]] | None) -> dict[str, dict[str, str]]:
    """order_id → 최근 작업 레코드 인덱스를 만든다 (입력은 오래된 순)."""
    index: dict[str, dict[str, str]] = {}
    for record in operations or []:
        order_id = _ops_record_order_id(record)
        if order_id:
            index[order_id] = record
    return index


def _work_log_time_key(order: Order, ops_index: dict[str, dict[str, str]]) -> str:
    """정렬·표시 기준 시각 — 실제 처리시간 컬럼 우선, 없으면 수령 시각, 둘 다 없으면 작업 레코드 갱신 시각."""
    processing_time = (order.processing_time or "").strip()
    if processing_time:
        return processing_time
    received = (order.received_at or "").strip()
    if received:
        return received
    record = ops_index.get((order.order_number or "").strip().upper())
    return str(record.get("updated_at") or "") if record else ""


def format_work_time(value: str) -> str:
    """'YYYY-MM-DD HH:MM:SS' 형태를 'MM-DD HH:MM:SS'로 줄인다."""
    text = (value or "").strip()
    if len(text) >= 19:
        return text[5:19]
    if len(text) >= 16:
        return text[5:16]
    return text or "-"


def _matches_query(
    order: Order,
    ticket_goods: list[str],
    general_goods: list[str],
    device_text: str,
    query: str,
) -> bool:
    """이름·연락처·상품명·처리 기기 부분일치 검색. 연락처는 숫자만으로도 찾는다."""
    q = (query or "").strip().lower()
    if not q:
        return True
    texts = [order.name or "", device_text]
    texts.extend(parse_goods_item(item)[0] for item in [*ticket_goods, *general_goods])
    if any(q in text.lower() for text in texts):
        return True
    digits = re.sub(r"[-\s]", "", q)
    return bool(digits) and digits.isdigit() and digits in re.sub(r"[-\s]", "", order.phone or "")


def _build_goods_remaining(
    orders: list[Order],
    ticket_names: list[str] | set[str],
) -> tuple[GoodsRemainingState, ...]:
    """상품별 주문 수량 − 수령 완료 수량을 계산한다 (티켓으로 분류된 상품 제외)."""
    totals: dict[str, int] = {}
    received: dict[str, int] = {}
    for order in orders or []:
        general_goods, _ticket_goods = split_order_goods(order.goods, ticket_names)
        for item in general_goods:
            name, qty = parse_goods_item(item)
            if not name:
                continue
            totals[name] = totals.get(name, 0) + qty
            if order.is_received:
                received[name] = received.get(name, 0) + qty
    return tuple(
        GoodsRemainingState(name=name, remaining=qty - received.get(name, 0))
        for name, qty in totals.items()
        if qty - received.get(name, 0) > 0
    )


def build_work_log_view_state(
    orders: list[Order],
    ops_index: dict[str, dict[str, str]],
    ticket_names: list[str] | set[str],
    *,
    selected_order_number: str | None = None,
    device_lookup: Callable[[str], DeviceLike | None] | None = None,
    query: str = "",
) -> WorkLogViewState:
    """주문 목록에서 처리 현황 탭의 표시 상태를 계산한다. 최신 건이 맨 위.

    목록은 수령 완료 건만 보여준다(확인필요·미처리 제외).
    집계 지표는 검색과 무관하게 전체 주문 기준이다.
    """
    candidates = [order for order in orders or [] if order.is_received]
    candidates.sort(key=lambda order: _work_log_time_key(order, ops_index), reverse=True)
    total = len(candidates)

    completed_count = total
    pending_count = sum(1 for order in orders or [] if not order.is_received)
    goods_remaining = _build_goods_remaining(orders or [], ticket_names)

    rows: list[WorkLogRowState] = []
    selected_upper = (selected_order_number or "").strip().upper()
    detail: WorkLogDetailState | None = None
    for display_index, order in enumerate(candidates):
        general_goods, ticket_goods = split_order_goods(order.goods, ticket_names)
        record = ops_index.get(order.order_number.upper())
        device_text = operator_label(
            record,
            device_lookup or (lambda _device_id: None),
            order_received=order.is_received,
        )
        if not _matches_query(order, ticket_goods, general_goods, device_text, query):
            continue
        is_selected = bool(selected_upper) and order.order_number.upper() == selected_upper
        device_bg, device_ink = device_chip_colors(device_text)
        rows.append(WorkLogRowState(
            order_number=order.order_number,
            seq=total - display_index,
            name=order.name,
            phone=order.phone,
            time_text=format_work_time(_work_log_time_key(order, ops_index)),
            ticket_items=tuple(ticket_goods),
            goods_items=tuple(general_goods),
            device_text=device_text,
            device_bgcolor=device_bg,
            device_color=device_ink,
            row_bgcolor=ACCENT_PRIMARY_SOFT if is_selected else ("#FFFFFF" if display_index % 2 == 0 else "#FAFAFA"),
            is_selected=is_selected,
        ))
        if is_selected:
            detail = WorkLogDetailState(
                order_number=order.order_number,
                name=order.name,
                phone=order.phone,
                seat=order.seat or "-",
                time_text=format_work_time(_work_log_time_key(order, ops_index)),
                device_text=device_text,
                device_bgcolor=device_bg,
                device_color=device_ink,
                ticket_items=tuple(ticket_goods),
                goods_items=tuple(general_goods),
                visible=True,
            )
    searching = bool((query or "").strip())
    return WorkLogViewState(
        rows=tuple(rows),
        detail=detail,
        completed_count=completed_count,
        pending_count=pending_count,
        goods_remaining=goods_remaining,
        empty_text=(
            "" if rows
            else "검색 결과가 없습니다." if searching
            else "아직 처리된 주문이 없습니다."
        ),
    )


def _build_header_cell(text: str, width: float | None = None) -> ft.Container:
    cell = ft.Container(
        content=ft.Text(text, weight=ft.FontWeight.BOLD, size=13, color=ACCENT_PRIMARY_DEEP),
        alignment=ft.alignment.center_left,
    )
    if width is None:
        cell.expand = True
    else:
        cell.width = width
    return cell


def _build_device_chip(text: str, bgcolor: str, color: str) -> ft.Control:
    """처리 단말 칩 — 기기별 고정 색상으로 구분한다."""
    if not text:
        return ft.Container(width=0, height=0)
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=color),
        bgcolor=bgcolor,
        border_radius=4,
        padding=ft.padding.symmetric(horizontal=7, vertical=2),
    )


def _build_product_chip(item: str, *, accent: bool) -> ft.Container:
    """'상품명 xN'을 이름 + ×수량 칩으로 그린다."""
    name, qty = parse_goods_item(item)
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Text(name, size=12 if accent else 13,
                        weight=ft.FontWeight.W_600,
                        color=ACCENT_PRIMARY_DEEP if accent else "#17222E"),
                ft.Text(f"×{qty}", size=11 if accent else 12,
                        weight=ft.FontWeight.W_700,
                        color=ACCENT_PRIMARY_DEEP if accent else "#17222E"),
            ],
            spacing=5,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=ACCENT_PRIMARY_SOFT if accent else "#F7F8FA",
        border=ft.border.all(1, ACCENT_PRIMARY_BORDER if accent else "#E0E4EA"),
        border_radius=6 if accent else 5,
        padding=ft.padding.symmetric(horizontal=7, vertical=4),
    )


def _build_items_flow(items: tuple[str, ...], *, accent: bool, empty_text: str = "-") -> ft.Control:
    if not items:
        return ft.Text(empty_text, size=12, color="#8B97A8")
    return ft.Row(
        controls=[_build_product_chip(item, accent=accent) for item in items],
        wrap=True,
        spacing=5,
        run_spacing=5,
    )


def _build_list_row(row: WorkLogRowState, on_select: Callable[[str], None]) -> ft.Container:
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(
                    content=ft.Text(f"#{row.seq}", size=12, color="#8B97A8"),
                    width=36,
                    alignment=ft.alignment.center_left,
                ),
                ft.Container(
                    content=ft.Text(row.name or "-", size=14, weight=ft.FontWeight.W_600, color="#1F1F1F"),
                    width=70,
                    alignment=ft.alignment.center_left,
                ),
                ft.Container(
                    content=_build_items_flow(row.ticket_items, accent=False),
                    expand=2,
                    alignment=ft.alignment.center_left,
                ),
                ft.Container(
                    content=_build_items_flow(row.goods_items, accent=True),
                    expand=3,
                    alignment=ft.alignment.center_left,
                ),
                ft.Container(
                    content=ft.Text(row.phone or "-", size=12, color="#333333"),
                    width=112,
                ),
                ft.Container(
                    content=ft.Text(row.time_text, size=12, color="#333333"),
                    width=104,
                ),
                ft.Container(
                    content=_build_device_chip(row.device_text, row.device_bgcolor, row.device_color),
                    width=140,
                    alignment=ft.alignment.center_left,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=row.row_bgcolor,
        padding=ft.padding.symmetric(horizontal=14, vertical=10),
        border=ft.border.only(bottom=ft.border.BorderSide(1, "#EEEEEE")),
        on_click=lambda _e, order_number=row.order_number: on_select(order_number),
        key=f"work_log_row_{row.order_number}",
        ink=True,
    )


def _build_detail_field(label: str, value: str) -> ft.Column:
    return ft.Column(
        controls=[
            ft.Text(label, size=13, weight=ft.FontWeight.W_600, color="#17222E"),
            ft.Text(value or "-", size=14, weight=ft.FontWeight.W_600, color="#1F1F1F"),
        ],
        spacing=5,
        tight=True,
    )


def _build_items_section(title: str, items: tuple[str, ...]) -> ft.Column:
    controls: list[ft.Control] = [ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color="#17222E")]
    if items:
        controls.extend(
            ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Text(name, size=14, weight=ft.FontWeight.W_600, color="#1F1F1F"),
                        ft.Text(f"×{qty}", size=13, weight=ft.FontWeight.W_700, color=ACCENT_PRIMARY_DEEP),
                    ],
                    spacing=8,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor="#F7F8FA",
                border=ft.border.all(1, "#EEEEEE"),
                border_radius=8,
                padding=ft.padding.symmetric(horizontal=12, vertical=12),
            )
            for name, qty in (parse_goods_item(item) for item in items)
        )
    else:
        controls.append(ft.Text("-", size=13, color="#8B97A8"))
    return ft.Column(controls=controls, spacing=8, tight=True)


def _build_detail_content(detail: WorkLogDetailState) -> ft.Column:
    device_suffix = f" · 처리시간 {detail.time_text}" if detail.time_text else ""
    return ft.Column(
        controls=[
            ft.Row(
                controls=[
                    ft.Text("상세정보", size=16, weight=ft.FontWeight.BOLD, color="#172235"),
                    _build_device_chip(detail.device_text, detail.device_bgcolor, detail.device_color),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Text(
                f"주문번호 {detail.order_number}{device_suffix}",
                size=12,
                color="#6B7787",
            ),
            ft.Container(height=8),
            ft.Row(
                controls=[
                    ft.Container(content=_build_detail_field("이름", detail.name), expand=True),
                    ft.Container(content=_build_detail_field("연락처", detail.phone), expand=True),
                    ft.Container(content=_build_detail_field("좌석번호", detail.seat), expand=True),
                ],
                spacing=12,
            ),
            ft.Divider(height=18, color="#E4E8EE"),
            _build_items_section("티켓", detail.ticket_items),
            ft.Container(height=6),
            _build_items_section("상품 내역", detail.goods_items),
        ],
        spacing=8,
        tight=True,
    )


def _build_metric_card(title: str, value_text: ft.Text, icon: str, icon_color: str) -> ft.Container:
    return ft.Container(
        expand=10,
        bgcolor="#FFFFFF",
        border_radius=12,
        border=ft.border.all(1, "#E0E4EA"),
        height=DASHBOARD_CARD_HEIGHT,
        padding=ft.padding.symmetric(horizontal=18, vertical=16),
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Icon(icon, size=20, color=icon_color),
                        ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color="#17222E"),
                    ],
                    spacing=8,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                value_text,
            ],
            spacing=8,
            tight=True,
        ),
    )


def _metric_value(key: str, color: str) -> ft.Text:
    return ft.Text(
        "—", size=32, weight=ft.FontWeight.BOLD, color=color, key=key,
        style=ft.TextStyle(letter_spacing=-1),
    )


def build_work_log_panel(
    *,
    on_select: Callable[[str], None],
    on_refresh: Callable[[ft.ControlEvent], None],
    on_search: Callable[[str], None] | None = None,
    on_reset: Callable[[ft.ControlEvent], None] | None = None,
) -> ft.Container:
    """처리 현황 조회 패널을 생성한다. refs는 panel._work_log_refs에 담긴다."""
    completed_text = _metric_value("work_log_completed_count", ACCENT_PRIMARY_DEEP)
    pending_text = _metric_value("work_log_pending_count", "#7A6500")
    goods_counts = ft.ResponsiveRow(
        spacing=20,
        run_spacing=14,
        key="work_log_goods_counts",
    )
    goods_empty = ft.Text("등록된 상품이 없습니다.", size=12, color="#8B97A8", visible=False, key="work_log_goods_empty")

    dashboard_row = ft.Row(
        controls=[
            _build_metric_card("처리 완료 손님", completed_text, ICONS.CHECK_CIRCLE_ROUNDED, ACCENT_PRIMARY_DEEP),
            _build_metric_card("미처리 손님", pending_text, ICONS.SCHEDULE_ROUNDED, "#7A6500"),
            ft.Container(
                expand=28,
                bgcolor="#FFFFFF",
                border_radius=12,
                border=ft.border.all(1, "#E0E4EA"),
                height=DASHBOARD_CARD_HEIGHT,
                padding=ft.padding.symmetric(horizontal=18, vertical=16),
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Icon(ICONS.INVENTORY_2_ROUNDED, size=20, color=ACCENT_PRIMARY_DEEP),
                                        ft.Text("상품별 미수령", size=16, weight=ft.FontWeight.BOLD, color="#17222E"),
                                    ],
                                    spacing=8,
                                    tight=True,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Text("티켓 제외 · 수량 기준", size=11, color="#6B7787"),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        ft.Container(
                            content=ft.Column(
                                controls=[goods_counts, goods_empty],
                                spacing=4,
                                tight=True,
                                scroll=ft.ScrollMode.AUTO,
                            ),
                            height=GOODS_COUNTS_HEIGHT,
                        ),
                    ],
                    spacing=16,
                    tight=True,
                ),
            ),
        ],
        spacing=12,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    search_field = ft.TextField(
        hint_text="이름·연락처·상품·기기 검색",
        text_size=13,
        content_padding=ft.padding.symmetric(horizontal=12, vertical=8),
        border_radius=8,
        expand=True,
        key="work_log_search",
        on_change=lambda e: on_search(e.control.value or "") if on_search else None,
    )
    empty_hint = ft.Container(
        content=ft.Column(
            controls=[
                ft.Icon(ICONS.FACT_CHECK_ROUNDED, size=34, color="#89A4C9"),
                ft.Text("아직 처리된 주문이 없습니다.", size=14, color="#8B97A8", key="work_log_empty_text"),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
            tight=True,
        ),
        alignment=ft.alignment.center,
        padding=ft.padding.symmetric(vertical=48),
        visible=False,
        key="work_log_empty_hint",
    )
    result_list = ft.ListView(expand=True, spacing=0, auto_scroll=False, key="work_log_result_list")
    detail_placeholder = ft.Container(
        content=ft.Column(
            controls=[
                ft.Icon(ICONS.RECEIPT_LONG_ROUNDED, size=34, color="#89A4C9"),
                ft.Text("좌측 목록에서 처리된 주문을 선택하세요.", size=14, color="#8B97A8"),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
            tight=True,
        ),
        alignment=ft.alignment.center,
        expand=True,
        key="work_log_detail_placeholder",
    )
    detail_body = ft.Container(
        content=ft.Column(controls=[], tight=True, scroll=ft.ScrollMode.AUTO),
        padding=ft.padding.all(18),
        expand=True,
        visible=False,
        key="work_log_detail_body",
    )

    list_card = ft.Container(
        expand=7,
        bgcolor="#FFFFFF",
        border_radius=12,
        border=ft.border.all(1, "#E0E4EA"),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Column(
            controls=[
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Text("처리 현황", size=16, weight=ft.FontWeight.BOLD, color="#17222E"),
                            search_field,
                            ft.IconButton(
                                icon=ICONS.REFRESH_ROUNDED,
                                tooltip="새로고침",
                                icon_size=18,
                                on_click=on_refresh,
                                key="work_log_refresh_button",
                            ),
                            ft.IconButton(
                                icon=ICONS.RESTART_ALT_ROUNDED,
                                tooltip="처리 데이터 초기화",
                                icon_size=18,
                                on_click=on_reset,
                                key="work_log_reset_button",
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.padding.symmetric(horizontal=14, vertical=10),
                    border=ft.border.only(bottom=ft.border.BorderSide(1, "#E0E4EA")),
                ),
                ft.Container(
                    content=ft.Row(
                        controls=[
                            _build_header_cell("순번", 36),
                            _build_header_cell("이름", 70),
                            ft.Container(
                                content=ft.Text("티켓", weight=ft.FontWeight.BOLD, size=13, color=ACCENT_PRIMARY_DEEP),
                                expand=2,
                                alignment=ft.alignment.center_left,
                            ),
                            ft.Container(
                                content=ft.Text("상품", weight=ft.FontWeight.BOLD, size=13, color=ACCENT_PRIMARY_DEEP),
                                expand=3,
                                alignment=ft.alignment.center_left,
                            ),
                            _build_header_cell("연락처", 112),
                            _build_header_cell("처리시간", 104),
                            _build_header_cell("처리", 140),
                        ],
                        spacing=8,
                    ),
                    bgcolor=ACCENT_PRIMARY_SOFT,
                    padding=ft.padding.symmetric(horizontal=14, vertical=10),
                    border=ft.border.only(bottom=ft.border.BorderSide(1, ACCENT_PRIMARY_BORDER)),
                ),
                ft.Stack(
                    controls=[result_list, empty_hint],
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        ),
    )

    detail_card = ft.Container(
        expand=4,
        bgcolor="#FFFFFF",
        border_radius=12,
        border=ft.border.all(1, "#E0E4EA"),
        content=ft.Stack(controls=[detail_placeholder, detail_body], expand=True),
    )

    panel = ft.Container(
        expand=True,
        content=ft.Column(
            controls=[
                dashboard_row,
                ft.Container(height=14),
                ft.Row(
                    controls=[list_card, detail_card],
                    spacing=14,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ],
            expand=True,
        ),
    )
    panel._work_log_refs = {  # noqa: SLF001 — 기존 패널 속성 주입 패턴과 동일
        "completed_text": completed_text,
        "pending_text": pending_text,
        "goods_counts": goods_counts,
        "goods_empty": goods_empty,
        "empty_hint": empty_hint,
        "result_list": result_list,
        "detail_placeholder": detail_placeholder,
        "detail_body": detail_body,
    }
    return panel


def _build_goods_remaining_cell(item: GoodsRemainingState) -> ft.Column:
    return ft.Column(
        controls=[
            ft.Text(item.name, size=12, color="#17222E", no_wrap=False),
            ft.Text(str(item.remaining), size=24, weight=ft.FontWeight.W_600, color="#17222E"),
        ],
        spacing=6,
        tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.START,
        col={"sm": 6, "md": 4, "lg": 3},
    )


def apply_work_log_view_state(
    panel: ft.Container,
    view_state: WorkLogViewState,
    *,
    on_select: Callable[[str], None],
) -> None:
    """계산된 표시 상태를 패널 컨트롤에 반영한다."""
    refs = getattr(panel, "_work_log_refs", None)
    if not refs:
        return
    refs["completed_text"].value = f"{view_state.completed_count:,}"
    refs["pending_text"].value = f"{view_state.pending_count:,}"
    refs["goods_counts"].controls = [
        _build_goods_remaining_cell(item) for item in view_state.goods_remaining
    ]
    refs["goods_empty"].visible = not view_state.goods_remaining
    refs["empty_hint"].visible = not view_state.rows
    refs["empty_hint"].content.controls[1].value = view_state.empty_text
    refs["result_list"].controls = [
        _build_list_row(row, on_select) for row in view_state.rows
    ]
    if view_state.detail is not None:
        refs["detail_body"].content.controls = _build_detail_content(view_state.detail).controls
        refs["detail_body"].visible = True
        refs["detail_placeholder"].visible = False
    else:
        refs["detail_body"].visible = False
        refs["detail_placeholder"].visible = True
