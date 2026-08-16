"""Integrated Flet control dashboard for Ticket_AUTO."""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Callable

# 직접 실행 시 프로젝트 루트를 sys.path에 추가
sys.path.append(str(Path(__file__).parent.parent))

import flet as ft

import threading

logger = logging.getLogger(__name__)

from main import Application
from models.order_model import Order
from project_paths import (
    copy_data_file_to_managed_location,
    ensure_managed_data_file,
    resolve_project_path,
)
from services.excel_service import ExcelService
from services.receipt_print_pipeline import print_order_receipt, render_receipt_preview_base64
from services.receipt_settings_store import ReceiptSettingsStore
from services.scan_success_sound_service import (
    ScanSuccessSoundService,
    ScanSuccessSoundStateStore,
)
from services.ticket_runtime_manager import TicketRuntimeManager
from services.windows_camera_service import CameraDevice, WindowsCameraService
from views.settings_flet_view import (
    build_app_settings_panel,
    build_receipt_settings_panel,
    build_receipt_sidebar_settings_panel,
)

ICONS = getattr(ft, "Icons", ft.icons)
SEARCH_DEBOUNCE_SEC = 0.25
EXCEL_WATCH_INTERVAL_SEC = 0.5
CAMERA_SETTINGS_DRAWER_WIDTH = 500
CAMERA_SETTINGS_HANDLE_WIDTH = 28
DASHBOARD_SIDEBAR_WIDTH = 244
CAMERA_SETTINGS_OVERLAY_WIDTH = CAMERA_SETTINGS_DRAWER_WIDTH + CAMERA_SETTINGS_HANDLE_WIDTH
CAMERA_SETTINGS_OVERLAY_CLOSED_OFFSET_X = CAMERA_SETTINGS_DRAWER_WIDTH / CAMERA_SETTINGS_OVERLAY_WIDTH
ACCENT_PRIMARY = "#39C5BB"
ACCENT_PRIMARY_DARK = "#1C8C84"
ACCENT_PRIMARY_DEEP = "#145F59"
ACCENT_PRIMARY_SOFT = "#E9F8F6"
ACCENT_PRIMARY_SOFT_ALT = "#D7F3F0"
ACCENT_PRIMARY_SOFT_HOVER = "#C3ECE7"
ACCENT_PRIMARY_BORDER = "#A8E5DE"
ACCENT_PRIMARY_BORDER_STRONG = "#7FD6CE"
ACCENT_PRIMARY_PROGRESS_BG = "#CDEFEB"
DRAWER_SURFACE = "#DDE9F3"
DRAWER_BORDER = "#B7C8D8"
DRAWER_BORDER_STRONG = "#9EB3C5"
HANDLE_IDLE_BG = "#DCE4EC"
HANDLE_HOVER_BG = "#CCD8E2"
HANDLE_TEXT = "#32475A"
HANDLE_DIVIDER = "#A0B1C3"
HANDLE_BADGE_BG = "#F9FBFD"
HANDLE_BADGE_BORDER = "#B9C8D6"
HANDLE_BADGE_ICON = "#51687D"
STATUS_DANGER = "#D80000"
STATUS_DANGER_SOFT = "#FFE0E0"
STATUS_INFO = "#0000FF"
STATUS_INFO_SOFT = "#E6EAFF"
STATUS_WARNING = "#FFE211"
STATUS_WARNING_SOFT = "#FFF8C4"
STATUS_WARNING_TEXT = "#7A6500"
STATUS_PINK = "#FFC0CB"
STATUS_PINK_SOFT = "#FFE9EF"
STATUS_PINK_TEXT = "#A34B68"
VISIBLE_ORDER_STATUSES = ("결제완료", "거래종료")


def _coerce_picker_files(result: object) -> list[ft.FilePickerFile]:
    files = getattr(result, "files", None)
    if isinstance(files, list):
        return files
    if isinstance(result, list):
        return result
    return []


def _attach_page_service(page: ft.Page, service) -> None:
    registry = getattr(page, "_services", None)
    registered = getattr(registry, "_services", None)
    if isinstance(registered, list):
        registered.append(service)
        return

    services = getattr(page, "services", None)
    if isinstance(services, list):
        services.append(service)
        return

    register_service = getattr(registry, "register_service", None)
    if callable(register_service):
        register_service(service)
        return

    page.overlay.append(service)


def build_dashboard_snack_bar(message: str, *, success: bool) -> ft.SnackBar:
    """Build a readable floating snackbar for dashboard success/error feedback."""
    behavior_enum = getattr(ft, "SnackBarBehavior", None)
    behavior = getattr(behavior_enum, "FLOATING", None) if behavior_enum is not None else None
    return ft.SnackBar(
        content=ft.Text(
            message,
            color=ACCENT_PRIMARY_DEEP if success else STATUS_DANGER,
            weight=ft.FontWeight.W_600,
        ),
        bgcolor=ACCENT_PRIMARY_SOFT_ALT if success else STATUS_DANGER_SOFT,
        behavior=behavior,
    )


def compute_button_enabled(runtime_state: str) -> tuple[bool, bool, bool]:
    """Return (start_enabled, stop_enabled, relogin_enabled)."""
    if runtime_state in {"RUNNING", "RECOVERING"}:
        return False, True, True
    if runtime_state == "STARTING":
        return False, True, False
    if runtime_state == "STOPPING":
        return False, False, False
    if runtime_state == "ERROR":
        return True, False, False
    return True, False, False


def resolve_tab_content(
    tab_key: str,
    ticket_panel: ft.Control,
    receipt_panel: ft.Control,
) -> ft.Control:
    """Return panel for selected tab."""
    if tab_key == "ticket":
        return ticket_panel
    return receipt_panel


def should_auto_refresh_order_views(tab_key: str, runtime_state: str) -> bool:
    """Return True when ticket search results should refresh automatically.

    티켓 확인 탭에서는 런타임 상태와 무관하게 항상 자동 갱신한다.
    """
    return tab_key == "ticket"


def resolve_preserved_order_selection(
    previous_value: str | None,
    available_order_numbers: set[str],
) -> str | None:
    """Keep current dropdown selection only if it still exists after refresh."""
    if previous_value and previous_value in available_order_numbers:
        return previous_value
    return None


