"""네트워크 관리 탭 — PC 서버·페어링 기기·연결 품질·기기 관리를 한 화면에서 다룬다.

레이아웃: PC 서버 카드(주소·시작/중지·인터넷 품질) → 장치 요약 4칸 →
승인 대기 → 등록된 장치 표(필터·검색·기기별 관리 액션).
표시 상태 계산(build_network_view_state)과 컨트롤 적용(apply_network_view_state)을
분리해 뷰 로직을 pytest로 검증할 수 있게 한다. 티켓 업무 탭과 같은 패턴.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

import flet as ft
import flet.canvas as cv

from services.device_presence import (
    PRESENCE_OFFLINE,
    PRESENCE_ONLINE,
    PRESENCE_PENDING,
    PRESENCE_REVOKED,
    connection_quality,
    display_names,
    parse_seen_at,
    presence_state,
    signal_level,
)
from services.internet_quality_service import (
    QUALITY_GOOD,
    QUALITY_OFFLINE,
    QUALITY_POOR,
    QUALITY_UNREACHABLE,
    QUALITY_WARN,
    InternetQualityState,
)
from services.pairing_service import DeviceInfo, PendingApproval
from views.work_log_flet_view import _ops_record_order_id

logger = logging.getLogger(__name__)

MAX_DEVICE_ROWS = 100
RECENT_OPS_LIMIT = 20

FILTER_ALL = "all"
FILTER_ONLINE = "online"
FILTER_OFFLINE = "offline"
FILTER_BLOCKED = "blocked"
FILTERS: tuple[tuple[str, str], ...] = (
    (FILTER_ALL, "전체"),
    (FILTER_ONLINE, "연결됨"),
    (FILTER_OFFLINE, "연결 끊김"),
    (FILTER_BLOCKED, "차단됨"),
)

# 필터 칩 색상 — 선택 칩만 accent 단색, 나머지는 배경 없이 뮤트 텍스트(OD .filter)
_FILTER_ACTIVE_BG = "#39C5BB"


def _apply_filter_chip_styles(chips: dict[str, ft.TextButton], active_key: str) -> None:
    """선택된 필터 칩만 teal 배경+흰 글자, 나머지는 배경 없는 기본 상태."""
    for key, chip in chips.items():
        active = key == active_key
        chip.style.bgcolor = _FILTER_ACTIVE_BG if active else "#00000000"
        chip.style.side = ft.BorderSide(0, "#00000000")
        chip.content.color = "#FFFFFF" if active else "#536474"
        chip.content.weight = ft.FontWeight.BOLD if active else ft.FontWeight.W_400

STATUS_BADGE = {
    PRESENCE_ONLINE: ("연결됨", "#D8F4E3", "#1E6B45"),
    PRESENCE_OFFLINE: ("연결 끊김", "#E5E5E5", "#5D6E82"),
    PRESENCE_PENDING: ("승인 대기", "#FFE9C8", "#8A5A00"),
    PRESENCE_REVOKED: ("차단됨", "#FBDCDC", "#A12622"),
}

_QUALITY_STATE_LABELS = {
    QUALITY_GOOD: ("양호", "안정적인 연결 상태"),
    QUALITY_WARN: ("지연 주의", "응답이 평소보다 느려요"),
    QUALITY_POOR: ("불안정", "인터넷 연결을 확인해주세요"),
    QUALITY_OFFLINE: ("측정 전", "아직 연결 품질을 측정하지 않았어요"),
    QUALITY_UNREACHABLE: ("연결 불가", "모든 측정 경로가 응답하지 않아요 — 인터넷 연결 또는 방화벽을 확인해주세요"),
}
_QUALITY_STATE_COLORS = {
    QUALITY_GOOD: "#145F59",
    QUALITY_WARN: "#805600",
    QUALITY_POOR: "#AE323B",
    QUALITY_OFFLINE: "#536474",
    QUALITY_UNREACHABLE: "#AE323B",
}
_QUALITY_LOADING = "loading"


@dataclass(frozen=True)
class PendingRowState:
    pair_ticket: str
    device_name: str
    known_device: bool = False


@dataclass(frozen=True)
class DeviceRowState:
    record_id: str
    display_name: str
    reported_name: str
    status_key: str
    status_text: str
    status_bgcolor: str
    status_color: str
    signal_level: int
    quality_key: str
    quality_label: str
    quality_lit: int
    last_activity_text: str
    rtt_text: str
    missed_text: str
    processed_text: str
    can_disconnect: bool
    can_revoke: bool
    can_unblock: bool
    can_reconnect: bool


@dataclass(frozen=True)
class RecentOpState:
    """처리 내역 다이얼로그의 주문 한 건 표시 상태."""

    order_id: str
    state_text: str
    time_text: str
    customer_name: str = ""
    ticket_items: tuple[str, ...] = ()
    goods_items: tuple[str, ...] = ()


@dataclass(frozen=True)
class NetworkViewState:
    server_running: bool
    server_addr_text: str
    server_status_text: str
    online_count: int
    offline_count: int
    pending_count: int
    blocked_count: int
    pending_rows: tuple[PendingRowState, ...]
    device_rows: tuple[DeviceRowState, ...]
    empty_visible: bool
    empty_filtered: bool


def _format_last_activity(info: DeviceInfo, state: str, now: float) -> str:
    """마지막 활동 — 연결됨이면 현재 연결, 아니면 마지막 활동 시각을 절대 표기."""
    if state == PRESENCE_ONLINE:
        return "현재 연결됨"
    seen = parse_seen_at(info.last_seen_at)
    if seen is None:
        return "—"
    return time.strftime("%m.%d %H:%M:%S", time.localtime(seen))


def _format_metrics(info: DeviceInfo) -> tuple[str, str]:
    """(응답시간 ms, 누락 횟수) 숫자 문자열 — 측정치가 없으면 '—'."""
    rtt = str(info.last_rtt_ms) if info.last_rtt_ms is not None else "—"
    return rtt, str(info.missed_beats or 0)


# 장치별 연결 품질 색상 — 판정 규칙은 device_presence.connection_quality와 /v1/devices가 공유한다
_DEVICE_QUALITY_COLORS = {
    "good": ("#39C5BB", "#145F59"),
    "warn": ("#805600", "#805600"),
    "poor": ("#AE323B", "#AE323B"),
    "loading": ("#536474", "#536474"),
    "inactive": ("#536474", "#536474"),
}

def _device_quality(info: DeviceInfo, state: str, server_running: bool) -> tuple[str, str, int]:
    """(품질 상태키, 라벨, 채워진 막대 수) — 측정 불가 상태는 inactive 계열."""
    return connection_quality(
        info.last_rtt_ms,
        info.missed_beats or 0,
        state,
        server_running=server_running,
    )


def _device_hashes(info: DeviceInfo) -> set[str]:
    """DeviceInfo가 노출하는 모든 해시(현재+과거)를 모은다."""
    return set(getattr(info, "device_ids", ()) or ())


# 처리 완료로 간주하는 작업 종결 상태 — 접수/실패/확인필요는 건수에서 제외한다.
_COUNTED_OP_STATES = {"succeeded", "already_processed"}


def build_ops_device_counts(operations: list[dict[str, str]]) -> dict[str, int]:
    """작업 이력의 device_id별 처리 건수를 계산한다 — 완료된 작업만 집계."""
    counts: dict[str, int] = {}
    for record in operations or []:
        device_id = str(record.get("device_id") or "").strip()
        state = str(record.get("state") or "").strip()
        if device_id and state in _COUNTED_OP_STATES:
            counts[device_id] = counts.get(device_id, 0) + 1
    return counts


def build_device_history(
    info: DeviceInfo,
    operations: list[dict[str, str]],
    orders: list | None = None,
    ticket_names: list[str] | set[str] = (),
) -> tuple[RecentOpState, ...]:
    """선택 기기의 최근 처리 이력 — 주문과 조인해 주문자·티켓·상품을 함께 보여준다.

    주문이 확인된 실제 처리 건만 포함한다 — 조인 실패로 "-"만 남는
    자리표시 행은 목록에 올리지 않는다.
    """
    from views.dashboard_flet_view import split_order_goods

    order_map = {
        str(order.order_number or "").strip().upper(): order
        for order in orders or []
    }
    hashes = _device_hashes(info)
    rows: list[RecentOpState] = []
    for record in reversed(operations or []):
        if str(record.get("device_id") or "").strip() not in hashes:
            continue
        if str(record.get("state") or "").strip() not in _COUNTED_OP_STATES:
            continue
        order_id = _ops_record_order_id(record) or ""
        order = order_map.get(order_id)
        if order is None:
            continue
        goods, tickets = split_order_goods(order.goods, ticket_names)
        rows.append(
            RecentOpState(
                order_id=order_id,
                state_text=str(record.get("state") or ""),
                time_text=str(record.get("updated_at") or "")[5:16],
                customer_name=order.name or "",
                ticket_items=tuple(tickets),
                goods_items=tuple(goods),
            )
        )
        if len(rows) >= RECENT_OPS_LIMIT:
            break
    return tuple(rows)


def build_network_view_state(
    devices: list[DeviceInfo],
    pending: list[PendingApproval],
    operations: list[dict[str, str]],
    *,
    server_running: bool,
    server_addr: str,
    now: float,
    filter_key: str = FILTER_ALL,
    query: str = "",
) -> NetworkViewState:
    """레지스트리·작업 이력에서 네트워크 관리 탭의 표시 상태를 계산한다."""
    counts = build_ops_device_counts(operations)
    names = display_names(list(devices))
    pending_rows = tuple(
        PendingRowState(
            pair_ticket=p.pair_ticket,
            device_name=p.device_name or "알 수 없음",
            known_device=p.known_device,
        )
        for p in pending
    )

    online = offline = blocked = 0
    all_rows: list[DeviceRowState] = []
    for info in devices[:MAX_DEVICE_ROWS]:
        state = presence_state(info, now)
        badge_text, badge_bg, badge_color = STATUS_BADGE[state]
        if state == PRESENCE_ONLINE:
            online += 1
        elif state == PRESENCE_REVOKED:
            blocked += 1
        else:
            offline += 1
        processed = sum(counts.get(h, 0) for h in _device_hashes(info))
        rtt_text, missed_text = _format_metrics(info)
        quality_key, quality_label, quality_lit = _device_quality(info, state, server_running)
        all_rows.append(
            DeviceRowState(
                record_id=info.record_id,
                display_name=names[id(info)],
                reported_name=info.reported_name or "-",
                status_key=state,
                status_text=badge_text,
                status_bgcolor=badge_bg,
                status_color=badge_color,
                # 차단 기기는 신호 0 — 나머지는 마지막 활동 경과시간으로 단계 산출
                signal_level=0 if state == PRESENCE_REVOKED else signal_level(info.last_seen_at, now),
                quality_key=quality_key,
                quality_label=quality_label,
                quality_lit=quality_lit,
                last_activity_text=_format_last_activity(info, state, now),
                rtt_text=rtt_text,
                missed_text=missed_text,
                processed_text=f"처리 {processed}건",
                can_disconnect=state == PRESENCE_ONLINE,
                can_revoke=state != PRESENCE_REVOKED,
                can_unblock=state == PRESENCE_REVOKED,
                can_reconnect=state == PRESENCE_OFFLINE,
            )
        )

    q = (query or "").strip().lower()
    device_rows = [
        row for row in all_rows
        if (filter_key == FILTER_ALL
            or (filter_key == FILTER_ONLINE and row.status_key == PRESENCE_ONLINE)
            or (filter_key == FILTER_OFFLINE and row.status_key == PRESENCE_OFFLINE)
            or (filter_key == FILTER_BLOCKED and row.status_key == PRESENCE_REVOKED))
        and (not q or q in row.display_name.lower() or q in row.reported_name.lower())
    ]

    pending_count = len(pending_rows)
    return NetworkViewState(
        server_running=server_running,
        server_addr_text=server_addr or "서버가 꺼져 있습니다",
        server_status_text="서버 실행 중" if server_running else "서버 중지됨",
        online_count=online,
        offline_count=offline,
        pending_count=pending_count,
        blocked_count=blocked,
        pending_rows=pending_rows,
        device_rows=tuple(device_rows),
        empty_visible=not device_rows,
        empty_filtered=bool(all_rows) and not device_rows,
    )


def _build_history_chip(item: str, *, ticket: bool) -> ft.Container:
    """처리 내역 상품 칩 — '상품명 ×N'. 티켓은 강조 배경."""
    from views.work_log_flet_view import parse_goods_item

    name, qty = parse_goods_item(item)
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Text(name, size=14, weight=ft.FontWeight.W_600, color="#17222E"),
                ft.Text(f"×{qty}", size=14, weight=ft.FontWeight.W_700, color="#145F59"),
            ],
            spacing=12,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.END,
        ),
        bgcolor="#E7F8F7" if ticket else "#F3F6F7",
        border=ft.border.all(1, "#B8EAE6" if ticket else "#E0E4EA"),
        border_radius=7,
        padding=ft.padding.symmetric(horizontal=10, vertical=7),
    )


def _build_history_chips_row(title: str, items: tuple[str, ...], *, ticket: bool) -> ft.Control:
    return ft.Row(
        controls=[
            ft.Container(
                content=ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color="#17222E"),
                width=38,
                padding=ft.padding.only(top=8),
            ),
            ft.Row(
                controls=(
                    [_build_history_chip(item, ticket=ticket) for item in items]
                    if items
                    else [ft.Text("-", size=14, color="#8B97A8")]
                ),
                wrap=True,
                spacing=6,
                run_spacing=6,
                expand=True,
            ),
        ],
        spacing=10,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )


def build_history_item_control(row: RecentOpState) -> ft.Control:
    """처리 내역 다이얼로그의 주문 한 건 — 주문자·시각·주문번호 + 티켓/상품 칩."""
    heading = row.customer_name or row.order_id
    meta_parts = [part for part in (row.time_text, row.order_id if row.customer_name else "") if part]
    meta = " · ".join(meta_parts) if meta_parts else row.state_text
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text(heading, size=16, weight=ft.FontWeight.BOLD, color="#17222E"),
                ft.Text(meta, size=12, color="#536474"),
                _build_history_chips_row("티켓", row.ticket_items, ticket=True),
                _build_history_chips_row("상품", row.goods_items, ticket=False),
            ],
            spacing=4,
            tight=True,
        ),
        padding=ft.padding.only(top=14, bottom=14),
        border=ft.border.only(bottom=ft.border.BorderSide(1, "#E0E4EA")),
    )


def _build_status_badge(text: str, bgcolor: str, color: str) -> ft.Container:
    """상태 pill — OD의 .status 스타일(앞쪽 6px 색 점 + 텍스트)."""
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(width=6, height=6, bgcolor=color, border_radius=3),
                ft.Text(text, size=12, weight=ft.FontWeight.W_700, color=color),
            ],
            spacing=6,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=bgcolor,
        border_radius=5,
        padding=ft.padding.symmetric(horizontal=8, vertical=3),
    )


def _build_quality_metric(label: str, value: str, unit: str) -> ft.Control:
    """품질 메트릭 한 쌍 — '응답 45 ms'. 측정값 없으면 단위를 숨긴다."""
    controls: list[ft.Control] = [
        ft.Text(label, size=12, color="#536474"),
        ft.Text(value, size=12, weight=ft.FontWeight.BOLD, color="#17222E"),
    ]
    if value != "—":
        controls.append(ft.Text(unit, size=11, color="#536474"))
    return ft.Row(
        controls=controls,
        spacing=5,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.END,
    )


def _build_device_quality_cell(row: DeviceRowState) -> ft.Control:
    """장치↔PC 연결 품질 셀 — 신호 막대 + 상태 라벨 + 응답/누락 메트릭 (OD 구조)."""
    bar_color, ink = _DEVICE_QUALITY_COLORS.get(row.quality_key, _DEVICE_QUALITY_COLORS["inactive"])
    heights = (6, 10, 15, 20)
    bars = ft.Row(
        controls=[
            ft.Container(
                width=5,
                height=heights[i],
                bgcolor=bar_color if i < row.quality_lit else f"{bar_color}40",
                border_radius=1.5,
            )
            for i in range(4)
        ],
        spacing=3,
        tight=True,
        alignment=ft.MainAxisAlignment.END,
        vertical_alignment=ft.CrossAxisAlignment.END,
    )
    return ft.Column(
        controls=[
            ft.Row(
                controls=[
                    bars,
                    ft.Text(row.quality_label, size=13, weight=ft.FontWeight.W_700, color=ink),
                ],
                spacing=9,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Row(
                controls=[
                    _build_quality_metric("응답", row.rtt_text, "ms"),
                    _build_quality_metric("누락", row.missed_text, "회"),
                ],
                spacing=16,
                tight=True,
            ),
        ],
        spacing=5,
        tight=True,
    )


def _build_pending_row(
    row: PendingRowState,
    server_running: bool,
    on_approve: Callable[[str], None],
    on_reject: Callable[[str], None],
) -> ft.Container:
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Column(
                    controls=[
                        ft.Text(
                            f"{row.device_name} (등록된 기기 재페어링)" if row.known_device else row.device_name,
                            size=14,
                            weight=ft.FontWeight.W_600,
                        ),
                        ft.Text(
                            "승인 요청" if server_running else "서버를 시작한 뒤 승인할 수 있습니다.",
                            size=12,
                            color="#536474",
                        ),
                    ],
                    spacing=2,
                    tight=True,
                    expand=True,
                ),
                ft.OutlinedButton(
                    "차단",
                    on_click=lambda _e, t=row.pair_ticket: on_reject(t),
                    tooltip="이 기기의 연결 요청을 거절합니다",
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                ),
                ft.FilledButton(
                    "연결 승인",
                    on_click=lambda _e, t=row.pair_ticket: on_approve(t),
                    tooltip="이 기기의 연결을 승인합니다",
                    disabled=not server_running,
                    style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.padding.symmetric(horizontal=14, vertical=12),
        border=ft.border.only(top=ft.border.BorderSide(1, "#EEE5CF")),
    )


def _action_button(
    text: str,
    on_click: Callable[[ft.ControlEvent], None],
    *,
    key: str | None = None,
    danger: bool = False,
) -> ft.OutlinedButton:
    """관리 열 버튼 — OD .btn 스타일 (테두리 있는 작은 버튼)."""
    return ft.OutlinedButton(
        key=key,
        content=ft.Text(
            text,
            size=12,
            weight=ft.FontWeight.W_600,
            color="#AE323B" if danger else "#17222E",
        ),
        on_click=on_click,
        style=ft.ButtonStyle(
            side=ft.BorderSide(1, "#E0E4EA"),
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=ft.padding.symmetric(horizontal=9, vertical=6),
        ),
    )


def _build_device_row(
    row: DeviceRowState,
    on_disconnect: Callable[[str], None],
    on_rename: Callable[[str], None],
    on_revoke: Callable[[str], None],
    on_unblock: Callable[[str], None],
    on_reconnect: Callable[[str], None],
    on_history: Callable[[str], None],
    on_remove: Callable[[str], None],
) -> ft.Container:
    actions: list[ft.Control] = []
    if row.can_disconnect:
        actions.append(
            _action_button("연결 해제", lambda _e, r=row.record_id: on_disconnect(r),
                           key=f"network_disconnect_{row.record_id}")
        )
    if row.can_reconnect:
        actions.append(
            _action_button("재연결 요청", lambda _e, r=row.record_id: on_reconnect(r),
                           key=f"network_reconnect_{row.record_id}")
        )
    if row.can_unblock:
        actions.append(
            _action_button("차단 해제", lambda _e, r=row.record_id: on_unblock(r),
                           key=f"network_unblock_{row.record_id}")
        )
    if row.can_revoke:
        actions.append(
            _action_button("차단", lambda _e, r=row.record_id: on_revoke(r),
                           key=f"network_revoke_{row.record_id}", danger=True)
        )
    actions.append(
        _action_button("제거", lambda _e, r=row.record_id: on_remove(r),
                       key=f"network_remove_{row.record_id}", danger=True)
    )
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Container(
                                content=ft.Icon(ft.icons.SMARTPHONE_ROUNDED, size=22, color="#526574"),
                                width=38,
                                height=44,
                                bgcolor="#F8FAFB",
                                border=ft.border.all(1, "#E0E4EA"),
                                border_radius=8,
                                alignment=ft.alignment.center,
                            ),
                            ft.Column(
                                controls=[
                                    ft.Row(
                                        controls=[
                                            ft.Text(row.display_name, size=14, weight=ft.FontWeight.W_700, color="#1F1F1F"),
                                            ft.IconButton(
                                                icon=ft.icons.EDIT_ROUNDED,
                                                icon_size=14,
                                                tooltip="이름 변경",
                                                on_click=lambda _e, r=row.record_id: on_rename(r),
                                                key=f"network_rename_{row.record_id}",
                                                width=26,
                                                height=26,
                                            ),
                                        ],
                                        spacing=4,
                                        tight=True,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Text(row.reported_name, size=12, color="#536474"),
                                ],
                                spacing=2,
                                tight=True,
                            ),
                        ],
                        spacing=10,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    expand=_COL_DEVICE,
                    alignment=ft.alignment.center_left,
                ),
                ft.Container(
                    content=_build_device_quality_cell(row),
                    expand=_COL_QUALITY,
                    alignment=ft.alignment.center,
                ),
                ft.Container(
                    content=_build_status_badge(row.status_text, row.status_bgcolor, row.status_color),
                    expand=_COL_STATUS,
                    alignment=ft.alignment.center,
                ),
                ft.Container(
                    content=ft.Text(row.last_activity_text, size=12, color="#333333"),
                    expand=_COL_ACTIVITY,
                    alignment=ft.alignment.center,
                ),
                ft.Container(
                    content=ft.TextButton(
                        content=ft.Text(
                            row.processed_text,
                            size=13,
                            weight=ft.FontWeight.W_600,
                            color="#2659A8",
                            style=ft.TextStyle(decoration=ft.TextDecoration.UNDERLINE),
                        ),
                        on_click=lambda _e, r=row.record_id: on_history(r),
                        key=f"network_history_{row.record_id}",
                    ),
                    expand=_COL_HISTORY,
                    alignment=ft.alignment.center,
                ),
                ft.Container(
                    content=ft.Row(
                        controls=actions,
                        spacing=6,
                        tight=True,
                        wrap=True,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    expand=_COL_MANAGE,
                    alignment=ft.alignment.center,
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor="#FFFFFF",
        padding=ft.padding.symmetric(horizontal=20, vertical=17),
        border=ft.border.only(bottom=ft.border.BorderSide(1, "#EDF0F2")),
        on_hover=lambda e: _on_device_row_hover(e),
    )


# 행 호버 배경 — OD의 tbody tr:hover (--row-hover: accent 6%)
_DEVICE_ROW_HOVER_BG = "#F3FBFB"


def _on_device_row_hover(e: ft.HoverEvent) -> None:
    """마우스를 올린 장치 행을 연한 teal로 표시한다."""
    e.control.bgcolor = _DEVICE_ROW_HOVER_BG if e.data == "true" else "#FFFFFF"
    e.control.update()


# 컬럼 비율 — 장치 열만 넓게, 나머지는 균등에 가깝게(장치:품질:상태:활동:내역:관리)
_COL_DEVICE, _COL_QUALITY, _COL_STATUS, _COL_ACTIVITY, _COL_HISTORY, _COL_MANAGE = 26, 18, 12, 13, 11, 20


def _build_header_cell(
    text: str,
    flex: int,
    *,
    center: bool = False,
) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, weight=ft.FontWeight.BOLD, size=12, color="#145F59"),
        alignment=ft.alignment.center if center else ft.alignment.center_left,
        expand=flex,
    )


def _build_summary_cell(title: str, value_ref: ft.Text) -> ft.Container:
    return ft.Container(
        expand=True,
        padding=ft.padding.symmetric(horizontal=20, vertical=14),
        content=ft.Column(
            controls=[
                ft.Text(title, size=13, weight=ft.FontWeight.W_600, color="#536474"),
                ft.Row(
                    controls=[value_ref, ft.Text("대", size=12, color="#536474")],
                    spacing=6,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
            ],
            spacing=4,
            tight=True,
        ),
    )


def build_network_panel(
    link_button: ft.Control | None = None,
    server_toggle_button: ft.Control | None = None,
    regen_cert_button: ft.Control | None = None,
    header_button: ft.Control | None = None,
    on_quality_check: Callable[[ft.ControlEvent], None] | None = None,
) -> dict[str, ft.Control]:
    """네트워크 관리 패널 컨트롤 묶음을 만든다. 내용은 apply_*로 채운다.

    link/server_toggle/regen_cert_button: 서버 상태 카드 오른쪽에 놓는 버튼 (호출부가 주입).
    header_button: "등록된 장치" 제목 줄 오른쪽에 놓는 버튼 (OD의 새로고침 버튼 자리).
    on_quality_check: 인터넷 품질 "다시 확인" 버튼 콜백.
    """
    server_addr_text = ft.Text("", size=13, weight=ft.FontWeight.W_600, color="#145F59")
    server_status_badge = ft.Container(key="network_server_status_badge")
    quality_status = ft.Text("확인 전", size=20, weight=ft.FontWeight.BOLD, color="#536474", key="network_quality_status")
    quality_hint = ft.Text("품질을 확인하면 최근 응답시간이 표시됩니다.", size=12, color="#536474", key="network_quality_hint")
    quality_latency = ft.Text("—", size=24, weight=ft.FontWeight.BOLD, key="network_quality_latency")
    quality_jitter = ft.Text("—", size=24, weight=ft.FontWeight.BOLD, key="network_quality_jitter")
    quality_loss = ft.Text("—", size=24, weight=ft.FontWeight.BOLD, key="network_quality_loss")
    quality_bars = ft.Row(
        controls=[
            ft.Container(width=7, height=h, bgcolor="#E0E4EA", border_radius=2)
            for h in (10, 17, 25, 33)
        ],
        spacing=4,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.END,
        key="network_quality_bars",
    )
    quality_trend = cv.Canvas(shapes=[], width=240, height=48)
    quality_range = ft.Text("—", size=11, color="#536474", key="network_quality_range")
    quality_updated = ft.Text("3초마다 갱신", size=11, color="#536474", key="network_quality_updated")

    counts = {
        key: ft.Text("0", size=26, weight=ft.FontWeight.BOLD, key=f"network_count_{key}")
        for key in ("online", "offline", "pending", "blocked")
    }
    pending_count_badge = ft.Container(key="network_pending_badge")
    pending_column = ft.Column(spacing=0, tight=True)
    pending_section = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("새 장치가 연결 승인을 기다리는 중", size=15, weight=ft.FontWeight.W_600, color="#70500D"),
                        pending_count_badge,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                pending_column,
            ],
            spacing=6,
            tight=True,
        ),
        bgcolor="#FFFDF7",
        border=ft.border.all(1, "#EAD7A6"),
        border_radius=12,
        padding=ft.padding.symmetric(horizontal=20, vertical=14),
        visible=False,
        key="network_pending_section",
    )

    # 필터 칩 — 선택 칩만 solid teal, 나머지는 상태별 soft 배경
    filter_chips = {
        key: ft.TextButton(
            key=f"network_filter_{key}",
            content=ft.Text(label, size=13),
            style=ft.ButtonStyle(
                padding=ft.padding.symmetric(horizontal=12, vertical=7),
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
        )
        for key, label in FILTERS
    }
    _apply_filter_chip_styles(filter_chips, FILTER_ALL)
    search_field = ft.TextField(
        hint_text="장치 이름 검색",
        text_size=13,
        width=260,
        content_padding=ft.padding.symmetric(horizontal=11, vertical=9),
        border_radius=7,
        key="network_search",
    )

    device_list = ft.Column(spacing=0, tight=True)
    empty_text = ft.Text("일치하는 장치가 없습니다.", size=13, color="#536474", key="network_empty")
    empty_reset = ft.TextButton(
        "검색·필터 초기화",
        visible=False,
        key="network_empty_reset",
        style=ft.ButtonStyle(color="#2659A8"),
    )
    empty_box = ft.Container(
        content=ft.Column(
            controls=[empty_text, empty_reset],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
            tight=True,
        ),
        alignment=ft.alignment.center,
        padding=ft.padding.symmetric(vertical=36),
        visible=False,
        key="network_empty_box",
    )

    panel = ft.Container(
        expand=True,
        padding=ft.padding.symmetric(horizontal=24, vertical=20),
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("네트워크 관리", size=24, weight=ft.FontWeight.BOLD, color="#172235"),
                        ft.Row(
                            controls=[b for b in (link_button,) if b is not None],
                            spacing=8,
                            tight=True,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Row(
                                        controls=[
                                            ft.Container(
                                                content=ft.Icon(ft.icons.MONITOR_ROUNDED, size=28, color="#145F59"),
                                                width=52,
                                                height=52,
                                                bgcolor="#E7F8F7",
                                                border_radius=12,
                                                alignment=ft.alignment.center,
                                            ),
                                            ft.Column(
                                                controls=[
                                                    ft.Text("PC 본체", size=17, weight=ft.FontWeight.BOLD, color="#17222E"),
                                                    ft.Container(
                                                        content=ft.Row(
                                                            controls=[
                                                                ft.Text("서버 주소", size=12, color="#145F59"),
                                                                server_addr_text,
                                                            ],
                                                            spacing=8,
                                                            tight=True,
                                                        ),
                                                        bgcolor="#E7F8F7",
                                                        border=ft.border.all(1, "#E0E4EA"),
                                                        border_radius=6,
                                                        padding=ft.padding.symmetric(horizontal=10, vertical=4),
                                                    ),
                                                ],
                                                spacing=4,
                                                tight=True,
                                            ),
                                        ],
                                        spacing=16,
                                        tight=True,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    ),
                                    ft.Row(
                                        controls=[
                                            server_status_badge,
                                            *[b for b in (server_toggle_button, regen_cert_button) if b is not None],
                                        ],
                                        spacing=8,
                                        tight=True,
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                wrap=True,
                            ),
                            ft.Divider(height=20, color="#E0E4EA"),
                            ft.Row(
                                controls=[
                                    ft.Text("인터넷 연결 품질", size=15, weight=ft.FontWeight.W_600, color="#17222E"),
                                    ft.OutlinedButton(
                                        "다시 확인",
                                        on_click=on_quality_check,
                                        key="network_quality_check",
                                        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            # OD .quality-grid — 1.1fr / 1.5fr / 1.1fr, gap 24
                            ft.Row(
                                controls=[
                                    ft.Container(
                                        expand=11,
                                        content=ft.Row(
                                            controls=[
                                                quality_bars,
                                                ft.Column(
                                                    controls=[quality_status, quality_hint],
                                                    spacing=2,
                                                    tight=True,
                                                ),
                                            ],
                                            spacing=12,
                                            tight=True,
                                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                        ),
                                    ),
                                    ft.Container(
                                        expand=15,
                                        content=ft.Row(
                                            controls=[
                                                ft.Column(
                                                    controls=[
                                                        ft.Text("응답시간", size=12, weight=ft.FontWeight.W_600, color="#536474"),
                                                        ft.Row(controls=[quality_latency, ft.Text("ms", size=12, color="#536474")], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.END),
                                                    ],
                                                    spacing=3,
                                                    tight=True,
                                                    expand=1,
                                                ),
                                                ft.Column(
                                                    controls=[
                                                        ft.Text("지연 변동", size=12, weight=ft.FontWeight.W_600, color="#536474"),
                                                        ft.Row(controls=[quality_jitter, ft.Text("ms", size=12, color="#536474")], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.END),
                                                    ],
                                                    spacing=3,
                                                    tight=True,
                                                    expand=1,
                                                ),
                                                ft.Column(
                                                    controls=[
                                                        ft.Text("손실률", size=12, weight=ft.FontWeight.W_600, color="#536474"),
                                                        ft.Row(controls=[quality_loss, ft.Text("%", size=12, color="#536474")], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.END),
                                                    ],
                                                    spacing=3,
                                                    tight=True,
                                                    expand=1,
                                                ),
                                            ],
                                            spacing=16,
                                        ),
                                    ),
                                    ft.Container(
                                        expand=11,
                                        content=ft.Column(
                                            controls=[
                                                ft.Container(
                                                    content=quality_trend,
                                                    border=ft.border.only(left=ft.border.BorderSide(1, "#E0E4EA")),
                                                    padding=ft.padding.only(left=20),
                                                ),
                                                ft.Container(
                                                    content=ft.Row(
                                                        controls=[
                                                            ft.Text("최근 응답시간", size=11, color="#536474"),
                                                            quality_range,
                                                        ],
                                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                                    ),
                                                    padding=ft.padding.only(left=20),
                                                ),
                                            ],
                                            spacing=2,
                                            tight=True,
                                        ),
                                    ),
                                ],
                                spacing=24,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            quality_updated,
                        ],
                        spacing=10,
                        tight=True,
                    ),
                    bgcolor="#FFFFFF",
                    border=ft.border.all(1, "#E0E4EA"),
                    border_radius=12,
                    padding=ft.padding.symmetric(horizontal=22, vertical=18),
                ),
                ft.Container(
                    content=ft.Row(
                        controls=[
                            _build_summary_cell("연결된 장치", counts["online"]),
                            ft.Container(width=1, bgcolor="#E0E4EA"),
                            _build_summary_cell("연결 끊김", counts["offline"]),
                            ft.Container(width=1, bgcolor="#E0E4EA"),
                            _build_summary_cell("승인 대기", counts["pending"]),
                            ft.Container(width=1, bgcolor="#E0E4EA"),
                            _build_summary_cell("차단된 장치", counts["blocked"]),
                        ],
                        spacing=0,
                    ),
                    bgcolor="#FFFFFF",
                    border=ft.border.all(1, "#E0E4EA"),
                    border_radius=12,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                ),
                pending_section,
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        ft.Text("등록된 장치", size=17, weight=ft.FontWeight.BOLD, color="#17222E"),
                                        ft.Row(
                                            controls=[b for b in (header_button,) if b is not None],
                                            spacing=8,
                                            tight=True,
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                padding=ft.padding.only(left=20, right=20, top=19, bottom=15),
                            ),
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        ft.Row(controls=list(filter_chips.values()), spacing=5, tight=True, wrap=True),
                                        search_field,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                padding=ft.padding.only(left=20, right=20, bottom=17),
                            ),
                            ft.Container(
                                content=ft.Row(
                                    controls=[
                                        _build_header_cell("장치", _COL_DEVICE, center=True),
                                        _build_header_cell("연결 품질", _COL_QUALITY, center=True),
                                        _build_header_cell("연결 상태", _COL_STATUS, center=True),
                                        _build_header_cell("마지막 활동", _COL_ACTIVITY, center=True),
                                        _build_header_cell("처리 내역", _COL_HISTORY, center=True),
                                        _build_header_cell("관리", _COL_MANAGE, center=True),
                                    ],
                                    spacing=10,
                                ),
                                bgcolor="#CEF0EE",
                                padding=ft.padding.symmetric(horizontal=20, vertical=11),
                                border=ft.border.only(
                                    top=ft.border.BorderSide(1, "#B8EAE6"),
                                    bottom=ft.border.BorderSide(1, "#B8EAE6"),
                                ),
                            ),
                            device_list,
                            empty_box,
                        ],
                        spacing=0,
                        tight=True,
                    ),
                    bgcolor="#FFFFFF",
                    border=ft.border.all(1, "#E0E4EA"),
                    border_radius=12,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                ),
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
    )
    return {
        "panel": panel,
        "server_addr_text": server_addr_text,
        "server_status_badge": server_status_badge,
        "quality_status": quality_status,
        "quality_hint": quality_hint,
        "quality_latency": quality_latency,
        "quality_jitter": quality_jitter,
        "quality_loss": quality_loss,
        "quality_bars": quality_bars,
        "quality_trend": quality_trend,
        "quality_range": quality_range,
        "quality_updated": quality_updated,
        "counts": counts,
        "pending_count_badge": pending_count_badge,
        "pending_section": pending_section,
        "pending_column": pending_column,
        "filter_chips": filter_chips,
        "search_field": search_field,
        "device_list": device_list,
        "empty_text": empty_text,
        "empty_reset": empty_reset,
        "empty_box": empty_box,
    }


_TREND_W, _TREND_H = 240, 48
_TREND_BASE_Y = 44  # OD SVG 기준선 y=44


def _trend_shapes(history: tuple[int, ...], color: str) -> list:
    """최근 응답시간 추이를 OD처럼 폴리라인으로 그린다 — 기준선 + 꺾은선."""
    shapes = [
        cv.Line(0, _TREND_BASE_Y, _TREND_W, _TREND_BASE_Y, paint=ft.Paint(color="#E0E4EA"))
    ]
    if history:
        maximum = max(history) or 1
        step = _TREND_W / max(len(history) - 1, 1)
        elements: list = []
        for i, value in enumerate(history):
            x = i * step
            y = _TREND_BASE_Y - (value / maximum) * (_TREND_BASE_Y - 6)
            elements.append(cv.Path.MoveTo(x, y) if i == 0 else cv.Path.LineTo(x, y))
        shapes.append(
            cv.Path(
                elements,
                paint=ft.Paint(
                    style=ft.PaintingStyle.STROKE,
                    stroke_width=2.5,
                    stroke_cap=ft.StrokeCap.ROUND,
                    color=color,
                ),
            )
        )
    return shapes


def apply_internet_quality(
    controls: dict[str, ft.Control],
    state: InternetQualityState | None,
    *,
    server_running: bool,
) -> None:
    """인터넷 품질 측정 결과를 PC 서버 카드에 반영한다."""
    if not server_running:
        status_key, hint = "paused", "서버 시작 후 다시 확인합니다"
        title = "확인 일시중지"
        color = "#536474"
    elif state is None:
        status_key, title, hint = _QUALITY_LOADING, "확인 중", "연결 품질을 확인하고 있어요"
        color = "#536474"
    else:
        title, hint = _QUALITY_STATE_LABELS.get(state.status, ("확인 중", ""))
        # 일부 경로만 실패한 경우 어느 경로가 안 되는지 함께 보여준다
        if state.failed_endpoints and state.status != QUALITY_UNREACHABLE:
            hint = f"{hint} · 응답 없음: {', '.join(state.failed_endpoints)}"
        status_key = state.status
        color = _QUALITY_STATE_COLORS.get(state.status, "#536474")
    lit = {QUALITY_GOOD: 4, QUALITY_WARN: 2, QUALITY_POOR: 1}.get(status_key, 0)
    controls["quality_status"].value = title
    controls["quality_status"].color = color
    controls["quality_hint"].value = hint
    for i, bar in enumerate(controls["quality_bars"].controls):
        bar.bgcolor = color if i < lit else "#E0E4EA"
    if state is not None:
        controls["quality_latency"].value = str(state.latency_ms) if state.latency_ms is not None else "—"
        controls["quality_jitter"].value = str(state.jitter_ms) if state.jitter_ms is not None else "—"
        controls["quality_loss"].value = f"{state.loss_pct:g}" if state.loss_pct is not None else "—"
        history = state.history
        controls["quality_trend"].shapes = _trend_shapes(history, color)
        controls["quality_range"].value = (
            f"{min(history)}~{max(history)}ms" if history else "—"
        )
        controls["quality_updated"].value = f"3초마다 갱신 · {state.measured_at} 갱신"
    else:
        controls["quality_latency"].value = "—"
        controls["quality_jitter"].value = "—"
        controls["quality_loss"].value = "—"
        controls["quality_trend"].shapes = _trend_shapes((), "#536474")
        controls["quality_range"].value = "—"


def apply_network_view_state(
    controls: dict[str, ft.Control],
    state: NetworkViewState,
    *,
    on_disconnect: Callable[[str], None],
    on_rename: Callable[[str], None],
    on_revoke: Callable[[str], None],
    on_unblock: Callable[[str], None],
    on_reconnect: Callable[[str], None],
    on_history: Callable[[str], None],
    on_remove: Callable[[str], None],
    on_approve: Callable[[str], None],
    on_reject: Callable[[str], None],
) -> None:
    """계산된 상태를 네트워크 관리 패널 컨트롤에 반영한다."""
    controls["server_addr_text"].value = state.server_addr_text or "—"
    controls["server_status_badge"].content = _build_status_badge(
        state.server_status_text,
        "#D8F4E3" if state.server_running else "#EDF0F3",
        "#1E6B45" if state.server_running else "#536474",
    )
    controls["server_status_badge"].border_radius = 5

    counts = controls["counts"]
    counts["online"].value = str(state.online_count)
    counts["offline"].value = str(state.offline_count)
    counts["pending"].value = str(state.pending_count)
    counts["blocked"].value = str(state.blocked_count)

    controls["pending_section"].visible = bool(state.pending_rows)
    controls["pending_count_badge"].content = _build_status_badge(
        f"{state.pending_count}대", "#FFE9C8", "#8A5A00"
    )
    controls["pending_column"].controls = [
        _build_pending_row(row, state.server_running, on_approve, on_reject)
        for row in state.pending_rows
    ]

    _apply_filter_chip_styles(
        controls["filter_chips"], controls.get("_filter_key", FILTER_ALL)
    )

    controls["device_list"].controls = [
        _build_device_row(
            row, on_disconnect, on_rename, on_revoke, on_unblock,
            on_reconnect, on_history, on_remove,
        )
        for row in state.device_rows
    ]
    controls["empty_text"].value = (
        "일치하는 장치가 없습니다." if state.empty_filtered else "아직 연결된 휴대폰이 없습니다."
    )
    controls["empty_box"].visible = state.empty_visible
    controls["empty_reset"].visible = state.empty_filtered

