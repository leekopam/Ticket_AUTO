"""네트워크 관리 탭 — 페어링 기기 목록·연결 상태·이름 변경·차단을 관리한다.

레이아웃은 상단 서버 상태 카드 + 승인 대기 섹션 + 기기 목록 + 선택 기기 최근 처리.
표시 상태 계산(build_network_view_state)과 컨트롤 적용(apply_network_view_state)을
분리해 뷰 로직을 pytest로 검증할 수 있게 한다. 티켓 업무 탭과 같은 패턴.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import flet as ft

from services.device_presence import (
    PRESENCE_OFFLINE,
    PRESENCE_ONLINE,
    PRESENCE_PENDING,
    PRESENCE_REVOKED,
    display_names,
    parse_seen_at,
    presence_state,
)
from services.pairing_service import DeviceInfo, PendingApproval
from views.work_log_flet_view import _ops_record_order_id

logger = logging.getLogger(__name__)

MAX_DEVICE_ROWS = 100
RECENT_OPS_LIMIT = 20

STATUS_BADGE = {
    PRESENCE_ONLINE: ("연결됨", "#D8F4E3", "#1E6B45"),
    PRESENCE_OFFLINE: ("연결 끊김", "#E5E5E5", "#5D6E82"),
    PRESENCE_PENDING: ("승인 대기", "#FFE9C8", "#8A5A00"),
    PRESENCE_REVOKED: ("차단됨", "#FBDCDC", "#A12622"),
}


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
    status_text: str
    status_bgcolor: str
    status_color: str
    last_seen_text: str
    processed_text: str
    can_revoke: bool
    is_selected: bool


@dataclass(frozen=True)
class RecentOpState:
    order_id: str
    state_text: str
    time_text: str


@dataclass(frozen=True)
class NetworkViewState:
    server_running: bool
    server_addr_text: str
    counts_text: str
    pending_rows: tuple[PendingRowState, ...]
    device_rows: tuple[DeviceRowState, ...]
    empty_visible: bool
    recent_rows: tuple[RecentOpState, ...]
    recent_title: str


def _format_last_seen(last_seen_at: str, now: float) -> str:
    """'YYYY-MM-DD HH:MM:SS'를 '방금 전/N분 전/N시간 전'으로 바꾼다."""
    seen = parse_seen_at(last_seen_at)
    if seen is None:
        return "기록 없음"
    delta = max(0.0, now - seen)
    if delta < 60:
        return "방금 전"
    if delta < 3600:
        return f"{int(delta // 60)}분 전"
    if delta < 86400:
        return f"{int(delta // 3600)}시간 전"
    return f"{int(delta // 86400)}일 전"


def _device_hashes(info: DeviceInfo) -> set[str]:
    """DeviceInfo가 노출하는 모든 해시(현재+과거)를 모은다."""
    return set(getattr(info, "device_ids", ()) or ())


def build_ops_device_counts(operations: list[dict[str, str]]) -> dict[str, int]:
    """작업 이력의 device_id별 처리 건수를 계산한다."""
    counts: dict[str, int] = {}
    for record in operations or []:
        device_id = str(record.get("device_id") or "").strip()
        if device_id:
            counts[device_id] = counts.get(device_id, 0) + 1
    return counts


def build_network_view_state(
    devices: list[DeviceInfo],
    pending: list[PendingApproval],
    operations: list[dict[str, str]],
    *,
    server_running: bool,
    server_addr: str,
    now: float,
    selected_record_id: str | None = None,
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

    online = 0
    device_rows: list[DeviceRowState] = []
    for info in devices[:MAX_DEVICE_ROWS]:
        is_pending = False  # pending ticket은 아직 레코드가 없으므로 별도 섹션에서 표시
        state = presence_state(info, now, pending=is_pending)
        badge_text, badge_bg, badge_color = STATUS_BADGE[state]
        if state == PRESENCE_ONLINE:
            online += 1
        processed = sum(counts.get(h, 0) for h in _device_hashes(info))
        device_rows.append(
            DeviceRowState(
                record_id=info.record_id,
                display_name=names[id(info)],
                reported_name=info.reported_name or "-",
                status_text=badge_text,
                status_bgcolor=badge_bg,
                status_color=badge_color,
                last_seen_text=_format_last_seen(info.last_seen_at, now),
                processed_text=f"처리 {processed}건",
                can_revoke=not info.revoked,
                is_selected=bool(selected_record_id) and info.record_id == selected_record_id,
            )
        )

    # 선택 기기의 최근 처리 이력
    recent_rows: list[RecentOpState] = []
    recent_title = "기기를 선택하면 최근 처리 내역을 볼 수 있습니다"
    if selected_record_id:
        selected = next((d for d in devices if d.record_id == selected_record_id), None)
        if selected is not None:
            hashes = _device_hashes(selected)
            recent_title = f"{names[id(selected)]}의 최근 처리"
            selected_ops = [
                r for r in reversed(operations or [])
                if str(r.get("device_id") or "").strip() in hashes
            ]
            for record in selected_ops[:RECENT_OPS_LIMIT]:
                recent_rows.append(
                    RecentOpState(
                        order_id=_ops_record_order_id(record) or "-",
                        state_text=str(record.get("state") or ""),
                        time_text=str(record.get("updated_at") or "")[5:16],
                    )
                )

    counts_text = f"연결됨 {online} / 전체 {len(devices)}"
    return NetworkViewState(
        server_running=server_running,
        server_addr_text=f"서버 주소: {server_addr}" if server_addr else "서버가 꺼져 있습니다",
        counts_text=counts_text,
        pending_rows=pending_rows,
        device_rows=tuple(device_rows),
        empty_visible=not devices,
        recent_rows=tuple(recent_rows),
        recent_title=recent_title,
    )


def _build_status_badge(text: str, bgcolor: str, color: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_600, color=color),
        bgcolor=bgcolor,
        border_radius=8,
        padding=ft.padding.symmetric(horizontal=8, vertical=3),
    )


def _build_pending_row(
    row: PendingRowState,
    on_approve: Callable[[str], None],
    on_reject: Callable[[str], None],
) -> ft.Container:
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Text(
                    f"{row.device_name} (등록된 기기 재페어링)" if row.known_device else row.device_name,
                    size=14,
                    expand=True,
                ),
                ft.FilledButton(
                    "승인",
                    on_click=lambda _e, t=row.pair_ticket: on_approve(t),
                    tooltip="이 기기의 연결을 승인합니다",
                ),
                ft.OutlinedButton(
                    "거절",
                    on_click=lambda _e, t=row.pair_ticket: on_reject(t),
                    tooltip="이 기기의 연결 요청을 거절합니다",
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        padding=ft.padding.symmetric(horizontal=14, vertical=8),
        border=ft.border.only(bottom=ft.border.BorderSide(1, "#EEEEEE")),
    )


def _build_device_row(
    row: DeviceRowState,
    on_select: Callable[[str], None],
    on_rename: Callable[[str], None],
    on_revoke: Callable[[str], None],
) -> ft.Container:
    actions: list[ft.Control] = [
        ft.IconButton(
            icon=getattr(ft, "Icons", ft.icons).EDIT_ROUNDED,
            icon_size=18,
            tooltip="이름 변경",
            on_click=lambda _e, rid=row.record_id: on_rename(rid),
            key=f"network_rename_{row.record_id}",
        ),
    ]
    if row.can_revoke:
        actions.append(
            ft.IconButton(
                icon=getattr(ft, "Icons", ft.icons).BLOCK_ROUNDED,
                icon_size=18,
                tooltip="차단 (연결을 끊고 이 기기의 접근을 막습니다)",
                on_click=lambda _e, rid=row.record_id: on_revoke(rid),
                key=f"network_revoke_{row.record_id}",
            )
        )
    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Text(row.display_name, size=14, weight=ft.FontWeight.W_600, color="#1F1F1F"),
                            ft.Text(
                                f"원래 이름: {row.reported_name} · 마지막 활동 {row.last_seen_text}",
                                size=12, color="#6B7787",
                            ),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                    expand=True,
                ),
                ft.Container(content=ft.Text(row.processed_text, size=12, color="#333333"), width=76),
                ft.Container(
                    content=_build_status_badge(row.status_text, row.status_bgcolor, row.status_color),
                    width=76,
                ),
                ft.Row(controls=actions, spacing=0, tight=True),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor="#EAF4F2" if row.is_selected else "#FFFFFF",
        padding=ft.padding.symmetric(horizontal=14, vertical=10),
        border=ft.border.only(bottom=ft.border.BorderSide(1, "#EEEEEE")),
        on_click=lambda _e, rid=row.record_id: on_select(rid),
        key=f"network_device_row_{row.record_id}",
        ink=True,
    )


def build_network_panel(link_button: ft.Control | None = None) -> dict[str, ft.Control]:
    """네트워크 관리 패널 컨트롤 묶음을 만든다. 내용은 apply_*로 채운다.

    link_button: 서버 상태 카드 오른쪽에 놓을 휴대폰 연결 버튼 (호출부가 주입).
    """
    server_status_text = ft.Text("", size=13, color="#333333")
    counts_text = ft.Text("", size=12, color="#6B7787")
    pending_column = ft.Column(spacing=0, tight=True)
    pending_section = ft.Container(
        content=ft.Column(
            controls=[ft.Text("승인 대기", size=14, weight=ft.FontWeight.W_600), pending_column],
            spacing=6,
            tight=True,
        ),
        bgcolor="#FFF8EC",
        border_radius=10,
        padding=12,
    )
    device_list = ft.Column(spacing=0, tight=True)
    empty_text = ft.Text("아직 연결된 휴대폰이 없습니다.", size=13, color="#8B97A8", visible=False)
    recent_title = ft.Text("", size=13, weight=ft.FontWeight.W_600)
    recent_list = ft.Column(spacing=0, tight=True)

    panel = ft.Container(
        expand=True,
        padding=ft.padding.symmetric(horizontal=24, vertical=20),
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("네트워크 관리", size=20, weight=ft.FontWeight.BOLD, color="#172235"),
                        counts_text,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(
                    content=ft.Row(
                        controls=[server_status_text, *([link_button] if link_button else [])],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor="#F7F8FA",
                    border_radius=10,
                    padding=12,
                ),
                pending_section,
                ft.Text("등록된 휴대폰", size=14, weight=ft.FontWeight.W_600),
                ft.Container(
                    content=device_list,
                    bgcolor="#FFFFFF",
                    border_radius=10,
                    border=ft.border.all(1, "#E5E9EF"),
                ),
                empty_text,
                ft.Divider(height=20, color="#00000000"),
                recent_title,
                ft.Container(
                    content=recent_list,
                    bgcolor="#FFFFFF",
                    border_radius=10,
                    border=ft.border.all(1, "#E5E9EF"),
                ),
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
    )
    return {
        "panel": panel,
        "server_status_text": server_status_text,
        "counts_text": counts_text,
        "pending_column": pending_column,
        "pending_section": pending_section,
        "device_list": device_list,
        "empty_text": empty_text,
        "recent_title": recent_title,
        "recent_list": recent_list,
    }


def apply_network_view_state(
    controls: dict[str, ft.Control],
    state: NetworkViewState,
    *,
    on_select: Callable[[str], None],
    on_rename: Callable[[str], None],
    on_revoke: Callable[[str], None],
    on_approve: Callable[[str], None],
    on_reject: Callable[[str], None],
) -> None:
    """계산된 상태를 네트워크 관리 패널 컨트롤에 반영한다."""
    controls["server_status_text"].value = state.server_addr_text
    controls["counts_text"].value = state.counts_text

    controls["pending_section"].visible = bool(state.pending_rows)
    controls["pending_column"].controls = [
        _build_pending_row(row, on_approve, on_reject) for row in state.pending_rows
    ]

    controls["device_list"].controls = [
        _build_device_row(row, on_select, on_rename, on_revoke) for row in state.device_rows
    ]
    controls["empty_text"].visible = state.empty_visible

    controls["recent_title"].value = state.recent_title
    controls["recent_list"].controls = [
        ft.Container(
            content=ft.Row(
                controls=[
                    ft.Text(row.order_id, size=12, expand=True, color="#333333"),
                    ft.Text(row.state_text, size=12, color="#6B7787"),
                    ft.Text(row.time_text, size=12, color="#8B97A8"),
                ]
            ),
            padding=ft.padding.symmetric(horizontal=14, vertical=6),
            border=ft.border.only(bottom=ft.border.BorderSide(1, "#F2F2F2")),
        )
        for row in state.recent_rows
    ] or [
        ft.Container(
            content=ft.Text("처리 내역이 없습니다.", size=12, color="#8B97A8"),
            padding=ft.padding.symmetric(horizontal=14, vertical=8),
        )
    ]