def read_file_mtime_ns(path: Path) -> int | None:
    """Return file mtime in ns, or None when the file is unavailable."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def has_file_timestamp_changed(
    previous_mtime_ns: int | None,
    current_mtime_ns: int | None,
) -> bool:
    """Detect changes after an initial workbook timestamp has been observed."""
    return (
        previous_mtime_ns is not None
        and current_mtime_ns is not None
        and current_mtime_ns != previous_mtime_ns
    )


def build_order_search_signature(
    keyword: str,
    filter_value: str,
    ticket_names: list[str],
    orders: list[Order],
    error_message: str = "",
    info_message: str = "",
    highlight_ticket_split: bool = False,
) -> tuple[object, ...]:
    """Build a comparable snapshot so unchanged refreshes do not redraw the UI."""
    return (
        keyword.strip(),
        filter_value,
        tuple(ticket_names),
        error_message.strip(),
        info_message.strip(),
        highlight_ticket_split,
        tuple(
            (
                order.order_number,
                order.name,
                order.phone,
                order.seat,
                tuple(order.goods or []),
                order.received_at or "",
                order.order_status or "",
            )
            for order in orders
        ),
    )


def split_order_goods(
    goods: list[str] | None,
    ticket_names: list[str] | set[str],
) -> tuple[list[str], list[str]]:
    """Split goods into general and ticket buckets with a shared matching rule."""
    normalized_ticket_names = {
        (name or "").strip().lower()
        for name in ticket_names
        if (name or "").strip()
    }
    general_goods: list[str] = []
    ticket_goods: list[str] = []

    for item in goods or []:
        clean_item = (item or "").strip()
        if not clean_item:
            continue
        normalized_item = clean_item.lower()
        if " x" in normalized_item:
            normalized_item = normalized_item.rsplit(" x", 1)[0].strip()

        is_ticket = normalized_item in normalized_ticket_names
        if not is_ticket and normalized_ticket_names:
            is_ticket = any(ticket_name in normalized_item for ticket_name in normalized_ticket_names)

        if is_ticket:
            ticket_goods.append(clean_item)
        else:
            general_goods.append(clean_item)

    return general_goods, ticket_goods


def should_refresh_search_results_from_watch(
    *,
    workbook_changed: bool,
    tab_key: str,
    runtime_state: str,
) -> bool:
    """엑셀 파일이 실제로 바뀐 경우에만 검색 자동 새로고침을 허용한다."""
    return workbook_changed and should_auto_refresh_order_views(tab_key, runtime_state)


def process_search_refresh_watch_tick(
    previous_mtime_ns: int | None,
    current_mtime_ns: int | None,
    *,
    tab_key: str,
    runtime_state: str,
) -> tuple[int | None, bool]:
    """엑셀 감시 루프 1회분의 mtime 갱신과 즉시 새로고침 여부를 계산한다."""
    workbook_changed = has_file_timestamp_changed(previous_mtime_ns, current_mtime_ns)
    next_mtime_ns = current_mtime_ns if current_mtime_ns is not None else previous_mtime_ns
    should_schedule_refresh = should_refresh_search_results_from_watch(
        workbook_changed=workbook_changed,
        tab_key=tab_key,
        runtime_state=runtime_state,
    )
    return next_mtime_ns, should_schedule_refresh


def format_filter_count_text(filter_value: str, total: int, received: int) -> str:
    """선택된 필터에 맞는 인원수 요약 문구를 반환한다."""
    safe_total = max(0, int(total))
    safe_received = min(max(0, int(received)), safe_total)
    safe_unreceived = safe_total - safe_received
    if filter_value == "전체":
        return f"{safe_total}명"
    if filter_value == "수령완료":
        return f"{safe_received} / {safe_total}명"
    return f"{safe_unreceived} / {safe_total}명"


def format_processed_count_text(processed_count: int) -> str:
    """누적 처리완료 카운트를 작은 상태 문구로 포맷한다."""
    return f"처리완료 누적 {max(0, int(processed_count))}명"


def load_processed_success_count(state_store: ScanSuccessSoundStateStore) -> int:
    """처리완료 누적 카운트를 안전하게 읽는다."""
    try:
        return max(0, int(state_store.load_success_count()))
    except Exception:
        logger.warning("처리완료 누적 카운트 로딩 실패", exc_info=True)
        return 0


def resolve_next_special_rule_progress(
    settings,
    *,
    current_count: int,
    service: ScanSuccessSoundService | None = None,
):
    """현재 처리완료 카운트 기준 다음 특수 규칙 진행 상태를 계산한다."""
    progress_service = service or ScanSuccessSoundService()
    return progress_service.describe_next_special_rule_progress(
        settings,
        current_count=current_count,
    )


def resolve_special_rule_progress_list(
    settings,
    *,
    current_count: int,
    service: ScanSuccessSoundService | None = None,
):
    """현재 처리완료 카운트 기준 특수 규칙 진행 상태 목록을 계산한다."""
    progress_service = service or ScanSuccessSoundService()
    return progress_service.describe_special_rule_progresses(
        settings,
        current_count=current_count,
    )


def format_next_special_rule_text(progress_state) -> NextSpecialRuleViewState:
    """다음 특수 규칙 요약 문구와 진행률, 목표 숫자, 타입 배지를 포맷한다."""
    if progress_state is None:
        return NextSpecialRuleViewState(
            trigger_type="",
            visible=False,
            title_text="다음 특수 규칙 없음",
            hint_text="설정된 N 번마다 / 특정 번호 규칙이 없습니다.",
            progress_value=0.0,
            target_text="-",
            tooltip_text="다음 특수 규칙이 아직 설정되지 않았습니다.",
            badge_text="",
            badge_icon=None,
            badge_bgcolor=STATUS_INFO_SOFT,
            badge_text_color="#59708F",
            card_bgcolor=STATUS_INFO_SOFT,
            card_border_color="#D6E4F7",
            progress_color=ACCENT_PRIMARY,
            progress_bgcolor=ACCENT_PRIMARY_PROGRESS_BG,
        )
    badge_text = "특정 번호" if progress_state.trigger_type == "specific_counts" else "N 번마다"
    badge_icon = ICONS.LABEL_ROUNDED if progress_state.trigger_type == "specific_counts" else ICONS.REPEAT_ROUNDED
    badge_bgcolor = STATUS_PINK_SOFT if progress_state.trigger_type == "specific_counts" else "#FFF1D6"
    badge_text_color = STATUS_PINK_TEXT if progress_state.trigger_type == "specific_counts" else "#B85D00"
    card_bgcolor = STATUS_PINK_SOFT if progress_state.trigger_type == "specific_counts" else "#FFF9EC"
    card_border_color = "#F2AEBF" if progress_state.trigger_type == "specific_counts" else "#FFCF80"
    progress_color = STATUS_PINK_TEXT if progress_state.trigger_type == "specific_counts" else "#FFA500"
    progress_bgcolor = "#FFD7E1" if progress_state.trigger_type == "specific_counts" else "#FFE4B3"
    sound_name = (getattr(progress_state, "sound_name", "") or progress_state.trigger_label).strip()
    return NextSpecialRuleViewState(
        trigger_type=progress_state.trigger_type,
        visible=True,
        title_text=sound_name,
        hint_text=f"다음 재생까지 {progress_state.remaining_count}명 · {progress_state.trigger_label}",
        progress_value=max(0.0, min(1.0, float(progress_state.progress_value))),
        target_text=str(progress_state.next_target_count),
        tooltip_text=(
            f"{sound_name}: 현재 {progress_state.current_count}명 처리완료, "
            f"다음 목표 {progress_state.next_target_count}명"
        ),
        badge_text=badge_text,
        badge_icon=badge_icon,
        badge_bgcolor=badge_bgcolor,
        badge_text_color=badge_text_color,
        card_bgcolor=card_bgcolor,
        card_border_color=card_border_color,
        progress_color=progress_color,
        progress_bgcolor=progress_bgcolor,
    )


def build_special_rule_progress_card(view_state: NextSpecialRuleViewState) -> ft.Container:
    """특수 규칙 진행 상태 카드를 렌더링한다."""
    is_every_n = view_state.trigger_type == "every_n"
    badge_icon = ft.Icon(
        view_state.badge_icon or ICONS.LABEL_ROUNDED,
        size=11,
        color=view_state.badge_text_color,
    )
    badge_text = ft.Text(
        view_state.badge_text,
        size=10,
        color=view_state.badge_text_color,
        weight=ft.FontWeight.W_600,
    )
    badge = ft.Container(
        visible=bool(view_state.badge_text),
        padding=ft.padding.symmetric(horizontal=8, vertical=4),
        bgcolor=view_state.badge_bgcolor,
        border=ft.border.all(1, "#FFB84D" if is_every_n else view_state.card_border_color),
        border_radius=999,
        content=ft.Row(
            controls=[badge_icon, badge_text],
            spacing=4,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
    target_badge = ft.Container(
        padding=ft.padding.symmetric(horizontal=10, vertical=5),
        bgcolor="#FFFFFF",
        border=ft.border.all(1, "#FFB84D" if is_every_n else view_state.card_border_color),
        border_radius=999,
        content=ft.Text(
            f"목표 {view_state.target_text}",
            size=11,
            color="#B85D00" if is_every_n else "#1F3F6C",
            weight=ft.FontWeight.W_700,
        ),
    )
    return ft.Container(
        visible=view_state.visible,
        padding=ft.padding.symmetric(horizontal=14, vertical=12),
        bgcolor=view_state.card_bgcolor,
        border=ft.border.all(1, view_state.card_border_color),
        border_radius=14,
        tooltip=view_state.tooltip_text,
        content=ft.Column(
            controls=[
                ft.Text(
                    view_state.title_text,
                    size=14,
                    color="#1F3F6C",
                    weight=ft.FontWeight.W_700,
                    no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Row(
                    controls=[
                        ft.Container(
                            expand=True,
                            content=ft.ProgressBar(
                                value=view_state.progress_value,
                                height=8,
                                color=view_state.progress_color,
                                bgcolor=view_state.progress_bgcolor,
                                border_radius=999,
                            ),
                        ),
                        target_badge,
                        badge,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Text(view_state.hint_text, size=12, color="#47627E", weight=ft.FontWeight.W_500),
            ],
                spacing=7,
            tight=True,
        ),
    )


def build_special_rule_progress_section(
    *,
    title_text: str,
    icon: object,
    header_bgcolor: str,
    header_text_color: str,
    cards: list[ft.Control],
) -> ft.Container:
    """특수 규칙을 타입별 섹션으로 묶어 렌더링한다."""
    card_rows = [
        ft.Row(
            controls=[
                ft.Container(expand=1, content=card)
                for card in cards[index:index + 3]
            ],
            spacing=8,
            wrap=False,
        )
        for index in range(0, len(cards), 3)
    ]
    return ft.Container(
        padding=ft.padding.symmetric(horizontal=10, vertical=10),
        bgcolor="#FFFFFF",
        border=ft.border.all(1, "#D9E6F7"),
        border_radius=16,
        content=ft.Column(
            controls=[
                ft.Container(
                    padding=ft.padding.symmetric(horizontal=10, vertical=6),
                    bgcolor=header_bgcolor,
                    border_radius=999,
                    content=ft.Row(
                        controls=[
                            ft.Icon(icon, size=13, color=header_text_color),
                            ft.Text(
                                title_text,
                                size=11,
                                color=header_text_color,
                                weight=ft.FontWeight.W_700,
                            ),
                        ],
                        spacing=6,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                ft.Column(controls=card_rows, spacing=8, tight=True),
            ],
            spacing=8,
            tight=True,
        ),
    )


def build_special_rule_progress_sections(
    view_states: list[NextSpecialRuleViewState],
) -> list[ft.Control]:
    """특수 규칙 카드 목록을 타입별 섹션으로 재구성한다."""
    section_specs = (
        ("specific_counts", "특정 번호", ICONS.LABEL_ROUNDED, STATUS_PINK_SOFT, STATUS_PINK_TEXT),
        ("every_n", "N 번마다", ICONS.REPEAT_ROUNDED, STATUS_WARNING_SOFT, STATUS_WARNING_TEXT),
    )
    sections: list[ft.Control] = []
    for trigger_type, title_text, icon, header_bgcolor, header_text_color in section_specs:
        cards = [
            build_special_rule_progress_card(view_state)
            for view_state in view_states
            if view_state.visible and view_state.trigger_type == trigger_type
        ]
        if not cards:
            continue
        sections.append(
            build_special_rule_progress_section(
                title_text=title_text,
                icon=icon,
                header_bgcolor=header_bgcolor,
                header_text_color=header_text_color,
                cards=cards,
            )
        )
    return sections


def resolve_order_search_feedback(
    result_count: int,
    error_message: str,
    info_message: str = "",
) -> tuple[str, str]:
    """검색 결과 영역에 표시할 상태 문구와 색상을 반환한다."""
    if error_message:
        return (
            f"주문 검색 실패: {error_message}. 엑셀 파일 상태를 확인한 뒤 다시 검색해주세요.",
            "#D14343",
        )
    if info_message:
        return (info_message, ACCENT_PRIMARY_DARK)
    if result_count == 0:
        return ("검색 결과가 없습니다.", "#777777")
    return ("", "#777777")


def compute_print_button_disabled(
    *,
    has_target: bool,
    print_in_progress: bool,
    blocked: bool = False,
) -> bool:
    """출력 대상 유무와 진행 상태에 따라 버튼 비활성화 여부를 계산한다."""
    return blocked or print_in_progress or not has_target


def create_dashboard_runtime_manager(*, camera_index: int = 0) -> TicketRuntimeManager:
    """대시보드 전용 런타임은 별도 Tk 주문창 없이 실행한다."""
    return TicketRuntimeManager(
        app_factory=partial(Application, show_order_window=False, camera_index=camera_index),
    )


def request_witchform_login_page(runtime_manager: TicketRuntimeManager) -> bool:
    """대시보드 버튼에서 로그인 페이지 열기 요청을 런타임으로 전달한다."""
    return runtime_manager.open_witchform_login_page()


@dataclass(frozen=True)
class NextSpecialRuleViewState:
    trigger_type: str
    visible: bool
    title_text: str
    hint_text: str
    progress_value: float
    target_text: str
    tooltip_text: str
    badge_text: str
    badge_icon: object | None
    badge_bgcolor: str
    badge_text_color: str
    card_bgcolor: str
    card_border_color: str
    progress_color: str
    progress_bgcolor: str


@dataclass(frozen=True)
class BuyerPanelState:
    name_text: str
    phone_text: str
    seat_text: str
    goods_text: str
    goods_items: tuple[str, ...]
    goods_visible: bool
    goods_hint_text: str
    ticket_text: str
    ticket_visible: bool
    received_text: str
    received_visible: bool


@dataclass(frozen=True)
class SearchResultRowState:
    order_number: str
    name: str
    phone: str
    seat: str
    goods_text: str
    goods_items: tuple[str, ...] = ()
    ticket_text: str = ""
    ticket_items: tuple[str, ...] = ()
    order_status_text: str = ""
    received_text: str = ""
    row_bg: str = "#FFFFFF"
    goods_highlight: bool = False
    ticket_highlight: bool = False


@dataclass(frozen=True)
class OrderSearchViewState:
    filter_count_text: str
    filtered_orders: tuple[Order, ...]
    row_states: tuple[SearchResultRowState, ...]
    dropdown_order_numbers: tuple[str, ...]
    preserved_order_value: str | None
    dropdown_disabled: bool
    feedback_message: str
    feedback_color: str
    search_blocked: bool
    search_signature: tuple[object, ...]


@dataclass(frozen=True)
class RuntimeControlsState:
    badge_bgcolor: str
    primary_text: str
    primary_icon: object
    primary_bgcolor: str
    primary_disabled: bool
    relogin_disabled: bool
    uses_stop_action: bool


@dataclass(frozen=True)
class RuntimeStatusViewState:
    state_text: str
    badge_bgcolor: str
    runtime_hint_text: str
    last_event_text: str
    controls_state: RuntimeControlsState


@dataclass(frozen=True)
class RuntimeEventViewState:
    status_view_state: RuntimeStatusViewState
    should_refresh_search: bool


@dataclass(frozen=True)
class SidebarTabState:
    content: ft.Control
    ticket_tab_bgcolor: str
    receipt_tab_bgcolor: str
    should_refresh_search: bool


@dataclass(frozen=True)
class BuyerEventViewState:
    order: Order
    panel_state: BuyerPanelState
    search_blocked: bool
    buyer_detail_visible: bool
    buyer_empty_hint_visible: bool


def build_buyer_panel_state(
    order: Order,
    ticket_names: list[str] | set[str],
) -> BuyerPanelState:
    """구매자 패널에 필요한 텍스트/가시성 상태를 계산한다."""
    general_goods, ticket_goods = split_order_goods(order.goods, ticket_names)
    return BuyerPanelState(
        name_text=f"주문자명: {order.name}",
        phone_text=f"연락처: {order.phone}",
        seat_text=f"좌석번호: {order.seat}",
        goods_text="\n".join(general_goods),
        goods_items=tuple(general_goods),
        goods_visible=bool(general_goods),
        goods_hint_text="" if general_goods else "별도 구매 상품이 없습니다.",
        ticket_text="티켓: " + ", ".join(ticket_goods) if ticket_goods else "",
        ticket_visible=bool(ticket_goods),
        received_text=f"수령완료: {order.received_at}" if order.is_received else "",
        received_visible=order.is_received,
    )


def build_buyer_goods_card_controls(goods_items: tuple[str, ...]) -> list[ft.Control]:
    def _split_goods_display_item(item: str) -> tuple[str, str]:
        text = (item or "").strip()
        if not text:
            return "", ""
        match = re.match(r"^(.*?)(?:\s*[xX×]\s*(\d+))?$", text)
        if not match:
            return text, ""
        name_text = (match.group(1) or "").strip() or text
        quantity_value = (match.group(2) or "").strip()
        return name_text, (f"{quantity_value}개" if quantity_value else "")

    cards: list[ft.Control] = []
    for item in goods_items:
        item_name, quantity_text = _split_goods_display_item(item)
        cards.append(
            ft.Container(
                bgcolor="#F7FAFF",
                border=ft.border.all(1, "#D9E6F7"),
                border_radius=14,
                padding=ft.padding.symmetric(horizontal=12, vertical=11),
                content=ft.Row(
                    controls=[
                        ft.Container(
                            width=28,
                            height=28,
                            border_radius=14,
                            bgcolor="#E7F0FF",
                            alignment=ft.alignment.center,
                            content=ft.Text("•", size=16, color=ACCENT_PRIMARY, weight=ft.FontWeight.BOLD),
                        ),
                        ft.Column(
                            controls=[
                                ft.Text(
                                    item_name,
                                    size=13,
                                    color="#22324A",
                                    weight=ft.FontWeight.W_600,
                                    max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                            expand=True,
                            spacing=2,
                        ),
                        ft.Container(
                            visible=bool(quantity_text),
                            bgcolor="#EAF2FF",
                            border=ft.border.all(1, "#C8DBF6"),
                            border_radius=999,
                            padding=ft.padding.symmetric(horizontal=9, vertical=5),
                            content=ft.Text(
                                quantity_text,
                                size=11,
                                color="#1F5FCE",
                                weight=ft.FontWeight.BOLD,
                            ),
                        ),
                    ],
                    spacing=12,
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
        )
    return cards


def build_search_result_row_state(
    order: Order,
    ticket_names: list[str] | set[str],
    row_index: int,
    *,
    highlight_ticket_split: bool = False,
) -> SearchResultRowState:
    """검색 결과 행에 필요한 텍스트/배경 상태를 계산한다."""
    general_goods, ticket_goods = split_order_goods(order.goods, ticket_names)
    return SearchResultRowState(
        order_number=order.order_number,
        name=order.name,
        phone=order.phone,
        seat=order.seat,
        goods_text="\n".join(general_goods) if general_goods else "-",
        goods_items=tuple(general_goods),
        ticket_text="\n".join(ticket_goods) if ticket_goods else "-",
        ticket_items=tuple(ticket_goods),
        order_status_text=(order.order_status or "").strip() or "-",
        received_text=order.received_at if order.received_at else "-",
        row_bg="#FFFFFF" if row_index % 2 == 0 else "#FAFAFA",
        goods_highlight=highlight_ticket_split and bool(general_goods),
        ticket_highlight=highlight_ticket_split and bool(ticket_goods),
    )


def build_order_search_view_state(
    keyword: str,
    filter_value: str,
    orders: list[Order],
    ticket_names: list[str] | set[str],
    previous_order_value: str | None,
    *,
    error_message: str = "",
    info_message: str = "",
    highlight_ticket_split: bool = False,
) -> OrderSearchViewState:
    """검색 결과 영역에 필요한 필터/피드백/행 상태를 계산한다."""
    visible_orders = [
        order
        for order in orders
        if (order.order_status or "").strip() in VISIBLE_ORDER_STATUSES
    ]
    filtered_orders = visible_orders
    if filter_value in VISIBLE_ORDER_STATUSES:
        filtered_orders = [
            order
            for order in visible_orders
            if (order.order_status or "").strip() == filter_value
        ]
    filter_count = f"표시 {len(filtered_orders)}건"

    row_states = tuple(
        build_search_result_row_state(
            order,
            ticket_names,
            row_index,
            highlight_ticket_split=highlight_ticket_split,
        )
        for row_index, order in enumerate(filtered_orders)
    )
    dropdown_order_numbers = tuple(order.order_number for order in filtered_orders)
    search_blocked = bool(error_message)
    feedback_message, feedback_color = resolve_order_search_feedback(
        len(filtered_orders),
        error_message,
        info_message,
    )

    return OrderSearchViewState(
        filter_count_text=filter_count,
        filtered_orders=tuple(filtered_orders),
        row_states=row_states,
        dropdown_order_numbers=dropdown_order_numbers,
        preserved_order_value=resolve_preserved_order_selection(
            previous_order_value,
            set(dropdown_order_numbers),
        ),
        dropdown_disabled=not bool(dropdown_order_numbers) or search_blocked,
        feedback_message=feedback_message,
        feedback_color=feedback_color,
        search_blocked=search_blocked,
        search_signature=build_order_search_signature(
            keyword,
            filter_value,
            list(ticket_names),
            filtered_orders,
            error_message=error_message,
            info_message=info_message,
            highlight_ticket_split=highlight_ticket_split,
        ),
    )


def build_order_search_panel(
    *,
    search_field: ft.Control,
    filter_dropdown: ft.Control,
    filter_count_text: ft.Text,
    on_search: Callable[..., None],
    btn_import_data: ft.Control,
    btn_refresh: ft.Control,
    search_feedback_text: ft.Text,
    search_result_header: ft.Control,
    search_result_list: ft.Control,
) -> ft.Container:
    """주문 검색 툴바와 결과 영역 레이아웃을 조립한다."""
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("주문 검색", weight=ft.FontWeight.BOLD, size=16),
                ft.Row(
                    controls=[
                        search_field,
                        filter_dropdown,
                        filter_count_text,
                        ft.ElevatedButton(
                            "검색",
                            icon=ICONS.SEARCH_ROUNDED,
                            on_click=on_search,
                            style=ft.ButtonStyle(
                                bgcolor=ACCENT_PRIMARY,
                                color="#FFFFFF",
                                shape=ft.RoundedRectangleBorder(radius=8),
                            ),
                        ),
                        btn_import_data,
                        btn_refresh,
                    ],
                    spacing=8,
                ),
                search_feedback_text,
                ft.Container(
                    content=ft.Column(
                        controls=[
                            search_result_header,
                            search_result_list,
                        ],
                        spacing=0,
                    ),
                    bgcolor="#FFFFFF",
                    border=ft.border.all(1, "#D2D2D2"),
                    border_radius=8,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    height=500,
                ),
            ],
            spacing=8,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        margin=ft.margin.only(top=14),
    )


def apply_camera_frame_state(camera_view: ft.Image, b64_str: str) -> None:
    """카메라 프레임 표시 상태를 한 번에 갱신한다."""
    camera_view.src_base64 = b64_str
    camera_view.visible = True


def build_ticket_dashboard_panel(
    *,
    top_controls_col: ft.Control,
    buyer_info_panel: ft.Control,
    camera_container: ft.Control,
    special_rule_progress_panel: ft.Control,
    order_search_panel: ft.Control,
) -> ft.Container:
    """티켓 확인 탭의 상단 제어, 구매자/카메라, 검색 영역 레이아웃을 조립한다."""
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Container(
                    content=top_controls_col,
                    padding=ft.padding.only(bottom=10, right=10),
                    border=ft.border.only(bottom=ft.border.BorderSide(1, "#E0E0E0")),
                    margin=ft.margin.only(bottom=10),
                ),
                ft.Row(
                    controls=[
                        buyer_info_panel,
                        camera_container,
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                special_rule_progress_panel,
                order_search_panel,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
        padding=ft.padding.all(8),
        expand=True,
    )


def build_camera_focus_panel(
    *,
    on_close: Callable[[ft.ControlEvent], None],
    camera_selector_row: ft.Control | None = None,
    focus_mode_dropdown: ft.Control,
    manual_focus_value_field: ft.Control,
    capability_badge: ft.Control | None = None,
) -> ft.Container:
    """Build the advanced camera focus settings card shown in the slide panel."""
    controls = [
        ft.Text("카메라 초점 기능", size=18, weight=ft.FontWeight.BOLD, color="#172235"),
        ft.Text(
            "현재 스캔용 웹캠의 초점 모드와 수동 값을 조정합니다.",
            size=12,
            color="#6D7C92",
        ),
        ft.Container(
            height=1,
            bgcolor="#E4EDF7",
            border_radius=999,
        ),
    ]
    if camera_selector_row is not None:
        controls.append(camera_selector_row)
    if capability_badge is not None:
        controls.append(capability_badge)
    controls.extend([focus_mode_dropdown, manual_focus_value_field])
    return ft.Container(
        bgcolor="#FFFFFF",
        border_radius=22,
        border=ft.border.all(1, "#DDE8F5"),
        padding=ft.padding.all(18),
        content=ft.Column(
            controls=controls,
            spacing=14,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )


def apply_camera_focus_input_tone(*controls: ft.Control) -> None:
    """Apply a shared soft-blue field chrome to camera focus controls."""
    for control in controls:
        setattr(control, "filled", True)
        setattr(control, "fill_color", "#F3FCFB")
        setattr(control, "bgcolor", "#F3FCFB")
        setattr(control, "border_color", "#CBEAE6")
        setattr(control, "focused_border_color", ACCENT_PRIMARY)
        setattr(control, "content_padding", ft.padding.symmetric(horizontal=14, vertical=12))
        setattr(control, "label_style", ft.TextStyle(size=12, color="#64748B"))
        setattr(control, "text_style", ft.TextStyle(size=14, color="#1D1D1D"))
        hint_style = getattr(control, "hint_style", None)
        if hint_style is not None or hasattr(control, "hint_style"):
            setattr(control, "hint_style", ft.TextStyle(size=13, color="#8A97AA"))


def build_camera_focus_capability_badge(
    *,
    text: str,
    visible: bool = False,
) -> ft.Container:
    """Build an inline capability warning badge for unsupported manual focus cameras."""
    return ft.Container(
        visible=visible,
        bgcolor="#FFF7E8",
        border=ft.border.all(1, "#F2D39A"),
        border_radius=16,
        padding=ft.padding.symmetric(horizontal=12, vertical=10),
        content=ft.Row(
            controls=[
                ft.Container(
                    width=28,
                    height=28,
                    border_radius=14,
                    bgcolor="#FFE7B8",
                    alignment=ft.alignment.center,
                    content=ft.Icon(ICONS.INFO_OUTLINE_ROUNDED, size=16, color="#8C5A00"),
                ),
                ft.Text(
                    text,
                    size=12,
                    color="#8C5A00",
                    expand=True,
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def build_settings_sidebar_placeholder_panel(
    *,
    title: str,
    description: str,
) -> ft.Container:
    """Build a placeholder section card for not-yet-implemented settings groups."""
    return ft.Container(
        bgcolor="#FFFFFF",
        border_radius=22,
        border=ft.border.all(1, "#DDE8F5"),
        padding=ft.padding.all(18),
        content=ft.Column(
            controls=[
                ft.Text(title, size=18, weight=ft.FontWeight.BOLD, color="#172235"),
                ft.Text(description, size=12, color="#6D7C92"),
                ft.Container(height=1, bgcolor="#E4EDF7", border_radius=999),
                ft.Container(
                    expand=True,
                    border_radius=18,
                    bgcolor="#F8FBFF",
                    border=ft.border.all(1, "#E7EEF8"),
                    padding=ft.padding.all(16),
                    content=ft.Column(
                        controls=[
                            ft.Icon(ICONS.RECEIPT_LONG_ROUNDED, size=34, color="#89A4C9"),
                            ft.Text(
                                "영수증 양식 관련 설정은 아직 준비 중입니다.",
                                size=13,
                                color="#62758F",
                            ),
                        ],
                        spacing=10,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    alignment=ft.alignment.center,
                ),
            ],
            spacing=14,
            expand=True,
        ),
    )


def build_camera_focus_drawer(
    *,
    is_open: bool,
    panel_content: ft.Control,
    on_close: Callable[[ft.ControlEvent], None] | None = None,
    title: str = "설정",
    width: int = CAMERA_SETTINGS_DRAWER_WIDTH,
) -> ft.Container:
    """Build a full-height right settings sidebar shell."""
    controls: list[ft.Control] = [
        ft.Row(
            controls=[
                ft.Text(title, size=22, weight=ft.FontWeight.BOLD, color="#172235"),
                ft.Container(expand=True),
                    ft.IconButton(
                        icon=ICONS.CLOSE_ROUNDED,
                        icon_color=HANDLE_TEXT,
                        tooltip="설정 사이드바 닫기",
                        on_click=on_close,
                        style=ft.ButtonStyle(
                            bgcolor="#EDF3F8",
                            shape=ft.RoundedRectangleBorder(radius=999),
                        ),
                    ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    ]
    controls.append(
        ft.Container(
            expand=True,
            content=panel_content,
        )
    )
    return ft.Container(
        width=width,
        right=0,
        top=0,
        bottom=0,
        opacity=1.0 if is_open else 0.0,
        offset=ft.Offset(0, 0) if is_open else ft.Offset(1.08, 0),
        animate_offset=ft.Animation(260, ft.AnimationCurve.EASE_IN_OUT_CUBIC),
        animate_opacity=ft.Animation(220, ft.AnimationCurve.EASE_IN_OUT),
        bgcolor=DRAWER_SURFACE,
        border=ft.border.all(1, DRAWER_BORDER),
        padding=ft.padding.only(left=18, top=22, right=18, bottom=22),
        shadow=ft.BoxShadow(
            blur_radius=20,
            color=ft.colors.with_opacity(0.08, "#0F172A"),
            offset=ft.Offset(-4, 0),
        ),
        content=ft.Column(
            controls=controls,
            spacing=16,
            expand=True,
        ),
    )


def build_camera_focus_side_handle(
    *,
    on_open: Callable[[ft.ControlEvent], None],
    on_hover: Callable[[ft.ControlEvent], None] | None = None,
) -> ft.Container:
    """Build the right-edge handle that opens the camera focus drawer."""
    return ft.Container(
        right=0,
        top=332,
        width=CAMERA_SETTINGS_HANDLE_WIDTH,
        height=116,
        bgcolor=HANDLE_IDLE_BG,
        border=ft.border.all(1, DRAWER_BORDER_STRONG),
        border_radius=ft.border_radius.only(top_left=14, bottom_left=14),
        padding=ft.padding.symmetric(horizontal=3, vertical=8),
        ink=True,
        on_click=on_open,
        on_hover=on_hover,
        scale=1.0,
        opacity=0.94,
        offset=ft.Offset(0, 0),
        animate_scale=ft.Animation(180, ft.AnimationCurve.EASE_IN_OUT_CUBIC),
        animate_opacity=ft.Animation(180, ft.AnimationCurve.EASE_IN_OUT),
        animate_offset=ft.Animation(260, ft.AnimationCurve.EASE_IN_OUT_CUBIC),
        shadow=ft.BoxShadow(
            blur_radius=10,
            color=ft.colors.with_opacity(0.06, "#0F172A"),
            offset=ft.Offset(-2, 3),
        ),
        content=ft.Column(
            controls=[
                ft.Container(
                    width=18,
                    height=18,
                    border_radius=9,
                    bgcolor=HANDLE_BADGE_BG,
                    border=ft.border.all(1, HANDLE_BADGE_BORDER),
                    alignment=ft.alignment.center,
                    content=ft.Icon(
                        ICONS.TUNE_ROUNDED,
                        color=HANDLE_BADGE_ICON,
                        size=10,
                    ),
                ),
                ft.Container(width=10, height=1, bgcolor=HANDLE_DIVIDER, border_radius=999),
                ft.Text("설", size=12, weight=ft.FontWeight.BOLD, color=HANDLE_TEXT),
                ft.Text("정", size=12, weight=ft.FontWeight.BOLD, color=HANDLE_TEXT),
            ],
            spacing=5,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        tooltip="설정 열기",
    )


def build_dashboard_overlay_host(
    *,
    content_host: ft.Control,
    overlay_drawer: ft.Control,
    side_handle: ft.Control,
) -> ft.Stack:
    """Build the main dashboard overlay stack with a unified right-edge settings group."""
    overlay_group = ft.Stack(
        width=CAMERA_SETTINGS_OVERLAY_WIDTH,
        right=0,
        top=0,
        bottom=0,
        clip_behavior=ft.ClipBehavior.NONE,
        animate_offset=ft.Animation(260, ft.AnimationCurve.EASE_IN_OUT_CUBIC),
        animate_opacity=ft.Animation(220, ft.AnimationCurve.EASE_IN_OUT),
        controls=[
            overlay_drawer,
            side_handle,
        ],
    )
    return ft.Stack(
        expand=True,
        fit=ft.StackFit.EXPAND,
        clip_behavior=ft.ClipBehavior.NONE,
        controls=[
            content_host,
            overlay_group,
        ],
    )


def build_dashboard_sidebar(
    *,
    btn_ticket_tab: ft.Control,
    btn_receipt_tab: ft.Control,
) -> ft.Container:
    """좌측 사이드바 레이아웃을 조립한다."""
    return ft.Container(
        width=DASHBOARD_SIDEBAR_WIDTH,
        bgcolor="#F5F6F8",
        border=ft.border.only(right=ft.BorderSide(1, "#D9E1EA")),
        padding=ft.padding.only(left=18, right=16, top=22, bottom=18),
        content=ft.Column(
            controls=[
                ft.Container(
                    padding=ft.padding.only(left=6, top=6, bottom=4),
                    content=ft.Text("Magical Play", size=28, weight=ft.FontWeight.W_700, color="#172235"),
                ),
                ft.Container(height=14),
                btn_ticket_tab,
                btn_receipt_tab,
                ft.Container(expand=True),
                ft.Container(
                    padding=ft.padding.only(left=6, bottom=2),
                    content=ft.Text("v1 Control Center", color="#8B97A8", size=12),
                ),
            ],
            spacing=9,
        ),
    )


def build_settings_dialog(
    *,
    app_settings_panel: ft.Control,
    on_close: Callable[[ft.ControlEvent], None],
) -> ft.AlertDialog:
    """설정 다이얼로그 레이아웃을 조립한다."""
    return ft.AlertDialog(
        title=ft.Text("설정", size=20, weight=ft.FontWeight.BOLD),
        content=ft.Container(
            width=1120,
            height=760,
            content=app_settings_panel,
        ),
        actions=[
            ft.TextButton("닫기", on_click=on_close)
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )


def build_receipt_preview_dialog(
    *,
    preview_items: list[tuple[str, str]],
    on_close: Callable[[ft.ControlEvent], None],
) -> ft.AlertDialog:
    """영수증 인쇄 미리보기 다이얼로그를 생성한다.

    Args:
        preview_items: (라벨, base64) 튜플 리스트. 상품 영수증 포함 시 2장.
    """
    def _preview_type_style(label: str) -> tuple[str, str, str, str]:
        normalized = (label or "").strip()
        if normalized == "상품 영수증":
            return ("상품 영수증", STATUS_PINK_SOFT, STATUS_PINK_TEXT, "#F5CAD6")
        return ("영수증", ACCENT_PRIMARY_SOFT, ACCENT_PRIMARY_DEEP, ACCENT_PRIMARY_BORDER)

    controls: list[ft.Control] = []
    for idx, (label, b64) in enumerate(preview_items):
        if idx > 0:
            controls.append(ft.Divider(height=18, color="transparent"))
        preview_label, badge_bg, badge_fg, card_border = _preview_type_style(label)
        controls.append(
            ft.Container(
                border=ft.border.all(1, card_border),
                border_radius=18,
                bgcolor="#FFFFFF",
                padding=ft.padding.symmetric(horizontal=14, vertical=14),
                content=ft.Column(
                    controls=[
                        ft.Row(
                            controls=[
                                ft.Container(
                                    bgcolor=badge_bg,
                                    border_radius=999,
                                    padding=ft.padding.symmetric(horizontal=10, vertical=6),
                                    content=ft.Text(
                                        preview_label,
                                        size=12,
                                        weight=ft.FontWeight.BOLD,
                                        color=badge_fg,
                                    ),
                                ),
                                ft.Text(
                                    "현재 미리보는 양식",
                                    size=12,
                                    color="#667085",
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.CENTER,
                            spacing=10,
                        ),
                        ft.Text(
                            preview_label,
                            size=18,
                            weight=ft.FontWeight.BOLD,
                            color="#101828",
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Text(
                            "실제 출력 전에 현재 레이아웃을 확인할 수 있습니다.",
                            size=12,
                            color="#667085",
                            text_align=ft.TextAlign.CENTER,
                        ),
                        ft.Container(
                            margin=ft.margin.only(top=8),
                            content=ft.Image(src_base64=b64, fit=ft.ImageFit.CONTAIN, width=400),
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=6,
                ),
            )
        )

    title_suffix = f" ({len(preview_items)}장)" if len(preview_items) > 1 else ""
    return ft.AlertDialog(
        title=ft.Text(f"영수증 미리보기{title_suffix}", size=18, weight=ft.FontWeight.BOLD),
        content=ft.Container(
            width=452,
            height=700,
            content=ft.Column(
                controls=controls,
                scroll=ft.ScrollMode.AUTO,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ),
        actions=[
            ft.TextButton("닫기", on_click=on_close),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )


def build_dashboard_shell(
    *,
    sidebar: ft.Control,
    content_host: ft.Control,
) -> ft.Row:
    """사이드바와 본문 호스트를 포함한 대시보드 메인 셸을 조립한다."""
    return ft.Row(
        controls=[
            sidebar,
            ft.Container(
                expand=True,
                bgcolor="#ECECEC",
                content=content_host,
            ),
        ],
        spacing=0,
        expand=True,
    )


def bootstrap_dashboard_page(
    *,
    runtime_manager: TicketRuntimeManager,
    page: ft.Page,
    current_tab: dict[str, str],
    ticket_panel: ft.Control,
    receipt_settings_panel: ft.Control,
    content_host: ft.Control,
    shell_content: ft.Control | None = None,
    btn_ticket_tab: ft.Control,
    btn_receipt_tab: ft.Control,
    sidebar: ft.Control,
    btn_relogin: ft.Control,
    btn_start_stop: ft.ElevatedButton,
    on_start: Callable[[ft.ControlEvent], None],
    on_stop: Callable[[ft.ControlEvent], None],
    set_tab: Callable[[str, bool], None],
    on_runtime_event: Callable[[str, str, str], None],
    watch_excel_changes: Callable[[], None],
    cancel_scheduled_search_refresh: Callable[[], None],
    closing_event: threading.Event,
) -> None:
    """Apply initial dashboard wiring, lifecycle hooks, and shell mount."""
    apply_runtime_controls_state(
        build_runtime_controls_state("IDLE"),
        btn_relogin=btn_relogin,
        btn_start_stop=btn_start_stop,
        on_start=on_start,
        on_stop=on_stop,
    )
    apply_sidebar_tab_view_state(
        build_sidebar_tab_state(
            current_tab["value"],
            ticket_panel,
            receipt_settings_panel,
        ),
        current_tab=current_tab,
        tab_key=current_tab["value"],
        content_host=content_host,
        btn_ticket_tab=btn_ticket_tab,
        btn_receipt_tab=btn_receipt_tab,
    )

    page.add(
        build_dashboard_shell(
            sidebar=sidebar,
            content_host=shell_content or content_host,
        )
    )

    set_tab("ticket", push_update=True)
    runtime_manager.subscribe(on_runtime_event)
    threading.Thread(target=watch_excel_changes, daemon=True).start()

    def on_window_event(event: ft.WindowEvent) -> None:
        if event.data == "close":
            if closing_event.is_set():
                return
            closing_event.set()
            cancel_scheduled_search_refresh()
            runtime_manager.unsubscribe(on_runtime_event)

            def _stop_and_close() -> None:
                try:
                    runtime_manager.stop(timeout_sec=4.0)
                finally:
                    call_page_from_thread(page, lambda: page.window.destroy())

            threading.Thread(target=_stop_and_close, daemon=True).start()

    page.window.on_event = on_window_event


def dispatch_camera_frame_update(
    page: ft.Page,
    camera_view: ft.Image,
    b64_str: str,
    closing_event: threading.Event | None = None,
) -> None:
    """카메라 프레임 UI 반영을 UI 스레드 경계 안에서 처리한다."""

    def _apply_frame() -> None:
        apply_camera_frame_state(camera_view, b64_str)
        safe_page_update(camera_view, closing_event)

    call_page_from_thread(page, _apply_frame, closing_event)


def dispatch_runtime_event_update(
    page: ft.Page,
    event_view_state: RuntimeEventViewState,
    apply_status_update: Callable[[RuntimeStatusViewState], None],
    refresh_search_results: Callable[[], None],
    closing_event: threading.Event | None = None,
) -> None:
    """런타임 이벤트의 UI 반영과 검색 갱신 배선을 한 곳에서 처리한다."""

    def update_ui() -> None:
        apply_status_update(event_view_state.status_view_state)
        if event_view_state.should_refresh_search:
            refresh_search_results()
        safe_page_update(page, closing_event)

    call_page_from_thread(page, update_ui, closing_event)


def load_ticket_product_names(settings_store: ReceiptSettingsStore) -> list[str]:
    """설정 저장소에서 티켓 상품명 목록을 안전하게 읽는다."""
    try:
        receipt_settings = settings_store.load()
    except Exception:
        return []
    return list(getattr(receipt_settings, "ticket_product_names", []) or [])


def build_runtime_controls_state(runtime_state: str) -> RuntimeControlsState:
    """런타임 상태에 따른 배지/컨트롤 버튼 상태를 계산한다."""
    start_enabled, stop_enabled, relogin_enabled = compute_button_enabled(runtime_state)
    badge_bgcolor = {
        "RUNNING": "#D8F4E3",
        "RECOVERING": STATUS_WARNING_SOFT,
        "ERROR": STATUS_DANGER_SOFT,
        "STOPPING": "#E5E5E5",
        "STARTING": STATUS_WARNING_SOFT,
    }.get(runtime_state, STATUS_WARNING_SOFT)

    if stop_enabled:
        return RuntimeControlsState(
            badge_bgcolor=badge_bgcolor,
            primary_text="중지",
            primary_icon=ICONS.STOP_CIRCLE_ROUNDED,
            primary_bgcolor=STATUS_DANGER,
            primary_disabled=False,
            relogin_disabled=not relogin_enabled,
            uses_stop_action=True,
        )

    return RuntimeControlsState(
        badge_bgcolor=badge_bgcolor,
        primary_text="티켓 확인 시작",
        primary_icon=ICONS.PLAY_ARROW_ROUNDED,
        primary_bgcolor=ACCENT_PRIMARY,
        primary_disabled=not start_enabled,
        relogin_disabled=not relogin_enabled,
        uses_stop_action=False,
    )


def build_runtime_status_view_state(
    runtime_state: str,
    message: str,
    timestamp: str,
) -> RuntimeStatusViewState:
    """런타임 상태 카드에 필요한 텍스트/버튼 상태를 계산한다."""
    controls_state = build_runtime_controls_state(runtime_state)
    return RuntimeStatusViewState(
        state_text=runtime_state,
        badge_bgcolor=controls_state.badge_bgcolor,
        runtime_hint_text=message,
        last_event_text=f"마지막 이벤트: {timestamp}",
        controls_state=controls_state,
    )


def build_runtime_event_view_state(
    tab_key: str,
    runtime_state: str,
    message: str,
    timestamp: str,
) -> RuntimeEventViewState:
    """런타임 이벤트 수신 시 필요한 상태 표시와 새로고침 여부를 계산한다."""
    return RuntimeEventViewState(
        status_view_state=build_runtime_status_view_state(runtime_state, message, timestamp),
        should_refresh_search=should_auto_refresh_order_views(tab_key, runtime_state),
    )


def build_sidebar_tab_state(
    tab_key: str,
    ticket_panel: ft.Control,
    receipt_panel: ft.Control,
) -> SidebarTabState:
    """사이드바 탭 전환에 필요한 패널/스타일 상태를 계산한다."""
    active_bg = ACCENT_PRIMARY_SOFT
    inactive_bg = "#00000000"
    return SidebarTabState(
        content=resolve_tab_content(tab_key, ticket_panel, receipt_panel),
        ticket_tab_bgcolor=active_bg if tab_key == "ticket" else inactive_bg,
        receipt_tab_bgcolor=active_bg if tab_key == "receipt" else inactive_bg,
        should_refresh_search=tab_key == "ticket",
    )


def build_sidebar_nav_button_style(*, is_active: bool, is_hovered: bool) -> ft.ButtonStyle:
    active_bg = ACCENT_PRIMARY_SOFT
    hover_bg = "#F2FCFB"
    idle_bg = "#00000000"
    return ft.ButtonStyle(
        bgcolor=active_bg if is_active else (hover_bg if is_hovered else idle_bg),
        color=ACCENT_PRIMARY_DEEP if is_active else ("#1A5F5A" if is_hovered else "#263547"),
        side=(
            ft.border.all(1, ACCENT_PRIMARY_BORDER)
            if is_active
            else (ft.border.all(1, "#D8EEEB") if is_hovered else ft.border.all(1, "#00000000"))
        ),
        shape=ft.RoundedRectangleBorder(radius=12),
        padding=ft.padding.symmetric(horizontal=16, vertical=13),
    )


def apply_sidebar_tab_view_state(
    tab_state: SidebarTabState,
    *,
    current_tab: dict[str, str],
    tab_key: str,
    content_host: ft.Control,
    btn_ticket_tab: ft.Control,
    btn_receipt_tab: ft.Control,
    ticket_hovered: bool = False,
    receipt_hovered: bool = False,
) -> None:
    """사이드바 탭 상태를 선택 상태, 콘텐츠, 버튼 스타일에 반영한다."""
    current_tab["value"] = tab_key
    content_host.content = tab_state.content
    ticket_active = tab_key == "ticket"
    receipt_active = tab_key == "receipt"
    btn_ticket_tab.style = build_sidebar_nav_button_style(
        is_active=ticket_active,
        is_hovered=(not ticket_active) and ticket_hovered,
    )
    btn_receipt_tab.style = build_sidebar_nav_button_style(
        is_active=receipt_active,
        is_hovered=(not receipt_active) and receipt_hovered,
    )
    setattr(btn_ticket_tab, "icon_color", ACCENT_PRIMARY_DARK if ticket_active else ("#58ABA3" if ticket_hovered else "#5D6E82"))
    setattr(btn_receipt_tab, "icon_color", ACCENT_PRIMARY_DARK if receipt_active else ("#58ABA3" if receipt_hovered else "#5D6E82"))


def dispatch_sidebar_tab_change(
    tab_key: str,
    tab_state: SidebarTabState,
    *,
    current_tab: dict[str, str],
    content_host: ft.Control,
    btn_ticket_tab: ft.Control,
    btn_receipt_tab: ft.Control,
    refresh_search_results: Callable[[], None],
    page: ft.Page,
    closing_event: threading.Event | None = None,
    push_update: bool = True,
) -> None:
    """사이드바 탭 전환 시 UI 반영과 검색 재실행을 함께 처리한다."""
    apply_sidebar_tab_view_state(
        tab_state,
        current_tab=current_tab,
        tab_key=tab_key,
        content_host=content_host,
        btn_ticket_tab=btn_ticket_tab,
        btn_receipt_tab=btn_receipt_tab,
    )
    if tab_state.should_refresh_search:
        refresh_search_results()
        if push_update:
            safe_page_update(page, closing_event)
        return
    if push_update:
        safe_page_update(page, closing_event)


def build_buyer_event_view_state(
    order: Order,
    ticket_names: list[str] | set[str],
    *,
    search_blocked: bool,
) -> BuyerEventViewState:
    """구매자 정보 이벤트 수신 시 패널 갱신에 필요한 상태를 계산한다."""
    return BuyerEventViewState(
        order=order,
        panel_state=build_buyer_panel_state(order, ticket_names),
        search_blocked=search_blocked,
        buyer_detail_visible=True,
        buyer_empty_hint_visible=False,
    )


def build_search_result_rows(
    row_states: tuple[SearchResultRowState, ...],
    *,
    on_order_number_click: Callable[[str], None] | None = None,
    on_copy_order_number: Callable[[str], None] | None = None,
) -> list[ft.Container]:
    """검색 결과 row state 목록을 Flet 행 컨트롤 목록으로 변환한다."""

    def _build_goods_chip(text: str, *, tone: str) -> ft.Container:
        chip_map = {
            "goods": (STATUS_PINK_SOFT, "#F5C8D3", STATUS_PINK_TEXT),
            "goods-highlight": ("#FFDDE7", "#F0A9BE", "#8F3658"),
            "ticket": (STATUS_INFO_SOFT, "#C8D0FF", STATUS_INFO),
            "ticket-highlight": ("#DCE3FF", "#AAB6FF", "#1A1AD6"),
        }
        bgcolor, border_color, text_color = chip_map[tone]
        return ft.Container(
            bgcolor=bgcolor,
            border=ft.border.all(1, border_color),
            border_radius=999,
            padding=ft.padding.symmetric(horizontal=10, vertical=7),
            content=ft.Text(
                text,
                size=12,
                color=text_color,
                weight=ft.FontWeight.W_600,
            ),
        )

    def _build_result_item_group(
        items: tuple[str, ...],
        *,
        tone: str,
        fallback_text: str,
    ) -> ft.Control:
        if not items:
            return ft.Text(fallback_text, size=13, color="#1D1D1D")
        return ft.Column(
            controls=[_build_goods_chip(item, tone=tone) for item in items],
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

    def _build_data_cell(
        control: ft.Control,
        expand: int,
    ) -> ft.Container:
        return ft.Container(content=control, expand=expand, alignment=ft.alignment.center_left)

    def _build_order_number_cell(order_number: str) -> ft.Control:
        """주문번호 셀을 생성한다. 콜백이 있으면 클릭 가능한 링크로 표시한다."""
        text = ft.Text(order_number, size=13, color=ACCENT_PRIMARY_DARK)
        if on_order_number_click is not None:
            order_text_widget: ft.Control = ft.GestureDetector(
                content=ft.Container(
                    content=text,
                    on_hover=lambda e: _apply_hover(e, text),
                ),
                on_tap=lambda _: on_order_number_click(order_number),
                mouse_cursor=ft.MouseCursor.CLICK,
            )
        else:
            order_text_widget = text

        if on_copy_order_number is None:
            return order_text_widget

        copy_btn = ft.IconButton(
            icon=ft.icons.CONTENT_COPY_ROUNDED,
            icon_size=14,
            icon_color="#94A3B8",
            tooltip="주문번호 복사",
            width=28,
            height=28,
            on_click=lambda _e, on=order_number: on_copy_order_number(on),
        )
        return ft.Row(
            controls=[copy_btn, order_text_widget],
            spacing=2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )

    def _apply_hover(e: ft.HoverEvent, text: ft.Text) -> None:
        text.style = ft.TextStyle(
            decoration=ft.TextDecoration.UNDERLINE,
        ) if e.data == "true" else None
        text.update()

    rows: list[ft.Container] = []
    for row_state in row_states:
        rows.append(ft.Container(
            content=ft.Row(
                controls=[
                    _build_data_cell(_build_order_number_cell(row_state.order_number), 3),
                    _build_data_cell(ft.Text(row_state.name, size=13), 2),
                    _build_data_cell(ft.Text(row_state.phone, size=13), 3),
                    _build_data_cell(ft.Text(row_state.seat, size=13), 2),
                    _build_data_cell(
                        _build_result_item_group(
                            row_state.goods_items,
                            tone="goods-highlight" if row_state.goods_highlight else "goods",
                            fallback_text=row_state.goods_text,
                        ),
                        4,
                    ),
                    _build_data_cell(
                        _build_result_item_group(
                            row_state.ticket_items,
                            tone="ticket-highlight" if row_state.ticket_highlight else "ticket",
                            fallback_text=row_state.ticket_text,
                        ),
                        3,
                    ),
                    _build_data_cell(ft.Text(row_state.order_status_text, size=13), 2),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=row_state.row_bg,
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            border=ft.border.only(bottom=ft.border.BorderSide(1, "#EEEEEE")),
        ))
    return rows


def apply_order_search_dashboard_state(
    search_view_state: OrderSearchViewState,
    *,
    orders_map: dict[str, Order],
    filter_count_text: ft.Text,
    search_blocked_state: dict[str, bool],
    refresh_print_controls: Callable[..., None],
    search_result_list: ft.ListView,
    search_feedback_text: ft.Text,
    last_search_signature: dict[str, tuple[object, ...] | None],
    on_order_number_click: Callable[[str], None] | None = None,
    on_copy_order_number: Callable[[str], None] | None = None,
) -> None:
    """계산된 주문 검색 상태를 대시보드 검색 UI에 반영한다."""
    filter_count_text.value = search_view_state.filter_count_text
    orders_map.clear()
    orders_map.update({order.order_number: order for order in search_view_state.filtered_orders})
    search_blocked_state["value"] = search_view_state.search_blocked
    refresh_print_controls(search_blocked=search_view_state.search_blocked)
    search_result_list.controls = build_search_result_rows(
        search_view_state.row_states,
        on_order_number_click=on_order_number_click,
        on_copy_order_number=on_copy_order_number,
    )
    search_feedback_text.value = search_view_state.feedback_message
    search_feedback_text.color = search_view_state.feedback_color
    search_feedback_text.visible = bool(search_view_state.feedback_message)
    last_search_signature["value"] = search_view_state.search_signature


def apply_runtime_status_view_state(
    status_view_state: RuntimeStatusViewState,
    *,
    current_state: dict[str, str],
    state_text: ft.Text,
    state_badge: ft.Control,
    runtime_hint_text: ft.Text,
    last_event_text: ft.Text,
) -> None:
    """런타임 상태 표시 객체를 UI 컨트롤에 적용한다."""
    current_state["value"] = status_view_state.state_text
    state_text.value = status_view_state.state_text
    state_badge.bgcolor = status_view_state.badge_bgcolor
    runtime_hint_text.value = status_view_state.runtime_hint_text
    last_event_text.value = status_view_state.last_event_text


def apply_runtime_controls_state(
    controls_state: RuntimeControlsState,
    *,
    btn_relogin: ft.Control,
    btn_start_stop: ft.ElevatedButton,
    on_start: Callable[[ft.ControlEvent], None],
    on_stop: Callable[[ft.ControlEvent], None],
) -> None:
    """런타임 컨트롤 상태를 실제 버튼들에 적용한다."""
    btn_relogin.disabled = controls_state.relogin_disabled

    # 이전에 적용한 상태와 동일하면 버튼 재렌더링을 건너뛴다 (스캔마다 깜빡임 방지)
    if getattr(btn_start_stop, "_last_controls_state", None) == controls_state:
        return

    btn_start_stop.text = controls_state.primary_text
    btn_start_stop.icon = controls_state.primary_icon
    btn_start_stop.style = ft.ButtonStyle(
        bgcolor=controls_state.primary_bgcolor,
        color="#FFFFFF",
        shape=ft.RoundedRectangleBorder(radius=8),
    )
    btn_start_stop.disabled = controls_state.primary_disabled
    btn_start_stop.on_click = on_stop if controls_state.uses_stop_action else on_start
    btn_start_stop._last_controls_state = controls_state


def apply_runtime_status_dashboard_state(
    status_view_state: RuntimeStatusViewState,
    *,
    current_state: dict[str, str],
    state_text: ft.Text,
    state_badge: ft.Control,
    runtime_hint_text: ft.Text,
    last_event_text: ft.Text,
    btn_relogin: ft.Control,
    btn_start_stop: ft.ElevatedButton,
    on_start: Callable[[ft.ControlEvent], None],
    on_stop: Callable[[ft.ControlEvent], None],
    page: ft.Page,
    closing_event: threading.Event | None = None,
    push_update: bool = True,
) -> None:
    """런타임 상태 카드와 버튼 상태를 한 번에 UI에 반영한다."""
    apply_runtime_status_view_state(
        status_view_state,
        current_state=current_state,
        state_text=state_text,
        state_badge=state_badge,
        runtime_hint_text=runtime_hint_text,
        last_event_text=last_event_text,
    )
    apply_runtime_controls_state(
        status_view_state.controls_state,
        btn_relogin=btn_relogin,
        btn_start_stop=btn_start_stop,
        on_start=on_start,
        on_stop=on_stop,
    )
    if push_update:
        safe_page_update(page, closing_event)


def dispatch_runtime_status_refresh(
    state: str,
    message: str,
    timestamp: str,
    *,
    current_state: dict[str, str],
    state_text: ft.Text,
    state_badge: ft.Control,
    runtime_hint_text: ft.Text,
    last_event_text: ft.Text,
    btn_relogin: ft.Control,
    btn_start_stop: ft.ElevatedButton,
    on_start: Callable[[ft.ControlEvent], None],
    on_stop: Callable[[ft.ControlEvent], None],
    page: ft.Page,
    status_view_state: RuntimeStatusViewState | None = None,
    closing_event: threading.Event | None = None,
    push_update: bool = True,
) -> None:
    """런타임 상태 갱신에 필요한 view state 생성/재사용과 UI 반영을 한곳에서 처리한다."""
    status_view_state = status_view_state or build_runtime_status_view_state(
        state,
        message,
        timestamp,
    )
    apply_runtime_status_dashboard_state(
        status_view_state,
        current_state=current_state,
        state_text=state_text,
        state_badge=state_badge,
        runtime_hint_text=runtime_hint_text,
        last_event_text=last_event_text,
        btn_relogin=btn_relogin,
        btn_start_stop=btn_start_stop,
        on_start=on_start,
        on_stop=on_stop,
        page=page,
        closing_event=closing_event,
        push_update=push_update,
    )


def dispatch_runtime_event_dashboard_state(
    page: ft.Page,
    event_view_state: RuntimeEventViewState,
    *,
    current_state: dict[str, str],
    state_text: ft.Text,
    state_badge: ft.Control,
    runtime_hint_text: ft.Text,
    last_event_text: ft.Text,
    btn_relogin: ft.Control,
    btn_start_stop: ft.ElevatedButton,
    on_start: Callable[[ft.ControlEvent], None],
    on_stop: Callable[[ft.ControlEvent], None],
    refresh_search_results: Callable[[], None],
    closing_event: threading.Event | None = None,
) -> None:
    """런타임 이벤트를 대시보드 상태 반영과 검색 재실행까지 묶어 처리한다."""

    def _apply_status(status_view_state: RuntimeStatusViewState) -> None:
        dispatch_runtime_status_refresh(
            status_view_state.state_text,
            status_view_state.runtime_hint_text,
            status_view_state.last_event_text.removeprefix("마지막 이벤트: "),
            current_state=current_state,
            state_text=state_text,
            state_badge=state_badge,
            runtime_hint_text=runtime_hint_text,
            last_event_text=last_event_text,
            btn_relogin=btn_relogin,
            btn_start_stop=btn_start_stop,
            on_start=on_start,
            on_stop=on_stop,
            page=page,
            status_view_state=status_view_state,
            closing_event=closing_event,
            push_update=False,
        )

    dispatch_runtime_event_update(
        page,
        event_view_state,
        _apply_status,
        refresh_search_results,
        closing_event,
    )


def apply_buyer_event_view_state(
    view_state: BuyerEventViewState,
    *,
    current_buyer_order: dict[str, Order | None],
    buyer_name_text: ft.Text,
    buyer_phone_text: ft.Text,
    buyer_seat_text: ft.Text,
    buyer_goods_text: ft.Text,
    buyer_goods_hint: ft.Control | None = None,
    buyer_goods_cards: ft.Column | None = None,
    buyer_goods_count_text: ft.Text | None = None,
    buyer_goods_count_badge: ft.Control | None = None,
    buyer_ticket_text: ft.Text,
    buyer_received_text: ft.Text,
    buyer_detail_col: ft.Control,
    buyer_empty_hint: ft.Control,
) -> None:
    """구매자 이벤트 상태를 UI 컨트롤에 적용한다."""
    current_buyer_order["value"] = view_state.order
    buyer_name_text.value = view_state.panel_state.name_text
    buyer_phone_text.value = view_state.panel_state.phone_text
    buyer_seat_text.value = view_state.panel_state.seat_text
    buyer_goods_text.value = view_state.panel_state.goods_text
    buyer_goods_text.visible = view_state.panel_state.goods_visible
    if buyer_goods_hint is not None:
        buyer_goods_hint.value = view_state.panel_state.goods_hint_text
        buyer_goods_hint.visible = not view_state.panel_state.goods_visible
    if buyer_goods_cards is not None:
        buyer_goods_cards.controls = (
            build_buyer_goods_card_controls(view_state.panel_state.goods_items)
            if view_state.panel_state.goods_visible
            else []
        )
        buyer_goods_cards.visible = view_state.panel_state.goods_visible
    if buyer_goods_count_text is not None:
        buyer_goods_count_text.value = f"{len(view_state.panel_state.goods_items)}개"
    if buyer_goods_count_badge is not None:
        buyer_goods_count_badge.visible = view_state.panel_state.goods_visible
    buyer_ticket_text.value = view_state.panel_state.ticket_text
    buyer_ticket_text.visible = view_state.panel_state.ticket_visible
    buyer_received_text.value = view_state.panel_state.received_text
    buyer_received_text.visible = view_state.panel_state.received_visible
    buyer_detail_col.visible = view_state.buyer_detail_visible
    buyer_empty_hint.visible = view_state.buyer_empty_hint_visible


def apply_buyer_event_dashboard_state(
    view_state: BuyerEventViewState,
    *,
    current_buyer_order: dict[str, Order | None],
    buyer_name_text: ft.Text,
    buyer_phone_text: ft.Text,
    buyer_seat_text: ft.Text,
    buyer_goods_text: ft.Text,
    buyer_goods_hint: ft.Control | None = None,
    buyer_goods_cards: ft.Column | None = None,
    buyer_goods_count_text: ft.Text | None = None,
    buyer_goods_count_badge: ft.Control | None = None,
    buyer_ticket_text: ft.Text,
    buyer_received_text: ft.Text,
    buyer_detail_col: ft.Control,
    buyer_empty_hint: ft.Control,
    refresh_print_controls: Callable[..., None],
) -> None:
    """구매자 이벤트 상태를 UI와 출력 버튼 상태에 함께 반영한다."""
    apply_buyer_event_view_state(
        view_state,
        current_buyer_order=current_buyer_order,
        buyer_name_text=buyer_name_text,
        buyer_phone_text=buyer_phone_text,
        buyer_seat_text=buyer_seat_text,
        buyer_goods_text=buyer_goods_text,
        buyer_goods_hint=buyer_goods_hint,
        buyer_goods_cards=buyer_goods_cards,
        buyer_goods_count_text=buyer_goods_count_text,
        buyer_goods_count_badge=buyer_goods_count_badge,
        buyer_ticket_text=buyer_ticket_text,
        buyer_received_text=buyer_received_text,
        buyer_detail_col=buyer_detail_col,
        buyer_empty_hint=buyer_empty_hint,
    )
    refresh_print_controls(search_blocked=view_state.search_blocked)


def safe_page_update(control: ft.Control, closing_event: threading.Event | None = None) -> bool:
    """종료 중이 아니면 컨트롤 update를 시도하고, 예외는 경고 후 흡수한다."""
    if closing_event is not None and closing_event.is_set():
        return False
    control_page = getattr(control, "_Control__page", None)
    if control_page is None and hasattr(control, "_Control__page") and not isinstance(control, ft.Page):
        return False
    try:
        control.update()
    except AssertionError as exc:
        if "Control must be added to the page first." in str(exc):
            return False
        if closing_event is None or not closing_event.is_set():
            logger.warning("대시보드 UI 갱신 실패", exc_info=True)
        return False
    except Exception:
        if closing_event is None or not closing_event.is_set():
            logger.warning("대시보드 UI 갱신 실패", exc_info=True)
        return False
    return True


def call_page_from_thread(
    page: ft.Page,
    callback: Callable[[], None],
    closing_event: threading.Event | None = None,
) -> None:
    """페이지가 종료 중이 아니면 UI 스레드에서 콜백을 실행한다."""
    callback_started = False

    def guarded_callback() -> None:
        nonlocal callback_started
        callback_started = True
        if closing_event is not None and closing_event.is_set():
            return
        try:
            callback()
        except Exception:
            if closing_event is None or not closing_event.is_set():
                logger.warning("대시보드 UI 콜백 처리 실패", exc_info=True)

    if closing_event is not None and closing_event.is_set():
        return
    bridge = getattr(page, "call_from_thread", None)
    if callable(bridge):
        try:
            bridge(guarded_callback)
            return
        except Exception:
            if closing_event is not None and closing_event.is_set():
                return
            if callback_started:
                return
    run_task = getattr(page, "run_task", None)
    if callable(run_task):
        try:
            async def _run_callback() -> None:
                guarded_callback()

            run_task(_run_callback)
            return
        except Exception:
            if closing_event is not None and closing_event.is_set():
                return
            if callback_started:
                return
    page_loop = getattr(page, "loop", None) or getattr(page, "_Page__loop", None)
    call_soon_threadsafe = getattr(page_loop, "call_soon_threadsafe", None)
    if callable(call_soon_threadsafe):
        try:
            call_soon_threadsafe(guarded_callback)
            return
        except Exception:
            if closing_event is not None and closing_event.is_set():
                return
            if callback_started:
                return
    # 비-UI 스레드에서 직접 Flet 컨트롤을 건드리면 이벤트 루프가 손상되므로 스킵
    logger.warning("call_page_from_thread: UI 브릿지를 찾지 못해 콜백 실행 생략")


class DashboardFletView:
    """Main control center UI."""

    def __init__(self, runtime_manager: TicketRuntimeManager | None = None):
        settings = ReceiptSettingsStore(".runtime/receipt_settings.json").load()
        self._runtime_manager = runtime_manager or create_dashboard_runtime_manager(
            camera_index=settings.camera_index,
        )

    def run(self) -> None:
        ft.app(target=self._build_page)

    def _build_page(self, page: ft.Page) -> None:
        page.title = "Ticket_AUTO Control Center"
        page.window.width = 1800
        page.window.height = 920
        page.window.resizable = False
        page.padding = 0
        page.bgcolor = "#EDEDED"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = ft.Theme(font_family="Segoe UI")

        # 캔버스 미리보기용 폰트 등록 (존재하는 파일만 등록해 Flet 로딩 오류 방지)
        _font_candidates = {
            "malgun":      r"C:\Windows\Fonts\malgun.ttf",
            "gulim":       r"C:\Windows\Fonts\gulim.ttc",
            "batang":      r"C:\Windows\Fonts\batang.ttc",
            "nanumgothic": r"C:\Windows\Fonts\NanumGothic.ttf",
            "arial":       r"C:\Windows\Fonts\arial.ttf",
            "times":       r"C:\Windows\Fonts\times.ttf",
            "calibri":     r"C:\Windows\Fonts\calibri.ttf",
            "comic":       r"C:\Windows\Fonts\comic.ttf",
            "georgia":     r"C:\Windows\Fonts\georgia.ttf",
            "verdana":     r"C:\Windows\Fonts\verdana.ttf",
            "consolas":    r"C:\Windows\Fonts\consola.ttf",
            "impact":      r"C:\Windows\Fonts\impact.ttf",
        }
        page.fonts = {k: v for k, v in _font_candidates.items() if Path(v).exists()}

        current_tab = {"value": "ticket"}
        current_state = {"value": "IDLE"}
        data_file_state = {"path": ensure_managed_data_file()}
        excel_service = ExcelService(str(data_file_state["path"]))
        excel_service.ensure_seat_column()
        excel_service.ensure_receipt_column()
        excel_service.ensure_order_status_column()
        settings_store = ReceiptSettingsStore(str(resolve_project_path(".runtime/receipt_settings.json")))
        scan_success_count_store = ScanSuccessSoundStateStore()
        scan_success_sound_service = ScanSuccessSoundService(state_store=scan_success_count_store)

        state_text = ft.Text("IDLE", size=18, weight=ft.FontWeight.BOLD, color="#1F1F1F")
        state_badge = ft.Container(
            content=state_text,
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
            bgcolor="#DDE8FF",
            border_radius=10,
        )
        last_event_text = ft.Text("마지막 이벤트: -", color="#505050")
        runtime_hint_text = ft.Text("런타임 대기 중", color="#606060", size=13)
        processed_count_text = ft.Text(
            format_processed_count_text(load_processed_success_count(scan_success_count_store)),
            size=12,
            color=ACCENT_PRIMARY_DEEP,
            weight=ft.FontWeight.W_600,
        )
        processed_count_reset_button = ft.OutlinedButton(
            content=ft.Row(
                controls=[
                    ft.Container(
                        content=processed_count_text,
                        padding=ft.padding.symmetric(horizontal=4, vertical=0),
                    ),
                    ft.Container(
                        width=1,
                        height=18,
                        bgcolor=ACCENT_PRIMARY_BORDER,
                        margin=ft.margin.symmetric(horizontal=2),
                    ),
                    ft.Icon(ICONS.RESTART_ALT_ROUNDED, size=16, color="#4A6278"),
                    ft.Text(
                        "초기화",
                        size=12,
                        color="#4A6278",
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=lambda _e: _reset_processed_success_count(),
            tooltip="처리완료 누적 카운트를 0으로 초기화",
            style=ft.ButtonStyle(
                color="#4A6278",
                side=ft.border.all(1, ACCENT_PRIMARY_BORDER),
                bgcolor=ACCENT_PRIMARY_SOFT,
                overlay_color=ACCENT_PRIMARY_SOFT_HOVER,
                shape=ft.RoundedRectangleBorder(radius=999),
                padding=ft.padding.symmetric(horizontal=12, vertical=10),
            ),
            height=38,
        )
        next_special_rule_sections = ft.Column(
            visible=False,
            controls=[],
            spacing=10,
            expand=False,
        )
        special_rule_progress_panel_ref: dict[str, ft.Container | None] = {"value": None}

        def _refresh_scan_success_progress_summary() -> None:
            current_count = load_processed_success_count(scan_success_count_store)
            processed_count_text.value = format_processed_count_text(current_count)
            progress_states = resolve_special_rule_progress_list(
                settings_store.load(),
                current_count=current_count,
                service=scan_success_sound_service,
            )
            view_states = [format_next_special_rule_text(item) for item in progress_states]
            next_special_rule_sections.controls = build_special_rule_progress_sections(view_states)
            next_special_rule_sections.visible = bool(next_special_rule_sections.controls)
            if special_rule_progress_panel_ref["value"] is not None:
                special_rule_progress_panel_ref["value"].visible = next_special_rule_sections.visible

        _refresh_scan_success_progress_summary()

        # 주문 검색 UI
        search_field = ft.TextField(
            hint_text="주문번호, 이름, 연락처로 검색",
            expand=True,
            border_radius=8,
            height=42,
        )
        filter_dropdown = ft.Dropdown(
            value="전체",
            options=[
                ft.dropdown.Option("전체"),
                ft.dropdown.Option("결제완료"),
                ft.dropdown.Option("거래종료"),
            ],
            width=120,
            height=42,
            border_radius=8,
            content_padding=ft.padding.symmetric(horizontal=10, vertical=0),
        )
        filter_count_text = ft.Text("", size=13, color="#666666")
        search_feedback_text = ft.Text("", size=13, color="#777777", visible=False)

        def _build_header_cell(text: str, expand: int) -> ft.Container:
            return ft.Container(
                content=ft.Text(text, weight=ft.FontWeight.BOLD, size=14, color="#333333"),
                expand=expand,
                alignment=ft.alignment.center_left,
            )

        search_result_header = ft.Container(
            content=ft.Row(
                controls=[
                    _build_header_cell("주문번호", 3),
                    _build_header_cell("이름", 2),
                    _build_header_cell("연락처", 3),
                    _build_header_cell("좌석번호", 2),
                    _build_header_cell("상품목록", 4),
                    _build_header_cell("티켓", 3),
                    _build_header_cell("주문상태", 2),
                ],
                spacing=10,
            ),
            bgcolor="#F5F5F5",
            padding=ft.padding.symmetric(horizontal=16, vertical=12),
            border=ft.border.only(bottom=ft.border.BorderSide(1, "#D2D2D2")),
        )

        search_result_list = ft.ListView(
            expand=True,
            spacing=0,
            auto_scroll=False,
        )

        btn_start_stop = ft.ElevatedButton(
            "티켓 확인 시작",
            icon=ICONS.PLAY_ARROW_ROUNDED,
            style=ft.ButtonStyle(
                bgcolor=ACCENT_PRIMARY,
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        btn_relogin = ft.OutlinedButton(
            "재로그인",
            icon=ICONS.LOGIN_ROUNDED,
        )
        btn_open_witchform = ft.OutlinedButton(
            "Witchform 열기",
            icon=ICONS.OPEN_IN_NEW_ROUNDED,
        )

        btn_ticket_tab = ft.TextButton("티켓 확인", icon=ICONS.CONFIRMATION_NUMBER_ROUNDED)
        btn_receipt_tab = ft.TextButton("영수증 양식", icon=ICONS.RECEIPT_LONG_ROUNDED)
        btn_ticket_tab.icon_size = 18
        btn_receipt_tab.icon_size = 18
        content_host = ft.Container(expand=True, padding=ft.padding.all(16))
        receipt_settings_panel_ref: dict[str, ft.Control | None] = {"value": None}

        # 주문 선택 출력용 Dropdown + 버튼
        orders_map: dict[str, Order] = {}
        current_buyer_order: dict[str, Order | None] = {"value": None}
        last_search_signature = {"value": None}
        search_blocked_state = {"value": False}
        search_result_highlight_state = {"value": False}
        search_feedback_override_state = {"value": ""}
        sidebar_tab_hover_state = {"ticket": False, "receipt": False}
        print_job_state = {"in_progress": False}
        search_refresh_lock = threading.Lock()
        search_refresh_timer: threading.Timer | None = None
        search_refresh_stop = threading.Event()

        def _reset_processed_success_count(_e: ft.ControlEvent | None = None) -> None:
            scan_success_count_store.save_success_count(0)
            _refresh_scan_success_progress_summary()
            page.snack_bar = build_dashboard_snack_bar(
                "처리완료 누적 카운트를 초기화했습니다.",
                success=True,
            )
            page.snack_bar.open = True
            safe_page_update(page, search_refresh_stop)

        btn_refresh = ft.IconButton(
            icon=ICONS.REFRESH_ROUNDED,
            tooltip="새로고침",
            icon_size=20,
        )
        btn_import_data = ft.OutlinedButton(
            "data 파일 가져오기",
            icon=ICONS.UPLOAD_FILE_ROUNDED,
            tooltip="엑셀 data 파일을 가져와 Resources/data/data.xlsx 로 바로 적용합니다.",
        )
        data_file_picker = ft.FilePicker()
        _attach_page_service(page, data_file_picker)

        def _show_dashboard_warning(message: str) -> None:
            page.snack_bar = build_dashboard_snack_bar(message, success=False)
            page.snack_bar.open = True
            safe_page_update(page, search_refresh_stop)

        def _show_dashboard_success(message: str) -> None:
            page.snack_bar = build_dashboard_snack_bar(message, success=True)
            page.snack_bar.open = True
            safe_page_update(page, search_refresh_stop)

        def _on_open_witchform(_e: ft.ControlEvent) -> None:
            def _open() -> None:
                opened = request_witchform_login_page(self._runtime_manager)
                message = "로그인 페이지를 열었습니다." if opened else "티켓 확인 시작 후 로그인 페이지를 열 수 있습니다."
                callback = _show_dashboard_success if opened else _show_dashboard_warning
                call_page_from_thread(page, lambda: callback(message), search_refresh_stop)

            threading.Thread(target=_open, daemon=True).start()

        def refresh_print_controls(*, search_blocked: bool = False) -> None:
            btn_buyer_print.disabled = compute_print_button_disabled(
                has_target=current_buyer_order["value"] is not None,
                print_in_progress=print_job_state["in_progress"],
            )
            btn_buyer_preview.disabled = current_buyer_order["value"] is None

        def _show_receipt_preview(order: Order) -> None:
            """주문 영수증 미리보기를 다이얼로그로 표시한다."""
            def _do_render() -> None:
                try:
                    receipt_settings = settings_store.load()
                    preview_items = render_receipt_preview_base64(order, receipt_settings)
                except Exception as exc:
                    logger.error("영수증 미리보기 실패: %s", exc, exc_info=True)

                    def _show_error() -> None:
                        page.snack_bar = build_dashboard_snack_bar(
                            f"미리보기 실패: {exc}",
                            success=False,
                        )
                        page.snack_bar.open = True
                        safe_page_update(page, search_refresh_stop)

                    call_page_from_thread(page, _show_error, search_refresh_stop)
                    return

                def _show_dialog() -> None:
                    def _close_preview(_e: ft.ControlEvent) -> None:
                        page.dialog.open = False
                        safe_page_update(page, search_refresh_stop)

                    page.dialog = build_receipt_preview_dialog(
                        preview_items=preview_items, on_close=_close_preview,
                    )
                    page.dialog.open = True
                    safe_page_update(page, search_refresh_stop)

                call_page_from_thread(page, _show_dialog, search_refresh_stop)

            threading.Thread(target=_do_render, daemon=True).start()

        def _on_order_print(order: Order) -> None:
            """주문번호 클릭 시 해당 주문의 영수증을 출력한다."""
            if print_job_state["in_progress"]:
                return
            print_job_state["in_progress"] = True
            refresh_print_controls(search_blocked=search_blocked_state["value"])
            safe_page_update(page, search_refresh_stop)

            def _do_print() -> None:
                try:
                    receipt_settings = settings_store.load()
                    copies = print_order_receipt(order, receipt_settings)
                    msg = f"영수증 {copies}매 출력 완료: {order.order_number}"
                    color = "#D8F4E3"
                except Exception as exc:
                    logger.error("영수증 출력 실패: %s (주문: %s)", exc, order.order_number, exc_info=True)
                    msg, color = f"영수증 출력 실패: {exc}", "#FFD6D6"

                def _show_snack() -> None:
                    print_job_state["in_progress"] = False
                    refresh_print_controls(search_blocked=search_blocked_state["value"])
                    page.snack_bar = build_dashboard_snack_bar(
                        msg,
                        success=(color == "#D8F4E3"),
                    )
                    page.snack_bar.open = True
                    safe_page_update(page, search_refresh_stop)

                call_page_from_thread(page, _show_snack, search_refresh_stop)

            threading.Thread(target=_do_print, daemon=True).start()

        def do_search(_=None, push_update: bool = True) -> None:
            """검색어 + 필터 조건으로 주문 조회 후 테이블 갱신."""
            keyword = search_field.value or ""
            filter_value = filter_dropdown.value or "전체"
            previous_order_value = None
            search_error_message = ""
            try:
                orders = excel_service.search_orders(keyword)
            except Exception as exc:
                logger.error("주문 검색 실패: %s", exc, exc_info=True)
                orders = []
                search_error_message = str(exc) or "엑셀 파일을 읽을 수 없습니다"

            ticket_names = load_ticket_product_names(settings_store)
            search_view_state = build_order_search_view_state(
                keyword,
                filter_value,
                orders,
                ticket_names,
                previous_order_value,
                error_message=search_error_message,
                info_message=search_feedback_override_state["value"],
                highlight_ticket_split=search_result_highlight_state["value"],
            )
            if search_view_state.search_signature == last_search_signature["value"]:
                return
            def _on_order_number_click(order_number: str) -> None:
                """검색 결과에서 주문번호 클릭 시 해당 주문의 영수증을 출력한다."""
                order = orders_map.get(order_number)
                if order:
                    _on_order_print(order)

            def _on_copy_order_number(order_number: str) -> None:
                page.set_clipboard(order_number)

            apply_order_search_dashboard_state(
                search_view_state,
                orders_map=orders_map,
                filter_count_text=filter_count_text,
                search_blocked_state=search_blocked_state,
                refresh_print_controls=refresh_print_controls,
                search_result_list=search_result_list,
                search_feedback_text=search_feedback_text,
                last_search_signature=last_search_signature,
                on_order_number_click=_on_order_number_click,
                on_copy_order_number=_on_copy_order_number,
            )
            search_result_highlight_state["value"] = False
            search_feedback_override_state["value"] = ""
            if push_update:
                safe_page_update(page, search_refresh_stop)

        def cancel_scheduled_search_refresh() -> None:
            nonlocal search_refresh_timer
            with search_refresh_lock:
                timer = search_refresh_timer
                search_refresh_timer = None
            if timer is not None:
                timer.cancel()

        def refresh_search_results_from_thread(push_update: bool = True) -> None:
            call_page_from_thread(
                page,
                lambda: do_search(push_update=push_update),
                search_refresh_stop,
            )

        def _refresh_ticket_product_option_panels() -> None:
            for panel in (
                ticket_settings_sidebar_panel_ref["value"],
                receipt_settings_panel_ref["value"],
            ):
                reload_fn = getattr(panel, "_reload_ticket_product_options", None)
                if not callable(reload_fn):
                    continue
                try:
                    reload_fn()
                except Exception:
                    logger.warning("티켓 상품 분류 옵션 새로고침 실패", exc_info=True)

        def _handle_data_file_import(files: list[ft.FilePickerFile]) -> None:
            if not files:
                return

            source_path = getattr(files[0], "path", None)
            if not source_path:
                _show_dashboard_warning("선택한 data 파일 경로를 읽을 수 없습니다.")
                return

            try:
                # 교체 전에 현재 처리완료 상태 스냅샷 저장
                received_snapshot = excel_service.get_received_status_map()

                imported_path = copy_data_file_to_managed_location(source_path)
                data_file_state["path"] = imported_path
                excel_service.ensure_seat_column()
                excel_service.ensure_receipt_column()
                excel_service.ensure_order_status_column()

                # 이전 파일의 처리완료 상태를 새 파일에 복원
                restored_count = excel_service.bulk_restore_received_status(received_snapshot)

                _refresh_ticket_product_option_panels()
                last_search_signature["value"] = None
                status_msg = f"data 파일 적용 완료: {Path(source_path).name}"
                if restored_count > 0:
                    status_msg += f" (처리완료 {restored_count}건 복원)"
                search_feedback_override_state["value"] = status_msg
                do_search(push_update=False)
                safe_page_update(page, search_refresh_stop)
                _show_dashboard_success("data 파일 가져오기 완료")
            except Exception as exc:
                logger.error("data 파일 가져오기 실패: %s", exc, exc_info=True)
                _show_dashboard_warning(f"data 파일 가져오기 실패: {exc}")

        def schedule_search_refresh(delay_sec: float = SEARCH_DEBOUNCE_SEC) -> None:
            nonlocal search_refresh_timer
            cancel_scheduled_search_refresh()
            if delay_sec <= 0:
                refresh_search_results_from_thread(push_update=True)
                return

            def _run_refresh() -> None:
                nonlocal search_refresh_timer
                with search_refresh_lock:
                    search_refresh_timer = None
                refresh_search_results_from_thread(push_update=True)

            timer = threading.Timer(delay_sec, _run_refresh)
            timer.daemon = True
            with search_refresh_lock:
                search_refresh_timer = timer
            timer.start()

        def watch_excel_changes() -> None:
            last_seen_mtime_ns = read_file_mtime_ns(data_file_state["path"])
            while not search_refresh_stop.wait(EXCEL_WATCH_INTERVAL_SEC):
                current_mtime_ns = read_file_mtime_ns(data_file_state["path"])
                last_seen_mtime_ns, should_schedule_refresh = process_search_refresh_watch_tick(
                    last_seen_mtime_ns,
                    current_mtime_ns,
                    tab_key=current_tab["value"],
                    runtime_state=current_state["value"],
                )
                if should_schedule_refresh:
                    schedule_search_refresh(delay_sec=0)

        search_field.on_change = lambda _e: schedule_search_refresh()
        search_field.on_submit = do_search
        filter_dropdown.on_change = do_search
        btn_refresh.on_click = do_search
        btn_import_data.on_click = lambda _e: data_file_picker.pick_files(
            allow_multiple=False,
            dialog_title="data 파일 가져오기",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx", "xlsm"],
        )
        setattr(data_file_picker, "on_result", lambda event: _handle_data_file_import(_coerce_picker_files(event)))

        def set_tab(tab_key: str, push_update: bool = True) -> None:
            receipt_panel = receipt_settings_panel_ref["value"]
            if tab_key == "receipt" and receipt_panel is None:
                try:
                    receipt_panel = build_receipt_settings_panel(
                        page,
                        store_path=str(resolve_project_path(".runtime/receipt_settings.json")),
                        initial_section="receipt",
                        show_section_tabs=False,
                    )
                    receipt_settings_panel_ref["value"] = receipt_panel
                except Exception:
                    logger.warning("영수증 양식 탭 패널 생성 실패", exc_info=True)
                    _show_dashboard_warning("영수증 양식 화면을 여는 중 오류가 발생했습니다.")
                    return
            tab_state = build_sidebar_tab_state(
                tab_key,
                ticket_panel,
                receipt_panel or receipt_settings_panel,
            )
            dispatch_sidebar_tab_change(
                tab_key,
                tab_state,
                current_tab=current_tab,
                content_host=content_host,
                btn_ticket_tab=btn_ticket_tab,
                btn_receipt_tab=btn_receipt_tab,
                refresh_search_results=lambda: do_search(push_update=False),
                page=page,
                closing_event=search_refresh_stop,
                push_update=push_update,
            )
            camera_focus_panel_state["value"] = False
            _apply_camera_focus_drawer(push_update=push_update)

        def on_runtime_event(state: str, message: str, timestamp: str) -> None:
            event_view_state = build_runtime_event_view_state(
                current_tab["value"],
                state,
                message,
                timestamp,
            )
            _refresh_camera_focus_capability_badge()
            _refresh_scan_success_progress_summary()
            dispatch_runtime_event_dashboard_state(
                page,
                event_view_state,
                current_state=current_state,
                state_text=state_text,
                state_badge=state_badge,
                runtime_hint_text=runtime_hint_text,
                last_event_text=last_event_text,
                btn_relogin=btn_relogin,
                btn_start_stop=btn_start_stop,
                on_start=on_start,
                on_stop=on_stop,
                refresh_search_results=lambda: do_search(push_update=False),
                closing_event=search_refresh_stop,
            )

        def on_start(_: ft.ControlEvent) -> None:
            # 시작 시 현재 선택된 카메라 인덱스로 app_factory 갱신
            selected_cam = int(camera_dropdown.value or "0")
            self._runtime_manager._app_factory = partial(
                Application, show_order_window=False, camera_index=selected_cam,
            )
            dispatch_runtime_status_refresh(
                "STARTING",
                "티켓 확인 시작 중",
                "-",
                current_state=current_state,
                state_text=state_text,
                state_badge=state_badge,
                runtime_hint_text=runtime_hint_text,
                last_event_text=last_event_text,
                btn_relogin=btn_relogin,
                btn_start_stop=btn_start_stop,
                on_start=on_start,
                on_stop=on_stop,
                page=page,
                closing_event=search_refresh_stop,
                push_update=True,
            )
            self._runtime_manager.start()

        def on_stop(_: ft.ControlEvent) -> None:
            dispatch_runtime_status_refresh(
                "STOPPING",
                "티켓 확인 종료 중",
                "-",
                current_state=current_state,
                state_text=state_text,
                state_badge=state_badge,
                runtime_hint_text=runtime_hint_text,
                last_event_text=last_event_text,
                btn_relogin=btn_relogin,
                btn_start_stop=btn_start_stop,
                on_start=on_start,
                on_stop=on_stop,
                page=page,
                closing_event=search_refresh_stop,
                push_update=True,
            )

            def _stop_runtime() -> None:
                self._runtime_manager.stop()

            threading.Thread(target=_stop_runtime, daemon=True).start()

        def on_relogin(_: ft.ControlEvent) -> None:
            self._runtime_manager.relogin()

        btn_start_stop.on_click = on_start
        btn_relogin.on_click = on_relogin
        btn_open_witchform.on_click = _on_open_witchform
        btn_ticket_tab.on_click = lambda _: set_tab("ticket")
        btn_receipt_tab.on_click = lambda _: set_tab("receipt")

        camera_view = ft.Image(width=400, height=300, fit=ft.ImageFit.CONTAIN, visible=False)

        def on_camera_frame(b64_str: str) -> None:
            dispatch_camera_frame_update(page, camera_view, b64_str, search_refresh_stop)

        self._runtime_manager.set_camera_frame_listener(on_camera_frame)

        # 카메라 선택 드롭다운 (목록 조회는 패널을 열 때 지연 수행)
        camera_svc = WindowsCameraService()
        saved_dashboard_settings = settings_store.load()
        saved_camera_index = saved_dashboard_settings.camera_index

        camera_dropdown = ft.Dropdown(
            label="스캔 카메라",
            width=270,
            value=str(saved_camera_index),
            options=[ft.dropdown.Option(key=str(saved_camera_index), text=f"카메라 {saved_camera_index}")],
        )
        saved_focus_settings = saved_dashboard_settings
        camera_focus_panel_state = {"value": False}
        camera_focus_handle_state = {"hovered": False}
        camera_focus_capability_state = {"manual_supported": None}
        camera_list_requested_state = {"value": False}
        ticket_settings_sidebar_panel_ref: dict[str, ft.Control | None] = {"value": None}
        receipt_settings_sidebar_panel_ref: dict[str, ft.Control | None] = {"value": None}
        focus_mode_dropdown = ft.Dropdown(
            label="초점 모드",
            value=saved_focus_settings.scanner_focus_mode,
            options=[
                ft.dropdown.Option(key="auto", text="자동 초점"),
                ft.dropdown.Option(key="manual", text="수동 초점"),
            ],
            border_radius=10,
        )
        manual_focus_value_field = ft.TextField(
            label="수동 초점 값",
            value=(
                ""
                if saved_focus_settings.scanner_manual_focus_value is None
                else str(saved_focus_settings.scanner_manual_focus_value)
            ),
            hint_text="예: 8.0",
            border_radius=10,
        )
        apply_camera_focus_input_tone(camera_dropdown, focus_mode_dropdown, manual_focus_value_field)
        camera_focus_capability_badge = build_camera_focus_capability_badge(
            text="현재 카메라는 수동 초점을 지원하지 않아 자동 초점만 사용할 수 있습니다.",
            visible=False,
        )
        def _sync_dashboard_focus_field_state() -> None:
            manual_focus_value_field.disabled = (
                (focus_mode_dropdown.value or "auto") != "manual"
                or camera_focus_capability_state["manual_supported"] is False
            )

        def _refresh_camera_focus_capability_badge() -> None:
            capability = self._runtime_manager.get_scanner_focus_capability()
            manual_supported = None
            if capability is not None:
                manual_supported = bool(getattr(capability, "manual_focus_supported", False))
            camera_focus_capability_state["manual_supported"] = manual_supported
            camera_focus_capability_badge.visible = manual_supported is False
            if manual_supported is False and (focus_mode_dropdown.value or "auto") == "manual":
                focus_mode_dropdown.value = "auto"
                current_settings = settings_store.load()
                current_settings.scanner_focus_mode = "auto"
                current_settings.scanner_manual_focus_value = None
                settings_store.save(current_settings)
                self._runtime_manager.apply_scanner_focus_settings("auto", None)
            _sync_dashboard_focus_field_state()

        def _parse_dashboard_manual_focus_value() -> float | None:
            raw = (manual_focus_value_field.value or "").strip()
            if not raw:
                return None
            return float(raw)

        def _save_dashboard_focus_settings() -> None:
            current_settings = settings_store.load()
            parsed_manual_focus_value = _parse_dashboard_manual_focus_value()
            requested_manual_focus = (focus_mode_dropdown.value or "auto") == "manual"
            manual_supported = camera_focus_capability_state["manual_supported"]
            current_settings.scanner_focus_mode = (
                "manual"
                if requested_manual_focus
                and parsed_manual_focus_value is not None
                and manual_supported is not False
                else "auto"
            )
            current_settings.scanner_manual_focus_value = (
                parsed_manual_focus_value
                if current_settings.scanner_focus_mode == "manual"
                else None
            )
            settings_store.save(current_settings)
            self._runtime_manager.apply_scanner_focus_settings(
                current_settings.scanner_focus_mode,
                current_settings.scanner_manual_focus_value,
            )
            safe_page_update(page, search_refresh_stop)

        def _apply_camera_focus_side_handle_style() -> None:
            is_settings_tab = current_tab["value"] in {"ticket", "receipt"}
            is_open = is_settings_tab and camera_focus_panel_state["value"]
            is_hovered = is_settings_tab and camera_focus_handle_state["hovered"]
            badge = camera_focus_side_handle.content.controls[0]
            divider = camera_focus_side_handle.content.controls[1]
            first_label = camera_focus_side_handle.content.controls[2]
            second_label = camera_focus_side_handle.content.controls[3]

            if is_open:
                camera_focus_side_handle.bgcolor = ACCENT_PRIMARY
                camera_focus_side_handle.border = ft.border.all(1, "#259E94")
                camera_focus_side_handle.shadow = ft.BoxShadow(
                    blur_radius=16,
                    color=ft.colors.with_opacity(0.12, ACCENT_PRIMARY_DARK),
                    offset=ft.Offset(-2, 4),
                )
                camera_focus_side_handle.scale = 1.0
                camera_focus_side_handle.opacity = 1.0
                camera_focus_side_handle.offset = ft.Offset(0, 0)
                camera_focus_side_handle.tooltip = "설정 닫기"
                badge.bgcolor = "#FFFFFF"
                badge.border = ft.border.all(1, ft.colors.with_opacity(0.22, "#FFFFFF"))
                badge.content.color = ACCENT_PRIMARY_DARK
                divider.bgcolor = ft.colors.with_opacity(0.36, "#FFFFFF")
                first_label.color = "#FFFFFF"
                second_label.color = "#FFFFFF"
                return

            if is_hovered:
                camera_focus_side_handle.bgcolor = HANDLE_HOVER_BG
                camera_focus_side_handle.border = ft.border.all(1, DRAWER_BORDER_STRONG)
                camera_focus_side_handle.shadow = ft.BoxShadow(
                    blur_radius=14,
                    color=ft.colors.with_opacity(0.08, "#0F172A"),
                    offset=ft.Offset(-3, 4),
                )
                camera_focus_side_handle.scale = 1.025
                camera_focus_side_handle.opacity = 1.0
                camera_focus_side_handle.offset = ft.Offset(-0.04, 0)
                camera_focus_side_handle.tooltip = "설정 열기"
                badge.bgcolor = HANDLE_BADGE_BG
                badge.border = ft.border.all(1, HANDLE_BADGE_BORDER)
                badge.content.color = HANDLE_BADGE_ICON
                divider.bgcolor = HANDLE_DIVIDER
                first_label.color = HANDLE_TEXT
                second_label.color = HANDLE_TEXT
                return

            camera_focus_side_handle.bgcolor = HANDLE_IDLE_BG
            camera_focus_side_handle.border = ft.border.all(1, DRAWER_BORDER_STRONG)
            camera_focus_side_handle.shadow = ft.BoxShadow(
                blur_radius=12,
                color=ft.colors.with_opacity(0.06, "#0F172A"),
                offset=ft.Offset(-2, 3),
            )
            camera_focus_side_handle.scale = 1.0
            camera_focus_side_handle.opacity = 0.98
            camera_focus_side_handle.offset = ft.Offset(0, 0)
            camera_focus_side_handle.tooltip = "설정 열기"
            badge.bgcolor = HANDLE_BADGE_BG
            badge.border = ft.border.all(1, HANDLE_BADGE_BORDER)
            badge.content.color = HANDLE_BADGE_ICON
            divider.bgcolor = HANDLE_DIVIDER
            first_label.color = HANDLE_TEXT
            second_label.color = HANDLE_TEXT

        def _apply_camera_focus_drawer(push_update: bool = True) -> None:
            active_tab = current_tab["value"]
            is_settings_tab = active_tab in {"ticket", "receipt"}
            is_open = is_settings_tab and camera_focus_panel_state["value"]
            _refresh_camera_focus_capability_badge()
            active_panel = (
                (
                    _get_ticket_settings_sidebar_panel()
                    if active_tab == "ticket"
                    else _get_receipt_settings_sidebar_panel()
                )
                if is_open
                else None
            )
            settings_sidebar_content_host.content = active_panel
            camera_focus_drawer.content.controls[0].controls[0].value = (
                "티켓 확인 설정" if active_tab == "ticket" else "영수증 양식 설정"
            )
            camera_focus_drawer.visible = is_settings_tab
            camera_focus_drawer.opacity = 1.0
            camera_focus_drawer.offset = ft.Offset(0, 0)
            camera_focus_side_handle.visible = is_settings_tab
            camera_focus_overlay_group.visible = is_settings_tab
            camera_focus_overlay_group.opacity = 1.0 if is_settings_tab else 0.0
            camera_focus_overlay_group.offset = (
                ft.Offset(0, 0)
                if is_open
                else ft.Offset(CAMERA_SETTINGS_OVERLAY_CLOSED_OFFSET_X, 0)
            )
            _apply_camera_focus_side_handle_style()
            if push_update:
                safe_page_update(page, search_refresh_stop)
            if is_open and active_tab == "ticket":
                _reset_settings_panel_scroll(active_panel)
                if push_update:
                    safe_page_update(page, search_refresh_stop)

        def _toggle_camera_focus_panel(_e: ft.ControlEvent | None = None) -> None:
            camera_focus_panel_state["value"] = not camera_focus_panel_state["value"]
            _apply_camera_focus_drawer()

        def _close_camera_focus_panel(_e: ft.ControlEvent | None = None) -> None:
            camera_focus_panel_state["value"] = False
            _apply_camera_focus_drawer()

        def _on_camera_focus_handle_hover(e: ft.ControlEvent) -> None:
            camera_focus_handle_state["hovered"] = e.data == "true"
            _apply_camera_focus_side_handle_style()
            safe_page_update(camera_focus_side_handle, search_refresh_stop)

        def _on_focus_mode_change(_e: ft.ControlEvent) -> None:
            _sync_dashboard_focus_field_state()
            try:
                _save_dashboard_focus_settings()
            except ValueError:
                safe_page_update(page, search_refresh_stop)

        def _on_manual_focus_value_blur(_e: ft.ControlEvent) -> None:
            try:
                _save_dashboard_focus_settings()
            except ValueError:
                safe_page_update(page, search_refresh_stop)

        def _on_camera_dropdown_change(_e: ft.ControlEvent) -> None:
            new_index = int(camera_dropdown.value or "0")
            current_settings = settings_store.load()
            current_settings.camera_index = new_index
            settings_store.save(current_settings)
            self._runtime_manager.change_camera(new_index)

        camera_dropdown.on_change = _on_camera_dropdown_change
        focus_mode_dropdown.on_change = _on_focus_mode_change
        manual_focus_value_field.on_blur = _on_manual_focus_value_blur

        btn_refresh_cameras = ft.IconButton(
            icon=ICONS.REFRESH_ROUNDED,
            tooltip="카메라 목록 새로고침",
            icon_size=20,
        )

        def _populate_camera_dropdown(cameras: list[CameraDevice]) -> None:
            """카메라 목록을 드롭다운에 반영한다."""
            camera_dropdown.options = [
                ft.dropdown.Option(key=str(c.index), text=c.name)
                for c in cameras
            ]
            current_val = int(camera_dropdown.value or "0")
            if cameras and not any(c.index == current_val for c in cameras):
                camera_dropdown.value = str(cameras[0].index)
            safe_page_update(page, search_refresh_stop)

        def _load_cameras_async() -> None:
            """백그라운드에서 카메라 목록을 조회한다."""
            try:
                cameras = camera_svc.list_cameras()
            except Exception:
                cameras = []
            if cameras:
                call_page_from_thread(
                    page, lambda: _populate_camera_dropdown(cameras), search_refresh_stop,
                )

        def _request_camera_list_refresh() -> None:
            if camera_list_requested_state["value"]:
                return
            camera_list_requested_state["value"] = True
            threading.Thread(target=_load_cameras_async, daemon=True).start()

        def _on_refresh_cameras(_e: ft.ControlEvent) -> None:
            camera_list_requested_state["value"] = True
            threading.Thread(target=_load_cameras_async, daemon=True).start()

        btn_refresh_cameras.on_click = _on_refresh_cameras
        _sync_dashboard_focus_field_state()

        camera_selector_row = ft.Row(
            controls=[
                camera_dropdown,
                btn_refresh_cameras,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        def _on_ticket_product_names_changed(_selected_names: list[str]) -> None:
            search_result_highlight_state["value"] = True
            search_feedback_override_state["value"] = "티켓 분류 기준을 검색 결과에 즉시 반영했습니다."
            current_order = current_buyer_order["value"]
            if current_order is not None:
                update_buyer_info(current_order)
            do_search(push_update=False)
            safe_page_update(page, search_refresh_stop)

        def _reset_settings_panel_scroll(panel: ft.Control | None) -> None:
            if panel is None:
                return
            scroll_host = getattr(panel, "_scroll_host", None) or getattr(panel, "content", None)
            scroll_to = getattr(scroll_host, "scroll_to", None)
            if not callable(scroll_to):
                return
            try:
                scroll_to(offset=0, duration=0)
            except TypeError:
                try:
                    scroll_to(0)
                except Exception:
                    return
            except Exception:
                return

        def _get_ticket_settings_sidebar_panel() -> ft.Control:
            panel = ticket_settings_sidebar_panel_ref["value"]
            if panel is None:
                _request_camera_list_refresh()
                try:
                    panel = build_app_settings_panel(
                        page,
                        store_path=str(resolve_project_path(".runtime/receipt_settings.json")),
                        on_apply_scanner_focus_settings=self._runtime_manager.apply_scanner_focus_settings,
                        on_ticket_products_changed=_on_ticket_product_names_changed,
                        on_scan_sound_rules_changed=lambda: (
                            _refresh_scan_success_progress_summary(),
                            safe_page_update(page, search_refresh_stop),
                        ),
                        show_section_tabs=False,
                        show_receipt_section=False,
                        camera_selector_row=camera_selector_row,
                        focus_capability_badge=camera_focus_capability_badge,
                        focus_section_title="카메라 초점 기능",
                        focus_description="스캔 카메라와 초점 모드를 현재 화면에서 바로 조정합니다.",
                        show_title=False,
                    )
                except Exception:
                    logger.warning("티켓 설정 사이드 패널 생성 실패", exc_info=True)
                    _show_dashboard_warning("티켓 설정 패널을 여는 중 오류가 발생했습니다.")
                    panel = build_settings_sidebar_placeholder_panel(
                        title="티켓 확인 설정",
                        description="설정 패널을 불러오는 중 오류가 발생했습니다.",
                    )
                ticket_settings_sidebar_panel_ref["value"] = panel
            return panel

        def _get_receipt_settings_sidebar_panel() -> ft.Control:
            panel = receipt_settings_sidebar_panel_ref["value"]
            if panel is None:
                try:
                    panel = build_receipt_sidebar_settings_panel(
                        page,
                        store_path=str(resolve_project_path(".runtime/receipt_settings.json")),
                    )
                except Exception:
                    logger.warning("영수증 설정 사이드 패널 생성 실패", exc_info=True)
                    _show_dashboard_warning("영수증 설정 패널을 여는 중 오류가 발생했습니다.")
                    panel = build_settings_sidebar_placeholder_panel(
                        title="영수증 양식 설정",
                        description="설정 패널을 불러오는 중 오류가 발생했습니다.",
                    )
                receipt_settings_sidebar_panel_ref["value"] = panel
            return panel

        settings_sidebar_content_host = ft.Container(expand=True)
        camera_focus_drawer = build_camera_focus_drawer(
            is_open=False,
            panel_content=settings_sidebar_content_host,
            on_close=_close_camera_focus_panel,
            title="티켓 확인 설정",
        )
        camera_focus_side_handle = build_camera_focus_side_handle(
            on_open=_toggle_camera_focus_panel,
            on_hover=_on_camera_focus_handle_hover,
        )
        camera_focus_side_handle.right = CAMERA_SETTINGS_DRAWER_WIDTH
        dashboard_overlay_host = build_dashboard_overlay_host(
            content_host=content_host,
            overlay_drawer=camera_focus_drawer,
            side_handle=camera_focus_side_handle,
        )
        camera_focus_overlay_group = dashboard_overlay_host.controls[1]
        top_controls_col = ft.Column(
            controls=[
                ft.Text("티켓 확인 제어", size=28, weight=ft.FontWeight.BOLD, color="#1D1D1D"),
                ft.Container(height=8),
                ft.Row(
                    controls=[btn_start_stop, btn_open_witchform, processed_count_reset_button],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    wrap=True,
                ),
            ],
            spacing=8,
            expand=False,
        )

        # 구매자 정보 표시 UI
        buyer_name_text = ft.Text("", size=18, weight=ft.FontWeight.BOLD)
        buyer_phone_text = ft.Text("", size=16, color="#333333")
        buyer_seat_text = ft.Text("", size=16, color="#333333")
        buyer_goods_text = ft.Text("", size=15, color="#243447", visible=False)
        buyer_goods_hint = ft.Text(
            "상품 구매 시 이곳에 표시됩니다.",
            size=13,
            color="#98A3B3",
        )
        buyer_goods_count_text = ft.Text("", size=11, color=STATUS_PINK_TEXT, weight=ft.FontWeight.BOLD)
        buyer_goods_count_badge = ft.Container(
            visible=False,
            bgcolor=STATUS_PINK_SOFT,
            border=ft.border.all(1, "#F5CAD6"),
            border_radius=999,
            padding=ft.padding.symmetric(horizontal=10, vertical=5),
            content=buyer_goods_count_text,
        )
        buyer_goods_cards = ft.Column(
            controls=[],
            spacing=8,
            visible=False,
            scroll=ft.ScrollMode.AUTO,
        )
        buyer_ticket_text = ft.Text("", size=14, color=ACCENT_PRIMARY_DARK, weight=ft.FontWeight.BOLD)
        buyer_received_text = ft.Text("", size=13, color="#888888")
        buyer_empty_hint = ft.Text(
            "QR 스캔 시 구매자 정보가 표시됩니다",
            size=14, color="#AAAAAA", text_align=ft.TextAlign.CENTER,
        )

        btn_buyer_print = ft.ElevatedButton(
            "출력",
            icon=ICONS.PRINT_ROUNDED,
            disabled=True,
            tooltip="영수증 출력",
            style=ft.ButtonStyle(
                bgcolor=ACCENT_PRIMARY,
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=12, vertical=0),
            ),
            height=34,
        )
        btn_buyer_preview = ft.OutlinedButton(
            "미리보기",
            icon=ICONS.VISIBILITY_ROUNDED,
            disabled=True,
            height=34,
            style=ft.ButtonStyle(
                padding=ft.padding.symmetric(horizontal=12, vertical=0),
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        def _on_buyer_print_click(_e) -> None:
            order = current_buyer_order["value"]
            if not order:
                return
            _on_order_print(order)

        def _on_buyer_preview_click(_e) -> None:
            order = current_buyer_order["value"]
            if not order:
                return
            _show_receipt_preview(order)

        btn_buyer_print.on_click = _on_buyer_print_click
        btn_buyer_preview.on_click = _on_buyer_preview_click

        buyer_detail_col = ft.Column(
            controls=[
                buyer_name_text,
                buyer_phone_text,
                buyer_seat_text,
                ft.Divider(height=1, color="#E0E0E0"),
                buyer_ticket_text,
                buyer_received_text,
            ],
            spacing=6,
            visible=False,
        )

        def update_buyer_info(order: Order) -> None:
            """구매자 정보 패널을 주문 데이터로 갱신한다."""
            view_state = build_buyer_event_view_state(
                order,
                load_ticket_product_names(settings_store),
                search_blocked=search_blocked_state["value"],
            )
            apply_buyer_event_dashboard_state(
                view_state,
                current_buyer_order=current_buyer_order,
                buyer_name_text=buyer_name_text,
                buyer_phone_text=buyer_phone_text,
                buyer_seat_text=buyer_seat_text,
                buyer_goods_text=buyer_goods_text,
                buyer_goods_hint=buyer_goods_hint,
                buyer_goods_cards=buyer_goods_cards,
                buyer_goods_count_text=buyer_goods_count_text,
                buyer_goods_count_badge=buyer_goods_count_badge,
                buyer_ticket_text=buyer_ticket_text,
                buyer_received_text=buyer_received_text,
                buyer_detail_col=buyer_detail_col,
                buyer_empty_hint=buyer_empty_hint,
                refresh_print_controls=refresh_print_controls,
            )

        def on_order_event(order: Order) -> None:
            """Application에서 전달된 주문 정보를 UI에 반영한다."""
            call_page_from_thread(page, lambda: _apply_order_update(order), search_refresh_stop)

        def _apply_order_update(order: Order) -> None:
            update_buyer_info(order)
            safe_page_update(page, search_refresh_stop)

        self._runtime_manager.set_order_listener(on_order_event)

        buyer_identity_panel = ft.Container(
            expand=True,
            height=318,
            bgcolor="#FFFFFF",
            border_radius=14,
            border=ft.border.all(1, "#D9E1EC"),
            padding=ft.padding.all(18),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text("구매자 정보", weight=ft.FontWeight.BOLD, size=16),
                            ft.Row(
                                controls=[btn_buyer_print, btn_buyer_preview],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    buyer_empty_hint,
                    buyer_detail_col,
                ],
                spacing=10,
                expand=True,
            ),
        )

        buyer_goods_panel = ft.Container(
            expand=True,
            height=318,
            bgcolor="#FFFFFF",
            border_radius=14,
            border=ft.border.all(1, "#D9E1EC"),
            padding=ft.padding.all(18),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Text("상품 정보", weight=ft.FontWeight.BOLD, size=16),
                            buyer_goods_count_badge,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    buyer_goods_hint,
                    buyer_goods_cards,
                ],
                spacing=12,
                expand=True,
            ),
        )

        buyer_info_panel = ft.Row(
            controls=[buyer_identity_panel, buyer_goods_panel],
            spacing=12,
            expand=True,
        )

        camera_container = ft.Container(
            content=camera_view,
            bgcolor="#000000",
            border_radius=8,
            width=400,
            height=300,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            alignment=ft.alignment.center,
            border=ft.border.all(1, "#D2D2D2"),
        )

        camera_column = ft.Column(
            controls=[camera_container],
            spacing=8,
        )

        special_rule_progress_panel = ft.Container(
            visible=False,
            content=next_special_rule_sections,
            padding=ft.padding.only(top=6, bottom=4),
            margin=ft.margin.only(top=6, bottom=4),
        )
        special_rule_progress_panel_ref["value"] = special_rule_progress_panel
        special_rule_progress_panel.visible = next_special_rule_sections.visible

        ticket_panel = build_ticket_dashboard_panel(
            top_controls_col=top_controls_col,
            buyer_info_panel=buyer_info_panel,
            camera_container=camera_column,
            special_rule_progress_panel=special_rule_progress_panel,
            order_search_panel=build_order_search_panel(
                search_field=search_field,
                filter_dropdown=filter_dropdown,
                filter_count_text=filter_count_text,
                on_search=do_search,
                btn_import_data=btn_import_data,
                btn_refresh=btn_refresh,
                search_feedback_text=search_feedback_text,
                search_result_header=search_result_header,
                search_result_list=search_result_list,
            ),
        )

        receipt_settings_panel = ft.Container(expand=True)

        def _refresh_sidebar_nav_visuals(push_update: bool = False) -> None:
            active_receipt_content = (
                content_host.content if current_tab["value"] == "receipt" and content_host.content is not None
                else receipt_settings_panel
            )
            tab_state = build_sidebar_tab_state(
                current_tab["value"],
                ticket_panel,
                active_receipt_content,
            )
            apply_sidebar_tab_view_state(
                tab_state,
                current_tab=current_tab,
                tab_key=current_tab["value"],
                content_host=content_host,
                btn_ticket_tab=btn_ticket_tab,
                btn_receipt_tab=btn_receipt_tab,
                ticket_hovered=sidebar_tab_hover_state["ticket"],
                receipt_hovered=sidebar_tab_hover_state["receipt"],
            )
            if push_update:
                safe_page_update(page, search_refresh_stop)

        def _on_sidebar_tab_hover(tab_key: str, event: ft.ControlEvent) -> None:
            sidebar_tab_hover_state[tab_key] = event.data == "true"
            _refresh_sidebar_nav_visuals(push_update=True)

        btn_ticket_tab.on_hover = lambda e: _on_sidebar_tab_hover("ticket", e)
        btn_receipt_tab.on_hover = lambda e: _on_sidebar_tab_hover("receipt", e)

        sidebar = build_dashboard_sidebar(
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
        )

        _apply_camera_focus_drawer(push_update=False)

        bootstrap_dashboard_page(
            runtime_manager=self._runtime_manager,
            page=page,
            current_tab=current_tab,
            ticket_panel=ticket_panel,
            receipt_settings_panel=receipt_settings_panel,
            content_host=content_host,
            shell_content=dashboard_overlay_host,
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
            sidebar=sidebar,
            btn_relogin=btn_relogin,
            btn_start_stop=btn_start_stop,
            on_start=on_start,
            on_stop=on_stop,
            set_tab=set_tab,
            on_runtime_event=on_runtime_event,
            watch_excel_changes=watch_excel_changes,
            cancel_scheduled_search_refresh=cancel_scheduled_search_refresh,
            closing_event=search_refresh_stop,
        )
        do_search(push_update=True)


def _setup_file_logging() -> None:
    """빌드 환경에서 파일 로그를 활성화한다. 콘솔 없는 exe에서 오류 추적용."""
    if not getattr(sys, "frozen", False):
        return
    try:
        from logging.handlers import RotatingFileHandler
        from project_paths import resolve_project_path
        log_path = resolve_project_path(".runtime/app.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            str(log_path), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        root_logger.addHandler(handler)
    except Exception:
        pass


def run_dashboard_app() -> None:
    _setup_file_logging()
    DashboardFletView().run()


if __name__ == "__main__":
    run_dashboard_app()
