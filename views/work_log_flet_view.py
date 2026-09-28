"""티켓 업무 탭 — 수령 처리된 주문을 처리 순서대로 보고 상세를 확인한다.

레이아웃은 좌측 처리 목록(표) + 우측 선택 건 상세의 master-detail 구조다.
주 데이터는 주문 시트의 수령확인/주문상태 컬럼이며, _operations 시트는
처리 단말(device_id)·최종 상태 보강용으로만 조인한다 — PC 스캔 처리는
_operations에 기록되지 않으므로 목록 소스로 쓰면 PC 처리 건이 누락된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Mapping

import flet as ft

from models.order_model import Order
from services.device_presence import DeviceLike, operator_label
from views.dashboard_flet_view import (
    ACCENT_PRIMARY_BORDER,
    ACCENT_PRIMARY_DEEP,
    ACCENT_PRIMARY_SOFT,
    ICONS,
    STATUS_WARNING_SOFT,
    STATUS_WARNING_TEXT,
    split_order_goods,
)

# api_v1_server.RECONCILE_MARKER와 동일 값 — 주문 시트 주문상태에 기록되는 표기.
RECONCILE_STATUS = "확인필요"

SUCCESS_BADGE_TEXT = "수령완료"
SUCCESS_BADGE_BG = "#D8F4E3"
SUCCESS_BADGE_COLOR = "#1E6B45"


@dataclass(frozen=True)
class WorkLogRowState:
    """목록 행 한 줄에 필요한 표시 상태."""

    order_number: str
    seq: int
    name: str
    phone: str
    time_text: str
    badge_text: str
    badge_bgcolor: str
    badge_color: str
    summary_text: str
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
    badge_text: str
    badge_bgcolor: str
    badge_color: str
    ticket_items: tuple[str, ...]
    goods_items: tuple[str, ...]
    visible: bool


@dataclass(frozen=True)
class WorkLogViewState:
    """티켓 업무 탭 전체 표시 상태."""

    rows: tuple[WorkLogRowState, ...]
    detail: WorkLogDetailState | None
    count_text: str
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


def filter_work_log_orders(orders: list[Order]) -> list[Order]:
    """수령 완료 또는 확인필요 상태인 주문만 추린다."""
    return [
        order
        for order in orders or []
        if order.is_received or (order.order_status or "").strip() == RECONCILE_STATUS
    ]


def _work_log_time_key(order: Order, ops_index: dict[str, dict[str, str]]) -> str:
    """정렬 기준 시각 — 수령 시각 우선, 없으면 작업 레코드 갱신 시각."""
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


def _resolve_badge(order: Order) -> tuple[str, str, str]:
    """주문 상태에 맞는 배지 텍스트/색상을 반환한다."""
    if (order.order_status or "").strip() == RECONCILE_STATUS:
        return RECONCILE_STATUS, STATUS_WARNING_SOFT, STATUS_WARNING_TEXT
    if order.is_received:
        return SUCCESS_BADGE_TEXT, SUCCESS_BADGE_BG, SUCCESS_BADGE_COLOR
    return "처리중", "#E6EAFF", "#333F9E"


def build_work_log_view_state(
    orders: list[Order],
    ops_index: dict[str, dict[str, str]],
    ticket_names: list[str] | set[str],
    *,
    selected_order_number: str | None = None,
    device_lookup: Callable[[str], DeviceLike | None] | None = None,
) -> WorkLogViewState:
    """주문 목록에서 티켓 업무 탭의 표시 상태를 계산한다. 최신 건이 맨 위."""
    candidates = filter_work_log_orders(orders)
    candidates.sort(key=lambda order: _work_log_time_key(order, ops_index), reverse=True)
    total = len(candidates)
    reconcile_count = sum(
        1 for order in candidates if (order.order_status or "").strip() == RECONCILE_STATUS
    )
    count_text = f"처리 {total}건" + (f" · 확인필요 {reconcile_count}건" if reconcile_count else "")

    rows: list[WorkLogRowState] = []
    selected_upper = (selected_order_number or "").strip().upper()
    detail: WorkLogDetailState | None = None
    for display_index, order in enumerate(candidates):
        general_goods, ticket_goods = split_order_goods(order.goods, ticket_names)
        badge_text, badge_bg, badge_color = _resolve_badge(order)
        is_selected = bool(selected_upper) and order.order_number.upper() == selected_upper
        summary_parts = []
        if ticket_goods:
            summary_parts.append(f"티켓 {len(ticket_goods)}종")
        if general_goods:
            summary_parts.append(f"상품 {len(general_goods)}종")
        rows.append(WorkLogRowState(
            order_number=order.order_number,
            seq=total - display_index,
            name=order.name,
            phone=order.phone,
            time_text=format_work_time(_work_log_time_key(order, ops_index)),
            badge_text=badge_text,
            badge_bgcolor=badge_bg,
            badge_color=badge_color,
            summary_text=" · ".join(summary_parts) or "-",
            row_bgcolor=ACCENT_PRIMARY_SOFT if is_selected else ("#FFFFFF" if display_index % 2 == 0 else "#FAFAFA"),
            is_selected=is_selected,
        ))
        if is_selected:
            record = ops_index.get(order.order_number.upper())
            detail = WorkLogDetailState(
                order_number=order.order_number,
                name=order.name,
                phone=order.phone,
                seat=order.seat or "-",
                time_text=format_work_time(_work_log_time_key(order, ops_index)),
                device_text=operator_label(
                    record,
                    device_lookup or (lambda _device_id: None),
                    order_received=order.is_received,
                ),
                badge_text=badge_text,
                badge_bgcolor=badge_bg,
                badge_color=badge_color,
                ticket_items=tuple(ticket_goods),
                goods_items=tuple(general_goods),
                visible=True,
            )
    return WorkLogViewState(
        rows=tuple(rows),
        detail=detail,
        count_text=count_text,
        empty_text="아직 처리된 주문이 없습니다." if not rows else "",
    )


def _build_header_cell(text: str, width: float) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, weight=ft.FontWeight.BOLD, size=13, color="#333333"),
        width=width,
        alignment=ft.alignment.center_left,
    )


def _build_badge(text: str, bgcolor: str, color: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=color),
        bgcolor=bgcolor,
        border_radius=8,
        padding=ft.padding.symmetric(horizontal=8, vertical=3),
    )


def _build_list_row(row: WorkLogRowState, on_select: Callable[[str], None]) -> ft.Container:
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(
                    content=ft.Text(f"#{row.seq}", size=12, color="#8B97A8"),
                    width=44,
                    alignment=ft.alignment.center_left,
                ),
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(row.name or "-", size=14, weight=ft.FontWeight.W_600, color="#1F1F1F"),
                            ft.Text(row.summary_text, size=12, color="#6B7787"),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                    expand=True,
                ),
                ft.Container(
                    content=ft.Text(row.phone or "-", size=13, color="#333333"),
                    width=130,
                ),
                ft.Container(
                    content=ft.Text(row.time_text, size=12, color="#333333"),
                    width=110,
                ),
                ft.Container(
                    content=_build_badge(row.badge_text, row.badge_bgcolor, row.badge_color),
                    width=76,
                ),
            ],
            spacing=10,
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
            ft.Text(label, size=12, color="#8B97A8"),
            ft.Text(value or "-", size=14, color="#1F1F1F", weight=ft.FontWeight.W_600),
        ],
        spacing=2,
        tight=True,
    )


def _build_items_section(title: str, items: tuple[str, ...]) -> ft.Column:
    controls: list[ft.Control] = [ft.Text(title, size=13, weight=ft.FontWeight.W_600, color="#333333")]
    if items:
        controls.extend(
            ft.Container(
                content=ft.Text(item, size=14, color="#1F1F1F"),
                bgcolor="#F7F8FA",
                border_radius=8,
                padding=ft.padding.symmetric(horizontal=12, vertical=8),
            )
            for item in items
        )
    else:
        controls.append(ft.Text("없음", size=13, color="#8B97A8"))
    return ft.Column(controls=controls, spacing=6, tight=True)


def _build_detail_content(detail: WorkLogDetailState) -> ft.Column:
    device_suffix = f" · 처리 단말: {detail.device_text}" if detail.device_text else ""
    return ft.Column(
        controls=[
            ft.Row(
                controls=[
                    ft.Text("처리 상세", size=16, weight=ft.FontWeight.BOLD, color="#172235"),
                    _build_badge(detail.badge_text, detail.badge_bgcolor, detail.badge_color),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Text(
                f"주문번호 {detail.order_number} · 처리시간 {detail.time_text}{device_suffix}",
                size=12,
                color="#6B7787",
            ),
            ft.Container(height=4),
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
            _build_items_section("전달 상품", detail.goods_items),
        ],
        spacing=8,
        tight=True,
    )


def build_work_log_panel(
    *,
    on_select: Callable[[str], None],
    on_refresh: Callable[[ft.ControlEvent], None],
) -> ft.Container:
    """티켓 업무 탭 패널을 생성한다. refs는 panel._work_log_refs에 담긴다."""
    count_text = ft.Text("", size=13, color="#666666", key="work_log_count_text")
    empty_hint = ft.Container(
        content=ft.Column(
            controls=[
                ft.Icon(ICONS.FACT_CHECK_ROUNDED, size=34, color="#89A4C9"),
                ft.Text("아직 처리된 주문이 없습니다.", size=14, color="#8B97A8"),
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
        expand=5,
        bgcolor="#FFFFFF",
        border_radius=12,
        border=ft.border.all(1, "#E0E4EA"),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        content=ft.Column(
            controls=[
                ft.Container(
                    content=ft.Row(
                        controls=[
                            _build_header_cell("순번", 44),
                            ft.Container(
                                content=ft.Text("이름 / 내용", weight=ft.FontWeight.BOLD, size=13, color="#333333"),
                                expand=True,
                                alignment=ft.alignment.center_left,
                            ),
                            _build_header_cell("연락처", 130),
                            _build_header_cell("처리시간", 110),
                            _build_header_cell("상태", 76),
                        ],
                        spacing=10,
                    ),
                    bgcolor="#F5F5F5",
                    padding=ft.padding.symmetric(horizontal=14, vertical=10),
                    border=ft.border.only(bottom=ft.border.BorderSide(1, "#D2D2D2")),
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
        expand=6,
        bgcolor="#FFFFFF",
        border_radius=12,
        border=ft.border.all(1, "#E0E4EA"),
        content=ft.Stack(controls=[detail_placeholder, detail_body], expand=True),
    )

    panel = ft.Container(
        expand=True,
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("처리 완료 내역", size=20, weight=ft.FontWeight.BOLD, color="#172235"),
                        count_text,
                        ft.Container(expand=True),
                        ft.IconButton(
                            icon=ICONS.REFRESH_ROUNDED,
                            tooltip="새로고침",
                            icon_size=20,
                            on_click=on_refresh,
                            key="work_log_refresh_button",
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(height=10),
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
        "count_text": count_text,
        "empty_hint": empty_hint,
        "result_list": result_list,
        "detail_placeholder": detail_placeholder,
        "detail_body": detail_body,
    }
    return panel


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
    refs["count_text"].value = view_state.count_text
    refs["empty_hint"].visible = not view_state.rows
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
