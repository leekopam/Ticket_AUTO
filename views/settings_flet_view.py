
"""Flet receipt settings panel with drag-and-drop canvas editor."""
from __future__ import annotations

import copy
import shutil
import subprocess
import sys
import time
import logging
from dataclasses import replace
from pathlib import Path
from typing import Callable

# Add project root to sys.path so direct execution works
sys.path.append(str(Path(__file__).parent.parent))

import flet as ft

logger = logging.getLogger(__name__)

from models.receipt_canvas_model import (
    ReceiptCanvasDocument,
    ReceiptCanvasElement,
    create_default_document,
    make_element_id,
    paper_width_to_px,
)
from models.order_model import Order
from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
from models.ticket_debug_settings_model import TicketDebugSettings
from project_paths import (
    RESOURCE_PRODUCT_TEMPLATE_FILE,
    RESOURCE_RECEIPT_TEMPLATE_FILE,
    ensure_managed_sound_dir,
    ensure_managed_templates_dir,
    make_project_relative_path,
    resolve_runtime_file_path,
)
from services.receipt_canvas_editor_state import (
    clamp_element_position,
    preview_to_real,
    real_to_preview,
    remove_element_by_id,
    update_element_in_list,
)
from services.qr_generator_service import QrConfig, QrType, build_payload, calculate_qr_native_size
from services.receipt_canvas_store import ReceiptCanvasStore
from services.receipt_print_pipeline import print_test_receipt, render_receipt_preview_base64
from services.receipt_settings_store import ReceiptSettingsStore
from services.scan_success_sound_service import (
    coerce_scan_success_weight,
    equalize_scan_success_general_weights,
    format_scan_success_weight,
    format_scan_success_specific_counts,
    parse_scan_success_specific_counts,
    rebalance_scan_success_general_weights_after_edit,
    normalize_scan_success_general_weights,
)
from services.ticket_debug_settings_store import TicketDebugSettingsStore
from services.ticket_debug_tools_service import TicketDebugToolsService
from services.excel_service import ExcelService
from services.windows_audio_service import WindowsAudioService
from services.windows_printer_service import WindowsPrinterService


ICONS = getattr(ft, "Icons", ft.icons)
ALIGN_CENTER = ft.Alignment(0, 0)
ALIGN_CENTER_RIGHT = ft.Alignment(1, 0)
ALIGN_CENTER_LEFT = ft.Alignment(-1, 0)
ALIGN_TOP_CENTER = ft.Alignment(0, -1)
_IMAGE_FIT = getattr(ft, "ImageFit", getattr(ft, "BoxFit", None))
IMAGE_FIT_CONTAIN = getattr(_IMAGE_FIT, "CONTAIN", "contain")
IMAGE_FIT_FILL = getattr(_IMAGE_FIT, "FILL", "fill")

# 폰트 선택 옵션: (key, 표시명)
FONT_OPTIONS: list[tuple[str, str]] = [
    ("malgun", "맑은 고딕"),
    ("gulim", "굴림"),
    ("batang", "바탕"),
    ("nanumgothic", "나눔고딕"),
    ("arial", "Arial"),
    ("times", "Times New Roman"),
    ("calibri", "Calibri"),
    ("comic", "Comic Sans"),
    ("georgia", "Georgia"),
    ("verdana", "Verdana"),
    ("consolas", "Consolas"),
    ("impact", "Impact"),
]
FIELD_BINDINGS = [
    ("order_number", "주문번호"),
    ("buyer_name", "주문자명"),
    ("buyer_phone", "연락처"),
    ("seat", "좌석번호"),
    ("goods_lines", "상품목록"),
    ("ticket_lines", "티켓목록"),
]
DEFAULT_RECEIPT_LAYOUT_PATH = RESOURCE_RECEIPT_TEMPLATE_FILE.as_posix()
DEFAULT_PRODUCT_RECEIPT_LAYOUT_PATH = RESOURCE_PRODUCT_TEMPLATE_FILE.as_posix()
ACCENT_PRIMARY = "#39C5BB"
ACCENT_PRIMARY_DARK = "#1C8C84"
ACCENT_PRIMARY_DEEP = "#145F59"
ACCENT_PRIMARY_SOFT = "#E9F8F6"
ACCENT_PRIMARY_BORDER = "#A8E5DE"
STATUS_DANGER = "#D80000"
STATUS_INFO = "#0000FF"
STATUS_INFO_SOFT = "#E6EAFF"
STATUS_WARNING = "#FFE211"
STATUS_WARNING_SOFT = "#FFF8C4"
STATUS_WARNING_TEXT = "#7A6500"
STATUS_PINK = "#FFC0CB"
STATUS_PINK_SOFT = "#FFE9EF"
STATUS_PINK_TEXT = "#A34B68"
SETTINGS_PANEL_BG = "#E7EFF7"
SETTINGS_PANEL_BORDER = "#C6D5E4"
SETTINGS_CARD_BG = "#FFFFFF"
SETTINGS_CARD_BORDER = "#C9D8E7"
SETTINGS_INSET_BG = "#F3F8FD"
SCAN_SOUND_TRIGGER_OPTIONS: list[tuple[str, str]] = [
    ("always", "기본 랜덤"),
    ("every_n", "N 번마다"),
    ("specific_counts", "특정 번호"),
]


def _switch_theme_kwargs() -> dict[str, str]:
    return {
        "active_color": ACCENT_PRIMARY,
        "active_track_color": ACCENT_PRIMARY_SOFT,
        "track_outline_color": ACCENT_PRIMARY_BORDER,
        "focus_color": ACCENT_PRIMARY_SOFT,
        "hover_color": "#D7F3F0",
        "inactive_track_color": "#DDEAE8",
    }


def _coerce_picker_files(result: object) -> list[ft.FilePickerFile]:
    files = getattr(result, "files", None)
    if isinstance(files, list):
        return files
    if isinstance(result, list):
        return result
    return []


def _coerce_picker_path(result: object) -> str | None:
    path = getattr(result, "path", None)
    if isinstance(path, str) and path:
        return path
    if isinstance(result, str) and result:
        return result
    files = _coerce_picker_files(result)
    if files and getattr(files[0], "path", None):
        return str(files[0].path)
    return None


def _copy_scan_sound_file_to_resources(src_path: str) -> str:
    src = Path(src_path)
    sound_dir = ensure_managed_sound_dir()
    dest = sound_dir / src.name
    shutil.copy2(str(src), str(dest))
    return make_project_relative_path(dest)


def _build_scan_success_sound_rule(*, sound_path: str, name: str | None = None) -> ScanSuccessSoundRule:
    sound_file = Path(sound_path)
    return ScanSuccessSoundRule(
        name=(name or sound_file.name or "스캔 성공음").strip(),
        sound_path=str(sound_path).strip(),
        enabled=True,
        weight=100.0,
        trigger_type="always",
        trigger_value="",
    )


def _load_scan_success_sound_rules(settings: ReceiptSettings) -> list[ScanSuccessSoundRule]:
    rules = [
        replace(rule)
        for rule in getattr(settings, "qr_scan_success_sound_rules", [])
        if isinstance(rule, ScanSuccessSoundRule) and (rule.sound_path or "").strip()
    ]
    if rules:
        return normalize_scan_success_general_weights(rules)
    legacy_path = (getattr(settings, "qr_scan_success_sound_path", "") or "").strip()
    if not legacy_path:
        return []
    return [_build_scan_success_sound_rule(sound_path=legacy_path, name=Path(legacy_path).name or "기본 성공음")]


def _primary_scan_success_sound_path(rules: list[ScanSuccessSoundRule], fallback_path: str = "") -> str:
    for rule in rules:
        path = (rule.sound_path or "").strip()
        if path:
            return path
    return (fallback_path or "").strip()


def _scan_success_trigger_value_hint(trigger_type: str) -> str:
    if trigger_type == "every_n":
        return "예: 10 입력 시 10번마다 재생"
    if trigger_type == "specific_counts":
        return "예: 7, 77, 777 처럼 쉼표로 구분"
    return "기본 랜덤은 비워 두세요. 확률은 일반 랜덤 규칙끼리 자동 조정됩니다."


def _scan_success_rule_pool_summary(rules: list[ScanSuccessSoundRule]) -> str:
    enabled_general_rules = [
        rule
        for rule in rules
        if rule.enabled and rule.trigger_type == "always" and (rule.sound_path or "").strip()
    ]
    enabled_special_rules = [
        rule
        for rule in rules
        if rule.enabled and rule.trigger_type != "always" and (rule.sound_path or "").strip()
    ]
    if not rules:
        return "등록된 스캔 사운드 규칙이 없습니다."
    summary = f"등록 규칙 {len(rules)}개"
    if enabled_general_rules:
        summary += f" | 기본 랜덤 {len(enabled_general_rules)}개는 총 100% 안에서 자동 조정됩니다."
    if enabled_special_rules:
        summary += f" 특수 규칙 {len(enabled_special_rules)}개는 독립적으로 동작합니다."
    return summary


def _scan_success_rule_display_name(rule: ScanSuccessSoundRule, index: int | None = None) -> str:
    if (rule.name or "").strip():
        return rule.name.strip()
    file_name = Path(rule.sound_path).name or Path(rule.sound_path).stem
    if file_name:
        return file_name
    if index is not None:
        return f"규칙 {index + 1}"
    return "스캔 성공음"


def _can_live_apply_scan_success_weight(raw: object) -> bool:
    text = str(raw or "").strip()
    if text in {"", ".", "-", "+", "-.", "+."}:
        return False
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _scan_success_trigger_badge(trigger_type: str, *, selected: bool = False) -> ft.Container:
    if trigger_type == "specific_counts":
        text = "특정 번호"
        bgcolor = STATUS_PINK_SOFT if selected else "#FFF3F7"
        border_color = STATUS_PINK
        color = STATUS_PINK_TEXT
    elif trigger_type == "every_n":
        text = "N 번마다"
        bgcolor = STATUS_WARNING_SOFT if selected else "#FFFBE3"
        border_color = "#D8BC00"
        color = STATUS_WARNING_TEXT
    else:
        text = "기본랜덤"
        bgcolor = ACCENT_PRIMARY_SOFT if selected else "#F2FCFB"
        border_color = ACCENT_PRIMARY
        color = ACCENT_PRIMARY_DEEP
    return ft.Container(
        bgcolor=bgcolor,
        border=ft.border.all(1, border_color),
        border_radius=999,
        padding=ft.padding.symmetric(horizontal=8, vertical=3),
        content=ft.Text(text, size=10, weight=ft.FontWeight.BOLD, color=color),
    )


def _scan_success_weight_badge(rule: ScanSuccessSoundRule, *, selected: bool = False) -> ft.Container:
    enabled = bool(rule.enabled)
    return ft.Container(
        bgcolor="#DCEAFE" if selected and enabled else "#F8FAFC",
        border=ft.border.all(1, "#BFDBFE" if enabled else "#E2E8F0"),
        border_radius=999,
        padding=ft.padding.symmetric(horizontal=7, vertical=3),
        content=ft.Text(
            f"{format_scan_success_weight(rule.weight)}%",
            size=10,
            weight=ft.FontWeight.BOLD,
            color="#1E3A8A" if enabled else "#94A3B8",
        ),
    )


def _scan_success_shows_probability(trigger_type: str) -> bool:
    return trigger_type == "always"


def _scan_success_trigger_value_badge(
    rule: ScanSuccessSoundRule,
    *,
    selected: bool = False,
) -> ft.Container | None:
    enabled = bool(rule.enabled)
    if rule.trigger_type == "every_n":
        every_n = max(0, _coerce_int(rule.trigger_value, 0))
        if every_n <= 0:
            return None
        text = f"{every_n}번"
        tooltip = f"N 번마다 조건값: {every_n}"
    elif rule.trigger_type == "specific_counts":
        counts = parse_scan_success_specific_counts(rule.trigger_value)
        if not counts:
            return None
        if len(counts) == 1:
            text = f"{counts[0]}번"
        else:
            preview = "·".join(str(value) for value in counts[:3])
            text = preview if len(counts) <= 3 else f"{preview}+"
        tooltip = f"특정 번호 조건값: {format_scan_success_specific_counts(counts)}"
    else:
        return None

    return ft.Container(
        bgcolor="#E2E8F0" if selected and enabled else "#F8FAFC",
        border=ft.border.all(1, "#CBD5E1" if enabled else "#E2E8F0"),
        border_radius=999,
        padding=ft.padding.symmetric(horizontal=7, vertical=3),
        tooltip=tooltip,
        content=ft.Text(
            text,
            size=10,
            weight=ft.FontWeight.BOLD,
            color="#334155" if enabled else "#94A3B8",
        ),
    )


def _scan_success_rule_is_general_pool_member(rule: ScanSuccessSoundRule) -> bool:
    return bool((rule.sound_path or "").strip()) and rule.enabled and rule.trigger_type == "always"


def _rebalance_scan_success_rules(
    rules: list[ScanSuccessSoundRule],
    *,
    mode: str = "normalize",
    edited_index: int | None = None,
    edited_weight: float | None = None,
) -> list[ScanSuccessSoundRule]:
    if mode == "equal":
        return equalize_scan_success_general_weights(rules)
    if mode == "edit" and edited_index is not None and edited_weight is not None:
        return rebalance_scan_success_general_weights_after_edit(
            rules,
            edited_index=edited_index,
            edited_weight=edited_weight,
        )
    return normalize_scan_success_general_weights(rules)


def _build_scan_sound_path_row(
    *,
    sound_path_field: ft.TextField,
    btn_open_sound_path: ft.Control | None = None,
) -> ft.Control:
    if btn_open_sound_path is None:
        return sound_path_field
    return ft.Row(
        controls=[
            ft.Container(expand=True, content=sound_path_field),
            btn_open_sound_path,
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.END,
    )


def _open_path_in_explorer(raw_path: str) -> bool:
    path_text = (raw_path or "").strip()
    if not path_text:
        return False

    target = resolve_runtime_file_path(path_text).expanduser()
    try:
        if target.exists():
            if target.is_file():
                subprocess.Popen(["explorer", f"/select,{target}"])
            else:
                subprocess.Popen(["explorer", str(target)])
            return True

        parent = target.parent
        if parent.exists():
            subprocess.Popen(["explorer", str(parent)])
            return True
    except OSError:
        return False

    return False


def _build_scan_success_sound_management_panel(
    *,
    summary_text: ft.Text,
    sound_rule_list: ft.Control,
    sound_rule_name_field: ft.TextField,
    sound_path_field: ft.TextField,
    btn_open_sound_path: ft.Control | None = None,
    sound_rule_weight_field: ft.Control,
    sound_rule_trigger_type_dropdown: ft.Control,
    sound_rule_trigger_value_field: ft.Control,
    sound_rule_enabled_switch: ft.Control,
    btn_pick_sound: ft.Control,
    btn_preview_sound: ft.Control,
    btn_remove_sound_rule: ft.Control,
    btn_clear_sound_rules: ft.Control,
    compact: bool = False,
) -> ft.Column:
    weight_field_host = ft.Container(width=160, content=sound_rule_weight_field)
    trigger_value_field_host = ft.Container(expand=True, content=sound_rule_trigger_value_field)
    setattr(sound_rule_weight_field, "_visibility_host", weight_field_host)
    setattr(sound_rule_trigger_value_field, "_visibility_host", trigger_value_field_host)

    if compact:
        return ft.Column(
            controls=[
                ft.Text("등록된 음원", size=12, weight=ft.FontWeight.BOLD, color="#334155"),
                ft.Container(
                    height=228,
                    bgcolor="#FFFFFF",
                    border_radius=12,
                    border=ft.border.all(1, "#E2E8F0"),
                    padding=8,
                    content=sound_rule_list,
                ),
                ft.Row(
                    controls=[
                        ft.Container(expand=True, content=btn_pick_sound),
                        ft.Container(expand=True, content=btn_preview_sound),
                    ],
                    spacing=8,
                ),
                ft.Row(
                    controls=[
                        ft.Container(expand=True, content=btn_remove_sound_rule),
                        ft.Container(expand=True, content=btn_clear_sound_rules),
                    ],
                    spacing=8,
                ),
                ft.Text("선택된 음원", size=12, weight=ft.FontWeight.BOLD, color="#334155"),
                sound_rule_name_field,
                _build_scan_sound_path_row(
                    sound_path_field=sound_path_field,
                    btn_open_sound_path=btn_open_sound_path,
                ),
                ft.Row(
                    controls=[
                        weight_field_host,
                        trigger_value_field_host,
                        ft.Container(expand=True, content=sound_rule_trigger_type_dropdown),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    return ft.Column(
        controls=[
            ft.Text("등록된 음원", size=12, weight=ft.FontWeight.BOLD, color="#334155"),
            ft.Container(
                height=232,
                bgcolor="#FFFFFF",
                border_radius=12,
                border=ft.border.all(1, "#E2E8F0"),
                padding=8,
                content=sound_rule_list,
            ),
            ft.Row(
                controls=[btn_pick_sound, btn_preview_sound, btn_remove_sound_rule, btn_clear_sound_rules],
                spacing=8,
                wrap=True,
            ),
            ft.Text("선택된 음원", size=12, weight=ft.FontWeight.BOLD, color="#334155"),
            sound_rule_name_field,
            _build_scan_sound_path_row(
                sound_path_field=sound_path_field,
                btn_open_sound_path=btn_open_sound_path,
            ),
            ft.Row(
                controls=[
                    weight_field_host,
                    trigger_value_field_host,
                    sound_rule_trigger_type_dropdown,
                ],
                spacing=8,
                wrap=False,
            ),
        ],
        spacing=10,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )


def _reorder_scan_success_rules(
    rules: list[ScanSuccessSoundRule],
    *,
    from_index: int,
    to_index: int,
    selected_index: int | None = None,
) -> tuple[list[ScanSuccessSoundRule], int | None]:
    items = list(rules)
    if (
        from_index < 0
        or to_index < 0
        or from_index >= len(items)
        or to_index >= len(items)
        or from_index == to_index
    ):
        return items, selected_index

    moved_rule = items.pop(from_index)
    items.insert(to_index, moved_rule)

    if selected_index is None:
        return items, None
    if selected_index == from_index:
        return items, to_index
    if from_index < selected_index <= to_index:
        return items, selected_index - 1
    if to_index <= selected_index < from_index:
        return items, selected_index + 1
    return items, selected_index


def _build_scan_success_sound_rule_card(
    *,
    page: ft.Page,
    rule: ScanSuccessSoundRule,
    index: int,
    selected_index: int | None,
    drag_group: str,
    on_select: Callable[[int], None],
    on_edit_name: Callable[[int], None],
    on_toggle_enabled: Callable[[int, bool], None],
    on_reorder: Callable[[int, int], None],
) -> ft.Control:
    is_selected = index == selected_index
    base_bgcolor = "#EEF4FF" if is_selected else "#FFFFFF"
    base_border_color = "#6EA8FE" if is_selected else "#D9E2F2"
    drag_border_color = ACCENT_PRIMARY
    drag_bgcolor = "#E9F8F6" if is_selected else "#F5FCFB"

    card_container = ft.Container(
        bgcolor=base_bgcolor,
        border_radius=8,
        border=ft.border.all(1, base_border_color),
        padding=ft.padding.symmetric(horizontal=10, vertical=8),
    )

    def _set_drag_hover(active: bool) -> None:
        card_container.bgcolor = drag_bgcolor if active else base_bgcolor
        card_container.border = ft.border.all(1, drag_border_color if active else base_border_color)
        try:
            card_container.update()
        except AssertionError:
            pass

    def _handle_drag_accept(e: ft.DragTargetEvent) -> None:
        get_control = getattr(page, "get_control", None)
        if not callable(get_control):
            return
        src_control = get_control(e.src_id)
        if src_control is None:
            return
        try:
            source_index = int(getattr(src_control, "data", "-1"))
        except (TypeError, ValueError):
            return
        _set_drag_hover(False)
        on_reorder(source_index, index)

    drag_handle_icon = ft.Container(
        width=18,
        height=28,
        alignment=ALIGN_CENTER,
        border_radius=8,
        bgcolor="#DCEAFE" if is_selected else "#F1F5F9",
        tooltip="홀드 후 위아래로 이동해 순서를 바꾸세요.",
        content=ft.Icon(
            getattr(ICONS, "DRAG_INDICATOR_ROUNDED", getattr(ICONS, "DRAG_INDICATOR", ICONS.MORE_VERT_ROUNDED)),
            size=14,
            color="#2563EB" if is_selected else "#64748B",
        ),
    )

    drag_handle = ft.Draggable(
        group=drag_group,
        data=str(index),
        content=drag_handle_icon,
        content_feedback=ft.Container(
            width=20,
            height=30,
            alignment=ALIGN_CENTER,
            border_radius=8,
            bgcolor="#D7F3F0",
            border=ft.border.all(1, ACCENT_PRIMARY_BORDER),
            content=ft.Icon(
                getattr(ICONS, "DRAG_INDICATOR_ROUNDED", getattr(ICONS, "DRAG_INDICATOR", ICONS.MORE_VERT_ROUNDED)),
                size=14,
                color=ACCENT_PRIMARY_DEEP,
            ),
        ),
    )

    card_container.on_click = lambda _e: on_select(index)
    card_container.content = ft.Row(
        controls=[
            drag_handle,
            ft.IconButton(
                icon=ICONS.EDIT_ROUNDED,
                icon_size=16,
                tooltip="프로그램 표시 이름 수정",
                style=ft.ButtonStyle(
                    padding=ft.padding.all(0),
                    bgcolor="#EFF6FF" if is_selected else "#F8FAFC",
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
                on_click=lambda _e: on_edit_name(index),
            ),
            ft.Container(
                expand=True,
                tooltip=_scan_success_rule_display_name(rule, index),
                content=ft.Text(
                    _scan_success_rule_display_name(rule, index),
                    weight=ft.FontWeight.BOLD,
                    color="#0F172A" if rule.enabled else "#94A3B8",
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ),
            *(
                [trigger_value_badge]
                if (
                    trigger_value_badge := _scan_success_trigger_value_badge(
                        rule,
                        selected=is_selected,
                    )
                )
                is not None
                else []
            ),
            *(
                [
                    _scan_success_weight_badge(
                        rule,
                        selected=is_selected,
                    )
                ]
                if _scan_success_shows_probability(rule.trigger_type)
                else []
            ),
            _scan_success_trigger_badge(
                rule.trigger_type,
                selected=is_selected,
            ),
            ft.Switch(
                value=bool(rule.enabled),
                scale=0.72,
                tooltip="음원 활성화",
                **_switch_theme_kwargs(),
                on_change=lambda e: on_toggle_enabled(
                    index,
                    bool(getattr(getattr(e, "control", None), "value", False)),
                ),
            ),
        ],
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    return ft.DragTarget(
        group=drag_group,
        content=card_container,
        on_will_accept=lambda e: _set_drag_hover(str(getattr(e, "data", "")).lower() == "true"),
        on_leave=lambda _e: _set_drag_hover(False),
        on_accept=_handle_drag_accept,
    )


def _set_scan_sound_editor_visibility(
    *,
    trigger_type: str,
    sound_rule_weight_field: ft.Control,
    sound_rule_trigger_value_field: ft.Control,
) -> None:
    show_probability = _scan_success_shows_probability(trigger_type)
    weight_host = getattr(sound_rule_weight_field, "_visibility_host", sound_rule_weight_field)
    trigger_value_host = getattr(sound_rule_trigger_value_field, "_visibility_host", sound_rule_trigger_value_field)
    setattr(weight_host, "visible", show_probability)
    setattr(trigger_value_host, "visible", not show_probability)


def _preview_width_for_paper(paper_width: str) -> int:
    return 300 if paper_width == "58" else 420


def _coerce_int(value: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(str(value).strip()))
    except Exception:
        return max(minimum, int(default))


def _align_to_text_align(align: str) -> ft.TextAlign:
    if align == "center":
        return ft.TextAlign.CENTER
    if align == "right":
        return ft.TextAlign.RIGHT
    return ft.TextAlign.LEFT


def _align_to_container_align(align: str) -> ft.Alignment:
    if align == "center":
        return ALIGN_CENTER
    if align == "right":
        return ALIGN_CENTER_RIGHT
    return ALIGN_CENTER_LEFT


def _compose_bottom_anchor(
    *,
    margin_bottom_px: int,
    other_start_y: int | None,
    moving_bottom_y: int | None,
) -> int | None:
    """Compute stable bottom margin anchor for drag/resize sessions."""
    if int(margin_bottom_px) <= 0:
        return None
    candidates: list[int] = []
    if other_start_y is not None:
        candidates.append(int(other_start_y))
    if moving_bottom_y is not None:
        candidates.append(int(moving_bottom_y))
    if not candidates:
        return None
    return max(candidates)


def _build_settings_section_button_style(is_active: bool) -> ft.ButtonStyle:
    active_bg = "#DDE8FF"
    inactive_bg = "#00000000"
    return ft.ButtonStyle(
        bgcolor=active_bg if is_active else inactive_bg,
        color="#1B1B1B",
        shape=ft.RoundedRectangleBorder(radius=10),
        padding=ft.padding.symmetric(horizontal=14, vertical=12),
    )


def _apply_settings_section_switch(
    *,
    active_section: str,
    ticket_button: ft.TextButton,
    receipt_button: ft.TextButton,
    content_host: ft.Container,
    ticket_content: ft.Control,
    receipt_content: ft.Control,
) -> None:
    ticket_button.style = _build_settings_section_button_style(active_section == "ticket")
    receipt_button.style = _build_settings_section_button_style(active_section == "receipt")
    content_host.content = ticket_content if active_section == "ticket" else receipt_content


def _load_excel_product_names(
    excel_path: str | None = None,
    excel_service_cls: type[ExcelService] = ExcelService,
) -> list[str]:
    try:
        service = excel_service_cls(excel_path) if excel_path is not None else excel_service_cls()
        return list(service.get_product_names())
    except Exception:
        return []


def _attach_ticket_product_reload_hook(
    control: ft.Control,
    reload_fn: Callable[[], None],
) -> ft.Control:
    setattr(control, "_reload_ticket_product_options", reload_fn)
    return control


def _build_receipt_placeholder_panel(
    *,
    title_size: int,
    outer_border_radius: int,
    inner_border_color: str,
    description_text: str,
    subtitle_text: str | None = None,
    icon_size: int = 44,
) -> ft.Container:
    controls: list[ft.Control] = [
        ft.Text("영수증 양식 설정", size=title_size, weight=ft.FontWeight.BOLD),
    ]
    if subtitle_text:
        controls.append(ft.Text(subtitle_text, color="#666666"))
    controls.append(
        ft.Container(
            expand=True,
            border_radius=16,
            border=ft.border.all(1, inner_border_color),
            bgcolor="#F8FAFD",
            alignment=ALIGN_CENTER,
            content=ft.Column(
                controls=[
                    ft.Icon(ICONS.RECEIPT_LONG_ROUNDED, size=icon_size, color="#8AA4C8"),
                    ft.Text("영수증 양식 설정 영역", size=18, weight=ft.FontWeight.BOLD, color="#334155"),
                    ft.Text(description_text, size=12, color="#64748B"),
                ],
                spacing=8,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
        )
    )
    return ft.Container(
        expand=True,
        bgcolor="#FFFFFF",
        border_radius=outer_border_radius,
        padding=20,
        content=ft.Column(
            controls=controls,
            spacing=16,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )


def _build_app_settings_ticket_panel(
    *,
    sound_path_field: ft.TextField,
    btn_pick_sound: ft.Control,
    btn_preview_sound: ft.Control,
    btn_clear_sound: ft.Control,
    sound_rules_management_panel: ft.Control | None = None,
    debug_tools_panel: ft.Control | None = None,
    camera_selector_row: ft.Control | None = None,
    focus_mode_dropdown: ft.Control,
    manual_focus_value_field: ft.Control,
    focus_capability_badge: ft.Control | None = None,
    focus_section_title: str = "카메라 초점 설정",
    focus_description: str = "수동 초점 설정은 다음 앱 시작 후 적용됩니다.",
    settings_status_text: ft.Text,
    ticket_checkbox_list: ft.Control,
    show_title: bool = True,
) -> ft.Container:
    ticket_checkbox_controls = getattr(ticket_checkbox_list, "controls", None)
    ticket_checkbox_count = len(ticket_checkbox_controls) if isinstance(ticket_checkbox_controls, list) else 0
    ticket_checkbox_panel_height = min(320, max(116, 28 + (ticket_checkbox_count * 48)))

    sound_settings_card = ft.Container(
        bgcolor=SETTINGS_CARD_BG,
        border_radius=16,
        border=ft.border.all(1, SETTINGS_CARD_BORDER),
        padding=16,
        content=ft.Column(
            controls=[
                ft.Text("QR 스캔 완료 알림음", size=18, weight=ft.FontWeight.BOLD),
                ft.Text("스캔 성공 시 재생할 MP3 또는 WAV 파일을 선택합니다.", size=12, color="#64748B"),
                *(
                    [sound_rules_management_panel]
                    if sound_rules_management_panel is not None
                    else [
                        sound_path_field,
                        ft.Row(
                            controls=[btn_pick_sound, btn_preview_sound, btn_clear_sound],
                            spacing=8,
                            wrap=True,
                        ),
                    ]
                ),
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )
    focus_settings_card = ft.Container(
        bgcolor=SETTINGS_CARD_BG,
        border_radius=16,
        border=ft.border.all(1, SETTINGS_CARD_BORDER),
        padding=16,
        content=ft.Column(
            controls=[
                *(
                    [
                        ft.Text("스캔 카메라", size=18, weight=ft.FontWeight.BOLD),
                        ft.Text("QR 코드 스캔에 사용할 카메라를 선택합니다.", size=12, color="#64748B"),
                        camera_selector_row,
                        ft.Divider(height=1, color=SETTINGS_CARD_BORDER),
                    ]
                    if camera_selector_row is not None
                    else []
                ),
                ft.Text(focus_section_title, size=18, weight=ft.FontWeight.BOLD),
                ft.Text(focus_description, size=12, color="#64748B"),
                *([focus_capability_badge] if focus_capability_badge is not None else []),
                focus_mode_dropdown,
                manual_focus_value_field,
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )
    ticket_product_card = ft.Container(
        bgcolor=SETTINGS_CARD_BG,
        border_radius=16,
        border=ft.border.all(1, SETTINGS_CARD_BORDER),
        padding=16,
        content=ft.Column(
            controls=[
                ft.Text("티켓 상품 분류", size=18, weight=ft.FontWeight.BOLD),
                ft.Text("선택한 상품은 티켓 영역으로 분리됩니다.", size=12, color="#64748B"),
                ft.Container(
                    height=ticket_checkbox_panel_height,
                    bgcolor=SETTINGS_INSET_BG,
                    border_radius=12,
                    border=ft.border.all(1, "#E2E8F0"),
                    padding=12,
                    content=ticket_checkbox_list,
                ),
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )

    controls: list[ft.Control] = []
    if show_title:
        controls.append(ft.Text("티켓 확인 설정", size=26, weight=ft.FontWeight.BOLD))

    controls.extend([focus_settings_card, ticket_product_card, sound_settings_card])
    if debug_tools_panel is not None:
        controls.append(debug_tools_panel)
    scroll_host = ft.Column(
        expand=True,
        spacing=16,
        scroll=ft.ScrollMode.AUTO,
        controls=controls,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    panel = ft.Container(
        expand=True,
        bgcolor=SETTINGS_PANEL_BG,
        border_radius=18,
        border=ft.border.all(1, SETTINGS_PANEL_BORDER),
        padding=20,
        content=scroll_host,
    )
    setattr(panel, "_scroll_host", scroll_host)
    return panel


def _build_ticket_debug_tools_panel(
    *,
    debug_status_summary_text: ft.Control,
    debug_count_scan_success_switch: ft.Control,
    debug_duplicate_sound_switch: ft.Control,
    debug_offline_scan_switch: ft.Control,
    debug_qr_section: ft.Control,
) -> ft.Container:
    return ft.Container(
        bgcolor="#F8FAFD",
        border_radius=16,
        border=ft.border.all(1, "#D8E2F0"),
        padding=16,
        content=ft.Column(
            controls=[
                ft.Text("개발자 도구", size=18, weight=ft.FontWeight.BOLD),
                ft.Container(
                    bgcolor="#FFFFFF",
                    border_radius=12,
                    border=ft.border.all(1, "#DCE6F2"),
                    padding=12,
                    content=debug_status_summary_text,
                ),
                ft.Container(
                    bgcolor="#FFFFFF",
                    border_radius=12,
                    border=ft.border.all(1, "#E2E8F0"),
                    padding=12,
                    content=ft.Column(
                        controls=[
                            debug_count_scan_success_switch,
                            ft.Text(
                                "실제 처리완료 대신 QR 스캔 성공을 누적 카운트와 특수 규칙 진행도에 반영합니다.",
                                size=12,
                                color="#64748B",
                            ),
                        ],
                        spacing=6,
                    ),
                ),
                ft.Container(
                    bgcolor="#FFFFFF",
                    border_radius=12,
                    border=ft.border.all(1, "#E2E8F0"),
                    padding=12,
                    content=ft.Column(
                        controls=[
                            debug_duplicate_sound_switch,
                            ft.Text(
                                "이미 처리완료된 주문을 중복 스캔했을 때도 기본 랜덤 효과음을 재생합니다.",
                                size=12,
                                color="#64748B",
                            ),
                        ],
                        spacing=6,
                    ),
                ),
                ft.Container(
                    bgcolor="#FFF8F0",
                    border_radius=12,
                    border=ft.border.all(1, "#FDDCB5"),
                    padding=12,
                    content=ft.Column(
                        controls=[
                            debug_offline_scan_switch,
                            ft.Text(
                                "로그인 없이 QR 스캔 흐름을 테스트합니다. "
                                "브라우저를 시작하지 않고 HTTP로 주문번호를 추출하며, "
                                "위치폼 수령 완료 버튼 클릭은 건너뜁니다.",
                                size=12,
                                color="#92400E",
                            ),
                        ],
                        spacing=6,
                    ),
                ),
                debug_qr_section,
            ],
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )


def _build_receipt_ticket_settings_panel(
    *,
    scan_sound_path_field: ft.TextField,
    btn_pick_scan_sound: ft.Control,
    btn_preview_scan_sound: ft.Control,
    btn_clear_scan_sound: ft.Control,
    scan_sound_rules_management_panel: ft.Control | None = None,
    ticket_settings_status_text: ft.Text,
    ticket_checkbox_list: ft.Control,
) -> ft.Container:
    return ft.Container(
        expand=True,
        bgcolor=SETTINGS_PANEL_BG,
        border_radius=16,
        border=ft.border.all(1, SETTINGS_PANEL_BORDER),
        padding=20,
        content=ft.Column(
            controls=[
                ft.Text("티켓 확인 설정", size=24, weight=ft.FontWeight.BOLD),
                ft.Text("QR 스캔 완료 알림음과 티켓 분류 기준을 관리합니다.", color="#666666"),
                ft.Divider(height=18, color="#D9DDE5"),
                ft.Container(
                    bgcolor=SETTINGS_CARD_BG,
                    border_radius=14,
                    border=ft.border.all(1, SETTINGS_CARD_BORDER),
                    padding=16,
                    content=ft.Column(
                        controls=[
                            ft.Text("QR 스캔 완료 알림음", size=18, weight=ft.FontWeight.BOLD),
                            ft.Text("선택한 MP3 또는 WAV 파일이 수령 처리 성공 직후 재생됩니다.", size=12, color="#666666"),
                            *(
                                [scan_sound_rules_management_panel]
                                if scan_sound_rules_management_panel is not None
                                else [
                                    scan_sound_path_field,
                                    ft.Row(
                                        controls=[
                                            btn_pick_scan_sound,
                                            btn_preview_scan_sound,
                                            btn_clear_scan_sound,
                                        ],
                                        spacing=8,
                                        wrap=True,
                                    ),
                                ]
                            ),
                            ticket_settings_status_text,
                        ],
                        spacing=10,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                ),
                ft.Container(
                    bgcolor=SETTINGS_CARD_BG,
                    border_radius=14,
                    border=ft.border.all(1, SETTINGS_CARD_BORDER),
                    padding=16,
                    expand=True,
                    content=ft.Column(
                        controls=[
                            ft.Text("티켓 상품 분류", size=18, weight=ft.FontWeight.BOLD),
                            ft.Text("체크한 상품은 티켓 영역으로 분리되어 표시됩니다.", size=12, color="#666666"),
                            ft.Container(
                                bgcolor=SETTINGS_INSET_BG,
                                border_radius=12,
                                border=ft.border.all(1, "#E2E8F0"),
                                padding=12,
                                height=280,
                                content=ticket_checkbox_list,
                            ),
                        ],
                        spacing=10,
                        expand=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                ),
            ],
            spacing=16,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )


def _build_receipt_sidebar_output_panel(
    *,
    product_receipt_switch: ft.Control,
) -> ft.Container:
    return ft.Container(
        expand=True,
        bgcolor=SETTINGS_PANEL_BG,
        border_radius=18,
        border=ft.border.all(1, SETTINGS_PANEL_BORDER),
        padding=20,
        content=ft.Column(
            controls=[
                ft.Container(
                    bgcolor=SETTINGS_CARD_BG,
                    border_radius=16,
                    border=ft.border.all(1, SETTINGS_CARD_BORDER),
                    padding=16,
                    content=ft.Column(
                        controls=[
                            ft.Text("상품 영수증 추가 출력", size=18, weight=ft.FontWeight.BOLD),
                            ft.Text(
                                "일반 상품이 포함된 주문일 때만 상품 영수증을 추가 출력합니다.",
                                size=12,
                                color="#64748B",
                            ),
                            product_receipt_switch,
                        ],
                        spacing=10,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                ),
            ],
            spacing=14,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )


def _build_settings_section_shell(
    *,
    ticket_button: ft.TextButton,
    receipt_button: ft.TextButton,
    content_host: ft.Container,
    padding: int,
    spacing: int,
) -> ft.Container:
    return ft.Container(
        padding=ft.padding.all(padding),
        expand=True,
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[ticket_button, receipt_button],
                    spacing=8,
                    wrap=True,
                ),
                content_host,
            ],
            spacing=spacing,
            expand=True,
        ),
    )


def _build_single_settings_section_shell(
    *,
    content: ft.Control,
    padding: int,
) -> ft.Container:
    return ft.Container(
        padding=ft.padding.all(padding),
        expand=True,
        content=content,
    )


def _select_receipt_settings_section_content(
    *,
    active_section: str,
    receipt_section_mode: str,
    ticket_content: ft.Control,
    receipt_placeholder_content: ft.Control,
    receipt_editor_content: ft.Control,
) -> ft.Control:
    if active_section == "ticket":
        return ticket_content
    return receipt_placeholder_content if receipt_section_mode == "placeholder" else receipt_editor_content


def _wire_receipt_settings_navigation_handlers(
    *,
    page: ft.Page,
    bind_keyboard_events: bool,
    keyboard_handler,
    ticket_section_button: ft.Control,
    receipt_section_button: ft.Control,
    receipt_editor_tab_button: ft.Control,
    product_editor_tab_button: ft.Control,
    set_settings_section,
    set_editor_layout,
) -> None:
    if bind_keyboard_events:
        page.on_keyboard_event = keyboard_handler
    ticket_section_button.on_click = lambda _e: set_settings_section("ticket")
    receipt_section_button.on_click = lambda _e: set_settings_section("receipt")
    receipt_editor_tab_button.on_click = lambda _e: set_editor_layout("receipt")
    product_editor_tab_button.on_click = lambda _e: set_editor_layout("product")


def _build_receipt_settings_section_controls(
    *,
    initial_section: str,
) -> tuple[dict[str, str], ft.Container, ft.TextButton, ft.TextButton]:
    settings_section = {"value": "receipt" if initial_section == "receipt" else "ticket"}
    settings_content_host = ft.Container(expand=True)
    btn_ticket_settings_section = ft.TextButton("티켓 확인 설정", icon=ICONS.CONFIRMATION_NUMBER_ROUNDED)
    btn_receipt_layout_section = ft.TextButton("영수증 양식 설정", icon=ICONS.RECEIPT_LONG_ROUNDED)
    return settings_section, settings_content_host, btn_ticket_settings_section, btn_receipt_layout_section


def _initialize_receipt_settings_panel_state(
    *,
    current_doc_paper_width: str,
    selected_paper_width: str | None,
    current_template_text: ft.Text,
    editor_layout_label_text: str,
    layout_path_text: str,
    set_paper_width,
    apply_editor_layout,
    apply_settings_section,
) -> None:
    if current_doc_paper_width != str(selected_paper_width):
        set_paper_width(str(selected_paper_width or "80"), push_update=False)
    current_template_text.value = _format_active_template_label(
        editor_layout_label_text=editor_layout_label_text,
        layout_path_text=layout_path_text,
    )
    apply_editor_layout(push_update=False)
    apply_settings_section(push_update=False)


def _build_receipt_settings_panel_shell(
    *,
    show_section_tabs: bool,
    active_section: str,
    receipt_section_mode: str,
    ticket_content: ft.Control,
    receipt_placeholder_content: ft.Control,
    receipt_editor_content: ft.Control,
    settings_content_host: ft.Container,
    ticket_button: ft.TextButton,
    receipt_button: ft.TextButton,
    padding: int,
    spacing: int,
) -> ft.Control:
    if not show_section_tabs:
        return _build_single_settings_section_shell(
            content=_select_receipt_settings_section_content(
                active_section=active_section,
                receipt_section_mode=receipt_section_mode,
                ticket_content=ticket_content,
                receipt_placeholder_content=receipt_placeholder_content,
                receipt_editor_content=receipt_editor_content,
            ),
            padding=padding,
        )
    return _build_settings_section_shell(
        ticket_button=ticket_button,
        receipt_button=receipt_button,
        content_host=settings_content_host,
        padding=padding,
        spacing=spacing,
    )


def _resolve_selected_printer(
    *,
    printers: list[str],
    requested_printer: str,
    default_printer: str | None,
) -> str:
    if requested_printer in printers:
        return requested_printer
    if default_printer in printers:
        return str(default_printer)
    return printers[0] if printers else ""


def _normalize_json_layout_path(path: str, default_path: str) -> str:
    resolved = path.strip() or default_path
    if not resolved.lower().endswith(".json"):
        return default_path
    return resolved


def _load_layout_document_or_default(
    *,
    canvas_store: ReceiptCanvasStore,
    path: str,
    paper_width: str,
    fallback_path: str | None = None,
) -> ReceiptCanvasDocument:
    try:
        return canvas_store.load_layout(path)
    except Exception:
        pass
    if fallback_path and Path(fallback_path).exists():
        try:
            return canvas_store.load_layout(fallback_path)
        except Exception:
            pass
    return create_default_document(paper_width)


def _resolve_editor_default_layout_path(editor_layout_key: str) -> str:
    return (
        DEFAULT_PRODUCT_RECEIPT_LAYOUT_PATH
        if editor_layout_key == "product"
        else DEFAULT_RECEIPT_LAYOUT_PATH
    )


def _build_layout_document_for_save(
    doc: ReceiptCanvasDocument,
    *,
    paper_width: str,
) -> ReceiptCanvasDocument:
    normalized_paper_width = "58" if str(paper_width) == "58" else "80"
    return replace(
        doc,
        meta=replace(
            doc.meta,
            paper_width=normalized_paper_width,
            canvas_width_px=paper_width_to_px(normalized_paper_width),  # 항상 203 DPI 기준
        ),
    )


def _apply_loaded_layout_document(
    *,
    doc: ReceiptCanvasDocument,
    default_layout_path: str,
    set_doc,
    set_layout_path,
    set_selected_id,
    paper_width_dropdown,
    save_layout,
    refresh_all,
) -> None:
    set_doc(doc)
    set_layout_path(default_layout_path)
    paper_width_dropdown.value = doc.meta.paper_width
    set_selected_id(None)
    save_layout(default_layout_path, doc)
    refresh_all()


def _attach_page_service(page: ft.Page, service) -> None:
    # Newer Flet versions mount FilePicker via the page service registry,
    # while older versions accepted it in overlay.
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


def _attach_page_services(page: ft.Page, *services) -> None:
    for service in services:
        _attach_page_service(page, service)


def _build_editor_layout_tab_style(*, is_active: bool) -> ft.ButtonStyle:
    active_bg = ACCENT_PRIMARY
    inactive_bg = "#F5FCFB"
    active_border = ft.border.all(1, ACCENT_PRIMARY)
    inactive_border = ft.border.all(1, "#D6E1EE")
    return ft.ButtonStyle(
        bgcolor=active_bg if is_active else inactive_bg,
        color="#FFFFFF" if is_active else "#203047",
        shape=ft.RoundedRectangleBorder(radius=12),
        side=active_border if is_active else inactive_border,
        padding=ft.padding.symmetric(horizontal=18, vertical=13),
    )


def _apply_editor_layout_tab_styles(
    *,
    active_layout: str,
    receipt_tab_button: ft.TextButton,
    product_tab_button: ft.TextButton,
) -> None:
    receipt_tab_button.style = _build_editor_layout_tab_style(is_active=active_layout == "receipt")
    product_tab_button.style = _build_editor_layout_tab_style(is_active=active_layout == "product")
    receipt_tab_button.icon_color = "#FFFFFF" if active_layout == "receipt" else "#54708F"
    product_tab_button.icon_color = "#FFFFFF" if active_layout == "product" else "#54708F"


def _reset_editor_layout_transient_state(
    *,
    set_selected_id,
    set_active_binding_target,
    state: dict[str, object],
) -> None:
    set_selected_id(None)
    set_active_binding_target(None)
    state["inline_edit_id"] = None


def _format_active_template_label(*, editor_layout_label_text: str, layout_path_text: str) -> str:
    return f"활성 {editor_layout_label_text} 템플릿: {layout_path_text}"


def _sync_editor_layout_display(
    *,
    current_doc_paper_width: str,
    selected_paper_width: str | None,
    current_template_text: ft.Text,
    editor_layout_label_text: str,
    layout_path_text: str,
    set_paper_width,
    refresh_all,
) -> None:
    current_template_text.value = _format_active_template_label(
        editor_layout_label_text=editor_layout_label_text,
        layout_path_text=layout_path_text,
    )
    if current_doc_paper_width != str(selected_paper_width or "80"):
        set_paper_width(str(selected_paper_width or "80"), push_update=False)
    else:
        refresh_all(push_update=False)


def _get_editor_layout_doc(*, state: dict[str, object], active_layout: str) -> ReceiptCanvasDocument:
    docs = state["docs"]  # type: ignore[assignment]
    return docs[active_layout]  # type: ignore[index, return-value]


def _set_editor_layout_doc(
    *,
    state: dict[str, object],
    active_layout: str,
    doc: ReceiptCanvasDocument,
) -> None:
    docs = dict(state["docs"])  # type: ignore[arg-type]
    docs[active_layout] = doc
    state["docs"] = docs


def _get_editor_selected_id(*, state: dict[str, object]) -> str | None:
    return state["selected_id"]  # type: ignore[return-value]


def _set_editor_selected_id(*, state: dict[str, object], value: str | None) -> None:
    if state["selected_id"] != value:
        state["inline_edit_id"] = None
    state["selected_id"] = value


def _get_editor_active_binding_target(*, state: dict[str, object]) -> str | None:
    return state["active_binding_target"]  # type: ignore[return-value]


def _set_editor_active_binding_target(*, state: dict[str, object], value: str | None) -> None:
    state["active_binding_target"] = value


def _get_editor_layout_path(*, state: dict[str, object], active_layout: str) -> str:
    layout_paths = state["layout_paths"]  # type: ignore[assignment]
    return layout_paths[active_layout]  # type: ignore[index, return-value]


def _set_editor_layout_path(
    *,
    state: dict[str, object],
    active_layout: str,
    path: str,
    current_template_text: ft.Text,
    editor_layout_label_text: str,
) -> None:
    layout_paths = dict(state["layout_paths"])  # type: ignore[arg-type]
    layout_paths[active_layout] = path
    state["layout_paths"] = layout_paths
    current_template_text.value = _format_active_template_label(
        editor_layout_label_text=editor_layout_label_text,
        layout_path_text=path,
    )


def _build_canvas_margin_overlay_controls(
    *,
    preview_width: int,
    preview_height: int,
    margin_top_preview: int,
    margin_bottom_preview: int,
) -> list[ft.Control]:
    controls: list[ft.Control] = []
    if margin_top_preview > 0:
        controls.append(
            ft.Container(
                left=0,
                top=0,
                width=preview_width,
                height=margin_top_preview,
                bgcolor="#E8E0D0",
                opacity=0.55,
            )
        )
    if margin_bottom_preview > 0:
        controls.append(
            ft.Container(
                left=0,
                top=preview_height - margin_bottom_preview,
                width=preview_width,
                height=margin_bottom_preview,
                bgcolor="#E8E0D0",
                opacity=0.55,
            )
        )
    return controls


def _format_canvas_meta_text(
    *,
    preview_width: int,
    preview_height: int,
    real_canvas_width: int,
    margin_top: int,
    margin_bottom: int,
) -> str:
    return (
        f"편집 캔버스 ({preview_width}x{preview_height}) / 실제폭 {real_canvas_width}px"
        + (f" / 여백 상:{margin_top} 하:{margin_bottom}" if margin_top or margin_bottom else "")
    )


def _build_property_panel_empty_state() -> ft.Column:
    return ft.Column(
        controls=[
            ft.Text("속성", size=20, weight=ft.FontWeight.BOLD),
            ft.Text("캔버스에서 요소를 선택하세요.", color="#666666"),
        ],
        spacing=8,
    )


def _apply_property_panel_controls(
    *,
    property_panel: ft.Container,
    controls: list[ft.Control],
) -> None:
    property_panel.content = ft.Column(controls=controls, spacing=8, scroll=ft.ScrollMode.AUTO)


def _build_property_panel_base_controls(
    *,
    selected: ReceiptCanvasElement,
    x_field: ft.Control,
    y_field: ft.Control,
    w_field: ft.Control,
    h_field: ft.Control,
) -> list[ft.Control]:
    return [
        ft.Text("속성", size=20, weight=ft.FontWeight.BOLD),
        ft.Text(f"요소 ID: {selected.id}", size=12, color="#666666", selectable=True),
        ft.Row(controls=[x_field, y_field, w_field, h_field], spacing=8),
        ft.Divider(),
    ]


def _build_property_panel_text_controls(
    *,
    text_template_field: ft.Control,
    font_family_dropdown: ft.Control,
    font_size_field: ft.Control,
    bold_btn: ft.Control,
    align_left_btn: ft.Control,
    align_center_btn: ft.Control,
    align_right_btn: ft.Control,
) -> list[ft.Control]:
    return [
        text_template_field,
        ft.Row(
            [
                font_family_dropdown,
                font_size_field,
                bold_btn,
                align_left_btn,
                align_center_btn,
                align_right_btn,
            ],
            spacing=4,
        ),
    ]


def _build_property_panel_image_controls(
    *,
    image_path_field: ft.Control,
    replace_image_button: ft.Control,
    preserve_ratio_switch: ft.Control,
) -> list[ft.Control]:
    return [
        image_path_field,
        ft.Row(
            controls=[replace_image_button, preserve_ratio_switch],
            spacing=10,
        ),
    ]


def _build_property_panel_qr_controls(
    *,
    qr_data_field: ft.Control,
    box_size_field: ft.Control,
) -> list[ft.Control]:
    return [qr_data_field, box_size_field]


def _build_property_panel_divider_controls(
    *,
    line_style_dropdown: ft.Control,
    line_thickness_field: ft.Control,
    divider_text_field: ft.Control,
    div_font_family_dropdown: ft.Control,
    div_font_size_field: ft.Control,
    div_bold_btn: ft.Control,
    visibility_tag_dropdown: ft.Control,
) -> list[ft.Control]:
    return [
        ft.Row([line_style_dropdown, line_thickness_field], spacing=10),
        divider_text_field,
        ft.Row([div_font_family_dropdown, div_font_size_field, div_bold_btn], spacing=4),
        visibility_tag_dropdown,
    ]


def _build_canvas_preview_controls(
    *,
    preview_width: int,
    preview_height: int,
    margin_top_preview: int,
    margin_bottom_preview: int,
    visible_elements: list[ReceiptCanvasElement],
    selected_element: ReceiptCanvasElement | None,
    snap_guides: list[ft.Control],
    build_element_preview,
    build_resize_handles,
) -> list[ft.Control]:
    canvas_controls = _build_canvas_margin_overlay_controls(
        preview_width=preview_width,
        preview_height=preview_height,
        margin_top_preview=margin_top_preview,
        margin_bottom_preview=margin_bottom_preview,
    )
    canvas_controls.extend(build_element_preview(element) for element in visible_elements)
    if selected_element and selected_element.visible:
        canvas_controls.extend(build_resize_handles(selected_element))
    canvas_controls.extend(snap_guides)
    return canvas_controls


def _apply_canvas_stack_view_state(
    *,
    canvas_stack: ft.Stack,
    canvas_controls: list[ft.Control],
    preview_width: int,
    preview_height: int,
    state: dict[str, object],
) -> None:
    canvas_stack.controls = canvas_controls
    canvas_stack.width = preview_width
    canvas_stack.height = preview_height
    state["canvas_stack"] = canvas_stack


def _apply_canvas_frame_body_view_state(
    *,
    canvas_frame_body: ft.Container,
    canvas_stack: ft.Stack,
    preview_width: int,
    preview_height: int,
) -> None:
    canvas_frame_body.width = preview_width
    canvas_frame_body.height = preview_height
    canvas_frame_body.content = canvas_stack


def _apply_canvas_scroll_view_state(
    *,
    scrollable_canvas: ft.Column,
    scroll_gutter: ft.Control | None,
    preview_height: int,
    viewport_height: int,
    max_viewport_height: int,
) -> None:
    needs_scroll = preview_height > max_viewport_height
    scrollable_canvas.height = viewport_height
    scrollable_canvas.scroll = ft.ScrollMode.AUTO if needs_scroll else None
    if scroll_gutter is not None:
        scroll_gutter.width = 14 if needs_scroll else 0
        scroll_gutter.height = preview_height


def _require_selected_id(
    *,
    selected_id: str | None,
    show_status,
    missing_message: str,
) -> str | None:
    if selected_id:
        return selected_id
    show_status(missing_message)
    return None


def _require_selected_element(
    *,
    selected_element: ReceiptCanvasElement | None,
    show_status,
    missing_message: str,
) -> ReceiptCanvasElement | None:
    if selected_element is not None:
        return selected_element
    show_status(missing_message)
    return None


def _remove_selected_element_from_elements(
    *,
    elements: list[ReceiptCanvasElement],
    selected_id: str,
) -> list[ReceiptCanvasElement]:
    return remove_element_by_id(elements, selected_id)


def _apply_selected_alignment_action(
    *,
    element: ReceiptCanvasElement,
    align: str,
    upsert_element,
    refresh_all,
) -> ReceiptCanvasElement:
    updated = replace(element, align=align)
    upsert_element(updated)
    refresh_all()
    return updated


def _element_display_content(el) -> str:
    """요소 목록 테이블에 표시할 내용 요약 문자열."""
    if el.type == "text":
        return el.text_template[:32] if el.text_template else "(빈 텍스트)"
    if el.type == "image":
        return Path(el.asset_path).name if el.asset_path else "(이미지)"
    if el.type == "divider":
        return el.text_template[:32] if el.text_template else "(구분선)"
    if el.type == "qr":
        return el.data_template[:32] if el.data_template else "(QR 코드)"
    return el.type


_ELEMENT_TYPE_BADGE_COLOR: dict[str, tuple[str, str]] = {
    "text":    ("#E3F2FD", "#1565C0"),
    "image":   ("#E8F5E9", "#2E7D32"),
    "divider": ("#EEEEEE", "#616161"),
    "qr":      ("#F3E5F5", "#6A1B9A"),
}
_ELEMENT_TYPE_LABEL: dict[str, str] = {
    "text": "텍스트", "image": "이미지", "divider": "구분선", "qr": "QR",
}


def _refresh_editor_view_state(
    *,
    refresh_canvas,
    refresh_property_panel,
    page: ft.Page,
    push_update: bool = True,
) -> None:
    refresh_canvas()
    refresh_property_panel()
    if push_update:
        page.update()


def _remove_canvas_stack_controls(
    *,
    canvas_stack: ft.Stack | None,
    controls: list[ft.Control] | None,
) -> None:
    if canvas_stack is None or not controls:
        return
    for control in list(controls):
        if control in canvas_stack.controls:
            canvas_stack.controls.remove(control)


def _replace_canvas_snap_guides(
    *,
    state: dict[str, object],
    canvas_stack: ft.Stack | None,
    guides: list[dict],
    build_guide_lines,
) -> list[ft.Control]:
    old_guides = state.get("snap_guides")
    _remove_canvas_stack_controls(
        canvas_stack=canvas_stack,
        controls=old_guides if isinstance(old_guides, list) else None,
    )
    new_guide_ctrls = build_guide_lines(guides)
    state["snap_guides"] = new_guide_ctrls
    if canvas_stack is not None and new_guide_ctrls:
        canvas_stack.controls.extend(new_guide_ctrls)
    return new_guide_ctrls


def _clear_canvas_insertion_indicator(
    *,
    state: dict[str, object],
    canvas_stack: ft.Stack | None,
) -> None:
    indicator = state.get("insertion_indicator")
    if canvas_stack is not None and indicator is not None and indicator in canvas_stack.controls:
        canvas_stack.controls.remove(indicator)
    state["insertion_indicator"] = None


def _update_canvas_insertion_indicator(
    *,
    state: dict[str, object],
    canvas_stack: ft.Stack | None,
    slot_y: int | None,
    build_insertion_indicator,
) -> ft.Control | None:
    _clear_canvas_insertion_indicator(
        state=state,
        canvas_stack=canvas_stack,
    )
    if slot_y is None:
        state["insertion_target_y"] = None
        return None

    indicator = build_insertion_indicator(slot_y)
    state["insertion_indicator"] = indicator
    state["insertion_target_y"] = slot_y
    if canvas_stack is not None:
        canvas_stack.controls.append(indicator)
    return indicator


def _consume_canvas_insertion_target(*, state: dict[str, object]) -> int | None:
    target = state.get("insertion_target_y")
    state["insertion_target_y"] = None
    return int(target) if isinstance(target, int) else None


def _reset_resize_interaction_state(*, state: dict[str, object]) -> None:
    state["snap_guides"] = []
    state["resize_bottom_start_y"] = None
    state["resize_pointer_start_gx"] = None
    state["resize_pointer_start_gy"] = None


def _reset_drag_interaction_state(*, state: dict[str, object]) -> None:
    state["snap_guides"] = []
    state["drag_bottom_start_y"] = None
    state["drag_pointer_start_gx"] = None
    state["drag_pointer_start_gy"] = None


def _should_ignore_canvas_background_tap(
    *,
    last_element_tap_time: float,
    current_time: float,
    guard_seconds: float = 0.05,
) -> bool:
    return current_time - float(last_element_tap_time) < guard_seconds


def _clear_canvas_selection(
    *,
    set_selected_id,
    set_active_binding_target,
) -> None:
    set_selected_id(None)
    set_active_binding_target(None)


def _resolve_binding_target_for_element_type(element_type: str) -> str:
    return "data_template" if element_type == "qr" else "text_template"


def _should_start_inline_edit_on_tap(
    *,
    already_selected: bool,
    element_type: str,
    inline_edit_id: str | None,
    element_id: str,
) -> bool:
    return (
        already_selected
        and element_type in ("text", "divider")
        and inline_edit_id != element_id
    )


def _apply_element_tap_selection(
    *,
    state: dict[str, object],
    element: ReceiptCanvasElement,
    current_selected_id: str | None,
    tap_time: float,
    set_canvas_focus,
    set_selected_id,
    set_active_binding_target,
) -> None:
    state["last_element_tap_time"] = tap_time
    set_canvas_focus(True)
    already_selected = current_selected_id == element.id
    set_selected_id(element.id)
    set_active_binding_target(_resolve_binding_target_for_element_type(element.type))
    if _should_start_inline_edit_on_tap(
        already_selected=already_selected,
        element_type=element.type,
        inline_edit_id=state.get("inline_edit_id"),  # type: ignore[arg-type]
        element_id=element.id,
    ):
        state["inline_edit_id"] = element.id


def _apply_element_double_tap_edit(
    *,
    state: dict[str, object],
    element: ReceiptCanvasElement,
    set_canvas_focus,
    set_selected_id,
) -> bool:
    if element.type not in ("text", "divider"):
        return False
    set_selected_id(element.id)
    set_canvas_focus(True)
    state["inline_edit_id"] = element.id
    return True


def _update_element_text_template(
    element: ReceiptCanvasElement,
    *,
    text_template: str,
) -> ReceiptCanvasElement:
    return replace(
        element,
        text_template=text_template,
    )


def _build_binding_insert_text(
    field_key: str,
    *,
    field_bindings: list[tuple[str, str]],
) -> str:
    token = f"{{{{{field_key}}}}}"
    label = next((lbl for fk, lbl in field_bindings if fk == field_key), field_key)
    return f"{label}: {token}"


def _apply_binding_insert_to_selected_element(
    *,
    selected_element: ReceiptCanvasElement,
    insert_text: str,
    active_binding_target: str | None,
    resolve_binding_target_for_element_type,
) -> ReceiptCanvasElement:
    target = active_binding_target
    if target is None:
        target = resolve_binding_target_for_element_type(selected_element.type)
    if target == "data_template" and selected_element.type == "qr":
        return replace(
            selected_element,
            data_template=f"{selected_element.data_template}{insert_text}",
        )
    return _update_element_text_template(
        selected_element,
        text_template=f"{selected_element.text_template}{insert_text}",
    )


def _build_new_binding_text_element(
    *,
    new_text_element: ReceiptCanvasElement,
    insert_text: str,
) -> ReceiptCanvasElement:
    return _update_element_text_template(
        new_text_element,
        text_template=insert_text,
    )


def _update_text_element_properties(
    element: ReceiptCanvasElement,
    *,
    text_template: str,
    font_size: int,
    bold: bool,
    font_family: str,
) -> ReceiptCanvasElement:
    return replace(
        element,
        text_template=text_template,
        font_size=max(8, int(font_size)),
        bold=bool(bold),
        font_family=font_family or "malgun",
    )


def _update_image_element_properties(
    element: ReceiptCanvasElement,
    *,
    asset_path: str,
    preserve_ratio: bool,
) -> ReceiptCanvasElement:
    return replace(
        element,
        asset_path=asset_path,
        preserve_ratio=bool(preserve_ratio),
    )


def _update_qr_element_properties(
    element: ReceiptCanvasElement,
    *,
    data_template: str,
    box_size: int,
    qr_size_calculator=calculate_qr_native_size,
) -> ReceiptCanvasElement:
    normalized_box_size = max(1, int(box_size))
    new_w, new_h = element.w, element.h
    if normalized_box_size != element.box_size and data_template.strip():
        native = qr_size_calculator(data_template.strip(), normalized_box_size)
        new_w = native
        new_h = native
    return replace(
        element,
        data_template=data_template,
        box_size=normalized_box_size,
        w=new_w,
        h=new_h,
    )


def _update_divider_element_properties(
    element: ReceiptCanvasElement,
    *,
    line_style: str,
    line_thickness: int,
    text_template: str,
    font_size: int,
    bold: bool,
    font_family: str,
    visibility_tag: str,
) -> ReceiptCanvasElement:
    return replace(
        element,
        line_style=line_style or "solid",
        line_thickness=max(1, int(line_thickness)),
        text_template=text_template,
        font_size=max(8, int(font_size)),
        bold=bool(bold),
        font_family=font_family or "malgun",
        visibility_tag=visibility_tag or "",
    )


def _commit_common_dimension_update(
    *,
    current: ReceiptCanvasElement | None,
    x_value: str,
    y_value: str,
    w_value: str,
    h_value: str,
    coerce_int,
    update_common_dimensions,
    upsert_element,
    refresh_all,
) -> ReceiptCanvasElement | None:
    if not current:
        return None
    updated = update_common_dimensions(
        current,
        x=coerce_int(x_value, current.x),
        y=coerce_int(y_value, current.y),
        w=coerce_int(w_value, current.w, minimum=10),
        h=coerce_int(h_value, current.h, minimum=10),
        align=current.align,
    )
    upsert_element(updated)
    refresh_all()
    return updated


def _commit_text_property_update(
    *,
    current: ReceiptCanvasElement | None,
    text_template: str,
    font_size_value: str,
    bold: bool,
    font_family: str,
    coerce_int,
    upsert_element,
    refresh_canvas,
    push_update,
) -> ReceiptCanvasElement | None:
    if not current or current.type != "text":
        return None
    updated = _update_text_element_properties(
        current,
        text_template=text_template,
        font_size=coerce_int(font_size_value, current.font_size, minimum=8),
        bold=bold,
        font_family=font_family,
    )
    upsert_element(updated)
    refresh_canvas()
    push_update()
    return updated


def _commit_image_property_update(
    *,
    current: ReceiptCanvasElement | None,
    asset_path: str,
    preserve_ratio: bool,
    upsert_element,
    refresh_canvas,
    push_update,
) -> ReceiptCanvasElement | None:
    if not current or current.type != "image":
        return None
    updated = _update_image_element_properties(
        current,
        asset_path=asset_path,
        preserve_ratio=preserve_ratio,
    )
    upsert_element(updated)
    refresh_canvas()
    push_update()
    return updated


def _commit_qr_property_update(
    *,
    current: ReceiptCanvasElement | None,
    data_template: str,
    box_size_value: str,
    coerce_int,
    upsert_element,
    refresh_all,
    push_update,
) -> ReceiptCanvasElement | None:
    if not current or current.type != "qr":
        return None
    updated = _update_qr_element_properties(
        current,
        data_template=data_template,
        box_size=coerce_int(box_size_value, current.box_size, minimum=1),
    )
    upsert_element(updated)
    refresh_all()
    push_update()
    return updated


def _commit_divider_property_update(
    *,
    current: ReceiptCanvasElement | None,
    line_style: str,
    line_thickness_value: str,
    text_template: str,
    font_size_value: str,
    bold: bool,
    font_family: str,
    visibility_tag: str,
    coerce_int,
    upsert_element,
    refresh_canvas,
    push_update,
) -> ReceiptCanvasElement | None:
    if not current or current.type != "divider":
        return None
    updated = _update_divider_element_properties(
        current,
        line_style=line_style,
        line_thickness=coerce_int(line_thickness_value, current.line_thickness, minimum=1),
        text_template=text_template,
        font_size=coerce_int(font_size_value, current.font_size, minimum=8),
        bold=bold,
        font_family=font_family,
        visibility_tag=visibility_tag,
    )
    upsert_element(updated)
    refresh_canvas()
    push_update()
    return updated


def _build_receipt_editor_workspace(
    *,
    btn_receipt_editor_tab: ft.Control,
    btn_product_editor_tab: ft.Control,
    property_panel: ft.Control,
    canvas_host: ft.Control,
) -> ft.Container:
    canvas_container = ft.Container(
        bgcolor="#F7F7F7",
        border_radius=8,
        padding=10,
        content=canvas_host,
        alignment=ALIGN_TOP_CENTER,
    )

    property_wrapper = ft.Container(
        bgcolor="#F7F7F7",
        border_radius=8,
        padding=10,
        content=property_panel,
        alignment=ALIGN_TOP_CENTER,
    )

    return ft.Container(
        expand=3,
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[btn_receipt_editor_tab, btn_product_editor_tab],
                    spacing=8,
                    wrap=True,
                ),
                property_wrapper,
                canvas_container,
            ],
            spacing=12,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )


def _build_receipt_editor_split_layout(
    *,
    left_controls_panel: ft.Control,
    right_workspace: ft.Control,
) -> ft.Row:
    return ft.Row(
        controls=[
            left_controls_panel,
            right_workspace,
        ],
        spacing=12,
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )


def _build_receipt_editor_left_controls_panel(
    *,
    printer_dropdown: ft.Control,
    paper_width_dropdown: ft.Control,
    dpi_dropdown: ft.Control,
    margin_top_field: ft.Control,
    margin_bottom_field: ft.Control,
    btn_save: ft.Control,
    btn_save_as: ft.Control,
    btn_load: ft.Control,
    btn_test_print: ft.Control,
    btn_test_preview: ft.Control,
    current_template_text: ft.Control,
    btn_add_text: ft.Control,
    btn_add_image: ft.Control,
    btn_add_divider: ft.Control,
    btn_delete: ft.Control,
    field_chip_row: ft.Control,
    status_text: ft.Control,
    qr_expansion_tile: ft.Control,
) -> ft.Container:
    return ft.Container(
        bgcolor="#FFFFFF",
        border_radius=8,
        padding=12,
        expand=2,
        content=ft.Column(
            controls=[
                ft.Text("영수증 양식 편집기", size=24, weight=ft.FontWeight.BOLD),
                ft.Row(
                    controls=[
                        printer_dropdown,
                        paper_width_dropdown,
                        dpi_dropdown,
                        margin_top_field,
                        margin_bottom_field,
                    ],
                    spacing=12,
                    wrap=True,
                ),
                ft.Row(
                    controls=[
                        btn_save,
                        btn_save_as,
                        btn_load,
                        btn_test_print,
                        btn_test_preview,
                    ],
                    spacing=12,
                    wrap=True,
                ),
                current_template_text,
                ft.Divider(),
                ft.Row(
                    controls=[
                        btn_add_text,
                        btn_add_image,
                        btn_add_divider,
                        btn_delete,
                    ],
                    spacing=8,
                    wrap=True,
                ),
                ft.Text("필드칩: 클릭하면 선택한 텍스트/QR 템플릿에 변수 삽입", size=12, color="#666666"),
                field_chip_row,
                status_text,
                ft.Divider(),
                qr_expansion_tile,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
    )


def build_receipt_settings_panel(
    page: ft.Page,
    *,
    store_path: str = ".runtime/receipt_settings.json",
    printer_service: WindowsPrinterService | None = None,
    audio_service: WindowsAudioService | None = None,
    initial_section: str = "ticket",
    show_section_tabs: bool = True,
    bind_keyboard_events: bool = True,
    receipt_section_mode: str = "editor",
) -> ft.Control:
    """Build reusable settings panel control."""
    ensure_managed_templates_dir()
    settings_store = ReceiptSettingsStore(store_path)
    canvas_store = ReceiptCanvasStore()
    printer_svc = printer_service or WindowsPrinterService()
    audio_svc = audio_service or WindowsAudioService()
    settings = settings_store.load()

    try:
        printers = printer_svc.list_printers()
    except Exception as exc:
        printers = []
        page.snack_bar = ft.SnackBar(ft.Text(f"프린터 목록 조회 실패: {exc}"))
        page.snack_bar.open = True

    default_printer = None
    if printers:
        try:
            default_printer = printer_svc.get_default_printer()
        except Exception:
            default_printer = None

    selected_printer = _resolve_selected_printer(
        printers=printers,
        requested_printer=settings.printer_name,
        default_printer=default_printer,
    )

    receipt_layout_path = _normalize_json_layout_path(
        settings.template_path,
        DEFAULT_RECEIPT_LAYOUT_PATH,
    )
    product_layout_path = _normalize_json_layout_path(
        getattr(settings, "product_template_path", ""),
        DEFAULT_PRODUCT_RECEIPT_LAYOUT_PATH,
    )
    documents = {
        "receipt": _load_layout_document_or_default(
            canvas_store=canvas_store,
            path=receipt_layout_path,
            paper_width=settings.paper_width,
        ),
        "product": _load_layout_document_or_default(
            canvas_store=canvas_store,
            path=product_layout_path,
            paper_width=settings.paper_width,
            fallback_path=DEFAULT_PRODUCT_RECEIPT_LAYOUT_PATH,
        ),
    }
    editor_layout = {"value": "receipt"}

    state: dict[str, object] = {
        "docs": documents,
        "selected_id": None,
        "active_binding_target": None,
        "layout_paths": {
            "receipt": receipt_layout_path,
            "product": product_layout_path,
        },
        "canvas_focused": False,
        "drag_start_x": 0,
        "drag_start_y": 0,
        "drag_accum_dx": 0.0,
        "drag_accum_dy": 0.0,
        "drag_accum_dx__src": None,
        "drag_pointer_start_gx": None,
        "drag_pointer_start_gy": None,
        "drag_bottom_start_y": None,    # 드래그 시작 시 고정한 하단 여백 앵커 Y
        "resize_start_x": 0,
        "resize_start_y": 0,
        "resize_start_w": 0,
        "resize_start_h": 0,
        "resize_accum_dx": 0.0,
        "resize_accum_dy": 0.0,
        "resize_accum_dx__src": None,
        "resize_pointer_start_gx": None,
        "resize_pointer_start_gy": None,
        "resize_bottom_start_y": None,  # 리사이즈 시작 시 고정한 하단 여백 앵커 Y
        "undo_stack": [],
        "redo_stack": [],
        "last_nudge_time": 0.0,
        "last_pan_update_time": 0.0,
        "canvas_scroll_ctrl": None,
        "scroll_gutter_ctrl": None,
        "canvas_stack_ctrl": None,
        "canvas_meta_text_ctrl": None,
        "canvas_frame_ctrl": None,
        "canvas_frame_body_ctrl": None,
        "snap_guides": [],
        "inline_edit_id": None,
        "insertion_indicator": None,    # ft.Container - 삽입 위치 표시선
        "insertion_target_y": None,     # int | None - 드롭 시 삽입할 Y 좌표
        "last_element_tap_time": 0.0,  # 요소 탭 시각 (배경 클릭 전파 차단용)
    }

    printer_dropdown = ft.Dropdown(
        label="프린터",
        width=260,
        value=selected_printer or None,
        options=[ft.dropdown.Option(key=item, text=item) for item in printers],
    )
    paper_width_dropdown = ft.Dropdown(
        label="용지",
        width=120,
        value=settings.paper_width,
        options=[ft.dropdown.Option("58", "58mm"), ft.dropdown.Option("80", "80mm")],
    )
    dpi_dropdown = ft.Dropdown(
        label="DPI",
        width=120,
        value=str(settings.printer_dpi),
        options=[
            ft.dropdown.Option("180", "180 DPI"),
            ft.dropdown.Option("203", "203 DPI"),
            ft.dropdown.Option("300", "300 DPI"),
        ],
    )
    margin_top_field = ft.TextField(
        label="상단 여백(px)",
        width=120,
        value=str(settings.margin_top),
        keyboard_type=ft.KeyboardType.NUMBER,
    )
    margin_bottom_field = ft.TextField(
        label="하단 여백(px)",
        width=120,
        value=str(settings.margin_bottom),
        keyboard_type=ft.KeyboardType.NUMBER,
    )
    status_text = ft.Text(value="편집 준비됨", selectable=True, color="#444444")
    current_template_text = ft.Text(
        value=f"활성 영수증 템플릿: {receipt_layout_path}",
        color="#5F5F5F",
        size=12,
        selectable=True,
    )
    ticket_settings_status_text = ft.Text(value="", selectable=True, color="#444444")
    scan_sound_rule_name_field = ft.TextField(
        label="프로그램 표시 이름",
        value="",
        hint_text="예: 일본어 감사음",
        border_radius=10,
    )
    scan_sound_path_field = ft.TextField(
        label="음원 파일 주소",
        value="",
        read_only=True,
        expand=True,
    )
    btn_open_scan_sound_path = ft.IconButton(
        icon=ICONS.FOLDER_OPEN_ROUNDED,
        tooltip="파일 탐색기에서 위치 열기",
        icon_color="#2563EB",
        disabled=True,
    )

    btn_add_text = ft.ElevatedButton("텍스트 추가", icon=ICONS.TEXT_FIELDS_ROUNDED)
    btn_add_image = ft.ElevatedButton("이미지 추가", icon=ICONS.IMAGE_ROUNDED)
    btn_add_divider = ft.ElevatedButton("구분선 추가", icon=ICONS.HORIZONTAL_RULE_ROUNDED)
    btn_delete = ft.OutlinedButton("삭제", icon=ICONS.DELETE_OUTLINE_ROUNDED)
    btn_save = ft.ElevatedButton("저장", icon=ICONS.SAVE_ROUNDED)
    btn_save_as = ft.ElevatedButton("다른이름으로 저장", icon=ICONS.SAVE_AS_ROUNDED)
    btn_load = ft.ElevatedButton("불러오기", icon=ICONS.FOLDER_OPEN_ROUNDED)
    btn_test_print = ft.ElevatedButton("테스트 출력", icon=ICONS.PRINT_ROUNDED)
    btn_test_preview = ft.OutlinedButton("미리보기", icon=ICONS.VISIBILITY_ROUNDED)
    btn_pick_scan_sound = ft.ElevatedButton("음원 추가", icon=ICONS.AUDIO_FILE_ROUNDED)
    btn_preview_scan_sound = ft.OutlinedButton("미리 듣기", icon=ICONS.PLAY_ARROW_ROUNDED)
    btn_remove_scan_sound_rule = ft.OutlinedButton("선택 삭제", icon=ICONS.DELETE_OUTLINE_ROUNDED)
    btn_clear_scan_sound = ft.OutlinedButton("전체 초기화", icon=ICONS.DELETE_OUTLINE_ROUNDED)

    canvas_host = ft.Container(expand=True)
    property_panel = ft.Container(bgcolor="#FFFFFF", border_radius=8, padding=12)

    image_picker = ft.FilePicker()
    save_as_picker = ft.FilePicker()
    load_picker = ft.FilePicker()
    scan_sound_picker = ft.FilePicker()

    _attach_page_services(
        page,
        image_picker,
        save_as_picker,
        load_picker,
        scan_sound_picker,
    )

    scan_sound_rules_state: dict[str, object] = {
        "rules": _load_scan_success_sound_rules(settings),
        "selected_index": 0 if _load_scan_success_sound_rules(settings) else None,
        "syncing": False,
    }
    scan_sound_drag_group = "receipt-settings-scan-sound-rules"
    scan_sound_rule_summary_text = ft.Text(size=12, color="#64748B", selectable=True)
    scan_sound_rule_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
    scan_sound_rule_weight_field = ft.TextField(
        label="확률(%)",
        width=160,
        value="100",
        hint_text="예: 25 / 33.33",
    )
    scan_sound_rule_trigger_type_dropdown = ft.Dropdown(
        label="조건 타입",
        width=170,
        value="always",
        options=[ft.dropdown.Option(key=key, text=label) for key, label in SCAN_SOUND_TRIGGER_OPTIONS],
    )
    scan_sound_rule_trigger_value_field = ft.TextField(
        label="조건값",
        hint_text=_scan_success_trigger_value_hint("always"),
        expand=True,
    )
    scan_sound_rule_enabled_switch = ft.Switch(label="활성", value=True, **_switch_theme_kwargs())
    scan_sound_rules_management_panel = _build_scan_success_sound_management_panel(
        summary_text=scan_sound_rule_summary_text,
        sound_rule_list=scan_sound_rule_list,
        sound_rule_name_field=scan_sound_rule_name_field,
        sound_path_field=scan_sound_path_field,
        btn_open_sound_path=btn_open_scan_sound_path,
        sound_rule_weight_field=scan_sound_rule_weight_field,
        sound_rule_trigger_type_dropdown=scan_sound_rule_trigger_type_dropdown,
        sound_rule_trigger_value_field=scan_sound_rule_trigger_value_field,
        sound_rule_enabled_switch=scan_sound_rule_enabled_switch,
        btn_pick_sound=btn_pick_scan_sound,
        btn_preview_sound=btn_preview_scan_sound,
        btn_remove_sound_rule=btn_remove_scan_sound_rule,
        btn_clear_sound_rules=btn_clear_scan_sound,
    )

    def _editor_layout_key() -> str:
        return editor_layout["value"]

    def _editor_layout_label(layout_key: str | None = None) -> str:
        return "상품 영수증" if (layout_key or _editor_layout_key()) == "product" else "영수증"

    def _doc() -> ReceiptCanvasDocument:
        return _get_editor_layout_doc(
            state=state,
            active_layout=_editor_layout_key(),
        )

    def _set_doc(doc: ReceiptCanvasDocument) -> None:
        _set_editor_layout_doc(
            state=state,
            active_layout=_editor_layout_key(),
            doc=doc,
        )

    _HISTORY_LIMIT = 50

    def _begin_undo_unit() -> None:
        """사용자 작업 시작 직전 현재 doc를 undo 스택에 푸시한다."""
        snapshot = copy.deepcopy(_doc())
        undo_stack = state["undo_stack"]  # type: ignore[assignment]
        if not isinstance(undo_stack, list):
            undo_stack = []
            state["undo_stack"] = undo_stack
        undo_stack.append(snapshot)
        if len(undo_stack) > _HISTORY_LIMIT:
            del undo_stack[0:len(undo_stack) - _HISTORY_LIMIT]
        redo_stack = state["redo_stack"]  # type: ignore[assignment]
        if isinstance(redo_stack, list):
            redo_stack.clear()

    def _undo() -> bool:
        undo_stack = state["undo_stack"]  # type: ignore[assignment]
        if not isinstance(undo_stack, list) or not undo_stack:
            return False
        redo_stack = state["redo_stack"]  # type: ignore[assignment]
        if not isinstance(redo_stack, list):
            redo_stack = []
            state["redo_stack"] = redo_stack
        redo_stack.append(copy.deepcopy(_doc()))
        if len(redo_stack) > _HISTORY_LIMIT:
            del redo_stack[0:len(redo_stack) - _HISTORY_LIMIT]
        prev_doc = undo_stack.pop()
        _set_doc(prev_doc)
        # 복원된 doc에 더 이상 존재하지 않는 요소가 선택되어 있을 수 있어 해제
        if _selected_id() and not any(el.id == _selected_id() for el in prev_doc.elements):
            _clear_canvas_selection(
                set_selected_id=_set_selected_id,
                set_active_binding_target=_set_active_binding_target,
            )
        return True

    def _redo() -> bool:
        redo_stack = state["redo_stack"]  # type: ignore[assignment]
        if not isinstance(redo_stack, list) or not redo_stack:
            return False
        undo_stack = state["undo_stack"]  # type: ignore[assignment]
        if not isinstance(undo_stack, list):
            undo_stack = []
            state["undo_stack"] = undo_stack
        undo_stack.append(copy.deepcopy(_doc()))
        if len(undo_stack) > _HISTORY_LIMIT:
            del undo_stack[0:len(undo_stack) - _HISTORY_LIMIT]
        next_doc = redo_stack.pop()
        _set_doc(next_doc)
        if _selected_id() and not any(el.id == _selected_id() for el in next_doc.elements):
            _clear_canvas_selection(
                set_selected_id=_set_selected_id,
                set_active_binding_target=_set_active_binding_target,
            )
        return True

    def _selected_id() -> str | None:
        return _get_editor_selected_id(state=state)

    def _set_selected_id(value: str | None) -> None:
        _set_editor_selected_id(
            state=state,
            value=value,
        )

    def _active_binding_target() -> str | None:
        return _get_editor_active_binding_target(state=state)

    def _set_active_binding_target(value: str | None) -> None:
        _set_editor_active_binding_target(
            state=state,
            value=value,
        )

    def _layout_path() -> str:
        return _get_editor_layout_path(
            state=state,
            active_layout=_editor_layout_key(),
        )

    def _set_canvas_focus(focused: bool) -> None:
        state["canvas_focused"] = focused

    def _set_layout_path(path: str) -> None:
        _set_editor_layout_path(
            state=state,
            active_layout=_editor_layout_key(),
            path=path,
            current_template_text=current_template_text,
            editor_layout_label_text=_editor_layout_label(),
        )

    def _show_status(message: str) -> None:
        status_text.value = message
        page.update()

    def _margin_top_px() -> int:
        """현재 상단 여백 (실제 px)"""
        return max(0, _coerce_int(margin_top_field.value, 0))

    def _margin_bottom_px() -> int:
        """현재 하단 여백 (실제 px)"""
        return max(0, _coerce_int(margin_bottom_field.value, 0))

    def _real_canvas_width() -> int:
        return max(1, int(_doc().meta.canvas_width_px))

    def _preview_canvas_width() -> int:
        return _preview_width_for_paper(str(paper_width_dropdown.value or "80"))

    def _preview_scale() -> float:
        return _preview_canvas_width() / _real_canvas_width()

    def _calc_real_canvas_height(*, exclude_ids: set[str] | None = None) -> int:
        """요소 배치 기준 콘텐츠 영역 높이 (여백 포함)"""
        margins = _margin_top_px() + _margin_bottom_px()
        base_height = max(600, int(_doc().meta.canvas_height_px) + margins)
        excluded = exclude_ids or set()
        elements = [el for el in _doc().elements if el.id not in excluded]
        if not elements:
            return base_height
        visible = [el for el in elements if el.visible]
        if not visible:
            return base_height
        max_bottom = max(el.y + el.h for el in visible)
        return max(base_height, max_bottom + margins)

    def _preview_canvas_height() -> int:
        """캔버스 프리뷰 높이: 콘텐츠 높이 (여백 이미 포함)"""
        content_h = _calc_real_canvas_height()
        return max(280, int(content_h * _preview_scale()))

    def _ensure_canvas_scaffold() -> tuple[ft.Stack, ft.GestureDetector, ft.Container, ft.Column, ft.Text]:
        existing_stack = state.get("canvas_stack_ctrl")
        existing_frame = state.get("canvas_frame_ctrl")
        existing_body = state.get("canvas_frame_body_ctrl")
        existing_scroll = state.get("canvas_scroll_ctrl")
        existing_meta = state.get("canvas_meta_text_ctrl")
        if (
            isinstance(existing_stack, ft.Stack)
            and isinstance(existing_frame, ft.GestureDetector)
            and isinstance(existing_body, ft.Container)
            and isinstance(existing_scroll, ft.Column)
            and isinstance(existing_meta, ft.Text)
        ):
            return existing_stack, existing_frame, existing_body, existing_scroll, existing_meta

        preview_w = _preview_canvas_width()
        preview_h = _preview_canvas_height()

        def _on_canvas_bg_click(_: ft.ControlEvent) -> None:
            # 요소 탭 직후 전파된 배경 클릭 무시 (50ms 가드)
            current_time = time.monotonic()
            if _should_ignore_canvas_background_tap(
                last_element_tap_time=float(state["last_element_tap_time"]),
                current_time=current_time,
            ):
                return
            _clear_canvas_selection(
                set_selected_id=_set_selected_id,
                set_active_binding_target=_set_active_binding_target,
            )
            _refresh_all()

        canvas_stack = ft.Stack(
            controls=[],
            width=preview_w,
            height=preview_h,
            clip_behavior=ft.ClipBehavior.NONE,
        )
        canvas_frame_body = ft.Container(
            width=preview_w,
            height=preview_h,
            bgcolor="#FFFFFF",
            border=ft.border.all(1, "#CFCFCF"),
            border_radius=10,
            clip_behavior=ft.ClipBehavior.NONE,
            content=canvas_stack,
            padding=0,
        )
        canvas_frame = ft.GestureDetector(on_tap=_on_canvas_bg_click, content=canvas_frame_body)
        max_viewport_h = 500
        viewport_h = min(preview_h, max_viewport_h)
        needs_scroll = preview_h > max_viewport_h
        # 스크롤바 전용 여백 - 캔버스 오른쪽에 배치하여 핸들과 겹침 방지
        scrollbar_gutter_w = 14
        scroll_gutter = ft.Container(
            width=scrollbar_gutter_w if needs_scroll else 0,
            height=preview_h,
        )
        canvas_with_gutter = ft.Row(
            controls=[canvas_frame, scroll_gutter],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.START,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        scrollable_canvas = ft.Column(
            controls=[canvas_with_gutter],
            height=viewport_h,
            scroll=ft.ScrollMode.AUTO if needs_scroll else None,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        canvas_meta_text = ft.Text(color="#666666", size=12)

        canvas_host.content = ft.Container(
            alignment=ALIGN_TOP_CENTER,
            padding=ft.padding.only(top=8),
            content=ft.Column(
                controls=[canvas_meta_text, scrollable_canvas],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
        )

        state["canvas_stack"] = canvas_stack
        state["canvas_stack_ctrl"] = canvas_stack
        state["canvas_frame_ctrl"] = canvas_frame
        state["canvas_frame_body_ctrl"] = canvas_frame_body
        state["canvas_scroll_ctrl"] = scrollable_canvas
        state["scroll_gutter_ctrl"] = scroll_gutter
        state["canvas_meta_text_ctrl"] = canvas_meta_text
        return canvas_stack, canvas_frame, canvas_frame_body, scrollable_canvas, canvas_meta_text

    def _bottom_margin_start_y(*, exclude_ids: set[str] | None = None) -> int | None:
        """하단 여백 시작 Y 좌표(실제 px). 콘텐츠 영역 끝 = 하단 여백 윗 경계."""
        if _margin_bottom_px() <= 0:
            return None
        # 전체 높이에서 하단 여백을 빼면 하단 여백 시작점
        return _calc_real_canvas_height(exclude_ids=exclude_ids) - _margin_bottom_px()

    def _fixed_bottom_anchor_y(*, moving_ids: set[str]) -> int | None:
        """드래그/리사이즈 세션 동안 사용할 고정 하단 여백 앵커."""
        return _bottom_margin_start_y()

    def _auto_scroll_on_drag(preview_y: float, preview_h: float) -> None:
        """드래그 중 자동 스크롤 (비활성: Flet Column에 scroll_offset 읽기 API 없음)."""
        # NOTE: ft.Column은 scroll_to()만 지원하고 현재 offset 읽기가 불가능하여
        # 뷰포트 위치를 알 수 없음 → 오작동(항상 위로 스크롤) 방지를 위해 비활성화
        return

    def _clamp_y_to_reserved_margins(
        y: int,
        h: int,
        *,
        exclude_ids: set[str] | None = None,
        bottom_start_y: int | None = None,
    ) -> int:
        """상/하단 예약 여백을 침범하지 않도록 Y 좌표를 제한."""
        mt = _margin_top_px()
        clamped = max(mt, int(y))
        bottom_start = (
            int(bottom_start_y)
            if bottom_start_y is not None
            else _bottom_margin_start_y(exclude_ids=exclude_ids)
        )
        if bottom_start is None:
            return clamped
        max_y = max(mt, int(bottom_start) - max(1, int(h)))
        return min(clamped, max_y)

    def _find_selected_element() -> ReceiptCanvasElement | None:
        selected = _selected_id()
        if not selected:
            return None
        for element in _doc().elements:
            if element.id == selected:
                return element
        return None

    def _upsert_element(updated: ReceiptCanvasElement) -> None:
        _set_doc(replace(_doc(), elements=update_element_in_list(_doc().elements, updated)))

    def _resolve_overlaps(exclude_id: str | None = None) -> None:
        """요소 간 겹침 해소: Y 순서대로 아래 요소를 밀어냄 (gap=0px, snap과 일관성 유지)"""
        elements = list(_doc().elements)
        if len(elements) < 2:
            return
        # Y 기준 정렬된 인덱스
        order = sorted(range(len(elements)), key=lambda i: (elements[i].y, elements[i].x))
        gap = 0
        changed = False
        for pos in range(len(order)):
            i = order[pos]
            cur = elements[i]
            if not cur.visible:
                continue
            # 위쪽 요소들과 X축 겹침 확인 → Y축 밀어내기
            for prev_pos in range(pos):
                j = order[prev_pos]
                prev = elements[j]
                if not prev.visible:
                    continue
                # X축 범위 겹침 여부
                if prev.x >= cur.x + cur.w or cur.x >= prev.x + prev.w:
                    continue
                min_y = prev.y + prev.h + gap
                if cur.y < min_y:
                    new_y = _clamp_y_to_reserved_margins(min_y, cur.h, exclude_ids={cur.id})
                    elements[i] = replace(cur, y=new_y)
                    cur = elements[i]
                    changed = True
        if changed:
            _set_doc(replace(_doc(), elements=elements))

    def _enforce_margin_boundaries() -> None:
        """여백 경계를 침범하는 모든 요소를 안전 영역으로 밀어냄."""
        mt = _margin_top_px()
        bottom_start = _bottom_margin_start_y()
        elements = list(_doc().elements)
        changed = False
        for i, el in enumerate(elements):
            new_y = el.y
            # 상단 여백 위반 → 아래로 밀기
            if new_y < mt:
                new_y = mt
            # 하단 여백 위반 → 위로 밀기
            if bottom_start is not None and new_y + el.h > bottom_start:
                new_y = max(mt, bottom_start - el.h)
            if new_y != el.y:
                elements[i] = replace(el, y=new_y)
                changed = True
        if changed:
            _set_doc(replace(_doc(), elements=elements))
        _resolve_overlaps_sticky()

    def _resolve_overlaps_sticky() -> None:
        """리사이즈 후 스티키 정렬: 위로 당김 + 아래로 밀기 (2-패스, gap=0 snap 일관성)"""
        elements = list(_doc().elements)
        if len(elements) < 2:
            return
        gap = 0
        changed = False
        order = sorted(range(len(elements)), key=lambda i: (elements[i].y, elements[i].x))

        # 패스 1: Pull-up — 각 요소를 X겹침 있는 위쪽 요소 바로 아래로 당김
        for pos in range(len(order)):
            i = order[pos]
            cur = elements[i]
            if not cur.visible:
                continue
            # X겹침이 있는 위쪽 요소 중 가장 가까운 bottom 찾기
            best_bottom: int | None = None
            for prev_pos in range(pos):
                j = order[prev_pos]
                prev = elements[j]
                if not prev.visible:
                    continue
                if prev.x >= cur.x + cur.w or cur.x >= prev.x + prev.w:
                    continue
                candidate = prev.y + prev.h
                if best_bottom is None or candidate > best_bottom:
                    best_bottom = candidate
            if best_bottom is not None:
                target_y = best_bottom + gap
                if cur.y > target_y:
                    new_y = _clamp_y_to_reserved_margins(target_y, cur.h, exclude_ids={cur.id})
                    elements[i] = replace(cur, y=new_y)
                    changed = True
            else:
                # 위에 X겹침 요소가 없으면 상단 여백 위치로 당김
                mt = _margin_top_px()
                if cur.y > mt:
                    new_y = _clamp_y_to_reserved_margins(mt, cur.h, exclude_ids={cur.id})
                    elements[i] = replace(cur, y=new_y)
                    changed = True

        # 패스 2: Push-down — 겹침 밀어내기 (안전망)
        order = sorted(range(len(elements)), key=lambda i: (elements[i].y, elements[i].x))
        for pos in range(len(order)):
            i = order[pos]
            cur = elements[i]
            if not cur.visible:
                continue
            for prev_pos in range(pos):
                j = order[prev_pos]
                prev = elements[j]
                if not prev.visible:
                    continue
                if prev.x >= cur.x + cur.w or cur.x >= prev.x + prev.w:
                    continue
                min_y = prev.y + prev.h + gap
                if cur.y < min_y:
                    new_y = _clamp_y_to_reserved_margins(min_y, cur.h, exclude_ids={cur.id})
                    elements[i] = replace(cur, y=new_y)
                    cur = elements[i]
                    changed = True

        if changed:
            _set_doc(replace(_doc(), elements=elements))

    def _scroll_canvas_to_element(element_id: str) -> None:
        """추가/이동된 요소가 보이도록 캔버스 뷰포트를 스크롤한다."""
        target = next((el for el in _doc().elements if el.id == element_id), None)
        if target is None:
            return
        scroll_ctrl = state.get("canvas_scroll_ctrl")
        if not isinstance(scroll_ctrl, ft.Column):
            return
        real_w = _real_canvas_width()
        preview_w = _preview_canvas_width()
        preview_y = real_to_preview(target.y, real_width=real_w, preview_width=preview_w)
        try:
            scroll_ctrl.scroll_to(offset=max(0, preview_y - 40), duration=200)
        except Exception:
            logger.debug("canvas scroll_to 실패", exc_info=True)

    def _add_element(element: ReceiptCanvasElement) -> None:
        _begin_undo_unit()
        _set_doc(replace(_doc(), elements=[*_doc().elements, element]))
        _resolve_overlaps()
        _set_selected_id(element.id)
        _scroll_canvas_to_element(element.id)

    def _remove_selected_element() -> None:
        selected = _require_selected_id(
            selected_id=_selected_id(),
            show_status=_show_status,
            missing_message="삭제할 요소를 먼저 선택하세요.",
        )
        if not selected:
            return
        _begin_undo_unit()
        elements = _remove_selected_element_from_elements(
            elements=_doc().elements,
            selected_id=selected,
        )
        _set_doc(replace(_doc(), elements=elements))
        _clear_canvas_selection(
            set_selected_id=_set_selected_id,
            set_active_binding_target=_set_active_binding_target,
        )
        _refresh_all()
        _show_status("선택 요소를 삭제했습니다. (Ctrl+Z로 되돌리기)")

    def _apply_align_to_selected(align: str) -> None:
        element = _require_selected_element(
            selected_element=_find_selected_element(),
            show_status=_show_status,
            missing_message="정렬할 요소를 먼저 선택하세요.",
        )
        if not element:
            return
        _begin_undo_unit()
        _apply_selected_alignment_action(
            element=element,
            align=align,
            upsert_element=_upsert_element,
            refresh_all=_refresh_all,
        )

    def _new_default_element(kind: str, *, asset_path: str = "") -> ReceiptCanvasElement:
        doc = _doc()
        mt = _margin_top_px()
        visible = [el for el in doc.elements if el.visible]
        if visible:
            # 기존 요소의 가장 아래 지점 + 간격 16px
            max_bottom = max(el.y + el.h for el in visible)
            next_y = max(mt, max_bottom + 16)
        else:
            next_y = max(mt, 24)
        if kind == "text":
            text_h = 64
            return ReceiptCanvasElement(
                id=make_element_id("txt"),
                type="text",
                x=20,
                y=_clamp_y_to_reserved_margins(next_y, text_h),
                w=260,
                h=text_h,
                align="left",
                text_template="새 텍스트",
                font_size=22,
                bold=False,
            )
        if kind == "divider":
            canvas_w = int(_doc().meta.canvas_width_px)
            divider_h = 12
            return ReceiptCanvasElement(
                id=make_element_id("div"),
                type="divider",
                x=0,
                y=_clamp_y_to_reserved_margins(next_y, divider_h),
                w=canvas_w,
                h=divider_h,
                align="left",
                line_style="solid",
            )
        if kind == "image":
            image_h = 120
            return ReceiptCanvasElement(
                id=make_element_id("img"),
                type="image",
                x=20,
                y=_clamp_y_to_reserved_margins(next_y, image_h),
                w=220,
                h=image_h,
                align="center",
                asset_path=asset_path,
                preserve_ratio=True,
            )
        qr_h = 140
        return ReceiptCanvasElement(
            id=make_element_id("qr"),
            type="qr",
            x=20,
            y=_clamp_y_to_reserved_margins(next_y, qr_h),
            w=140,
            h=qr_h,
            align="center",
            data_template="",
            box_size=4,
        )

    def _update_common_dimensions(
        element: ReceiptCanvasElement,
        *,
        x: int | None = None,
        y: int | None = None,
        w: int | None = None,
        h: int | None = None,
        align: str | None = None,
        visible: bool | None = None,
        bottom_start_y: int | None = None,
    ) -> ReceiptCanvasElement:
        new_x = element.x if x is None else x
        new_y = element.y if y is None else y
        new_w = element.w if w is None else max(10, w)
        new_h = element.h if h is None else max(10, h)
        # 캔버스 경계 내로 크기 제한 (폭만 제한, 높이는 자동 확장)
        c_w = int(_doc().meta.canvas_width_px)
        new_w = min(new_w, c_w - max(0, new_x))
        bottom_start = (
            int(bottom_start_y)
            if bottom_start_y is not None
            else _bottom_margin_start_y(exclude_ids={element.id})
        )
        if bottom_start is not None:
            max_h = max(10, int(bottom_start) - _margin_top_px())
            new_h = min(new_h, max_h)
        new_x = max(0, min(int(new_x), max(0, c_w - max(1, new_w))))
        new_y = _clamp_y_to_reserved_margins(
            new_y,
            new_h,
            exclude_ids={element.id},
            bottom_start_y=bottom_start,
        )
        clamped_x = new_x
        clamped_y = new_y
        return replace(
            element,
            x=clamped_x,
            y=clamped_y,
            w=new_w,
            h=new_h,
            align=element.align if align is None else align,
            visible=element.visible if visible is None else visible,
        )

    def _insert_binding(field_key: str) -> None:
        insert_text = _build_binding_insert_text(
            field_key,
            field_bindings=FIELD_BINDINGS,
        )
        selected = _find_selected_element()

        # 선택된 텍스트/QR 요소가 있으면 해당 요소에 필드 추가
        if selected and selected.type in ("text", "qr"):
            updated = _apply_binding_insert_to_selected_element(
                selected_element=selected,
                insert_text=insert_text,
                active_binding_target=_active_binding_target(),
                resolve_binding_target_for_element_type=_resolve_binding_target_for_element_type,
            )
            _upsert_element(updated)
            _refresh_all()
            return

        # 선택된 요소가 없으면 새 텍스트 요소 생성
        new_el = _build_new_binding_text_element(
            new_text_element=_new_default_element("text"),
            insert_text=insert_text,
        )
        _add_element(new_el)
        _set_active_binding_target("text_template")
        _refresh_all()
    def _current_dpi() -> int:
        """현재 선택된 DPI 값"""
        try:
            val = int(dpi_dropdown.value or "203")
            return val if val in {180, 203, 300} else 203
        except (ValueError, TypeError):
            return 203

    def _set_paper_width(new_width: str, push_update: bool = True) -> None:
        if new_width not in {"58", "80"}:
            return

        doc = _doc()
        old_real_width = max(
            1,
            int(doc.meta.canvas_width_px),
            max((int(element.x) + max(1, int(element.w)) for element in doc.elements), default=0),
        )
        new_real_width = paper_width_to_px(new_width)  # 항상 203 DPI 기준
        if old_real_width != new_real_width:
            ratio = new_real_width / old_real_width
            resized: list[ReceiptCanvasElement] = []
            for element in doc.elements:
                new_x = int(round(element.x * ratio))
                new_w = max(10, int(round(element.w * ratio)))
                clamped_x, clamped_y = clamp_element_position(
                    x=new_x,
                    y=element.y,
                    w=new_w,
                    h=element.h,
                    canvas_w=new_real_width,
                    canvas_h=99999,
                )
                resized.append(replace(element, x=clamped_x, y=clamped_y, w=new_w))
            _set_doc(
                replace(
                    doc,
                    meta=replace(
                        doc.meta,
                        paper_width="58" if new_width == "58" else "80",
                        canvas_width_px=new_real_width,
                    ),
                    elements=resized,
                )
            )
        else:
            _set_doc(replace(doc, meta=replace(doc.meta, paper_width="58" if new_width == "58" else "80")))
        _refresh_all(push_update=push_update)

    def _selected_ticket_product_names() -> list[str]:
        return [str(cb.label) for cb in ticket_checkboxes if cb.value]

    def _get_scan_sound_rules() -> list[ScanSuccessSoundRule]:
        return list(scan_sound_rules_state["rules"])  # type: ignore[arg-type, return-value]

    def _set_scan_sound_rules(rules: list[ScanSuccessSoundRule]) -> None:
        scan_sound_rules_state["rules"] = list(rules)

    def _selected_scan_sound_rule_index() -> int | None:
        raw = scan_sound_rules_state.get("selected_index")
        return raw if isinstance(raw, int) else None

    def _set_selected_scan_sound_rule_index(index: int | None) -> None:
        scan_sound_rules_state["selected_index"] = index

    def _selected_scan_sound_rule() -> ScanSuccessSoundRule | None:
        rules = _get_scan_sound_rules()
        index = _selected_scan_sound_rule_index()
        if index is None or index < 0 or index >= len(rules):
            return None
        return rules[index]

    def _replace_selected_scan_sound_rule(rule: ScanSuccessSoundRule) -> None:
        rules = _get_scan_sound_rules()
        index = _selected_scan_sound_rule_index()
        if index is None or index < 0 or index >= len(rules):
            return
        rules[index] = rule
        _set_scan_sound_rules(rules)

    def _set_scan_sound_rule_enabled(rule_index: int, enabled: bool) -> None:
        rules = _get_scan_sound_rules()
        if rule_index < 0 or rule_index >= len(rules):
            return
        rules[rule_index] = replace(rules[rule_index], enabled=bool(enabled))
        rules = _rebalance_scan_success_rules(rules, mode="equal")
        _set_scan_sound_rules(rules)
        saved = _save_settings_only(show_message=False)
        ticket_settings_status_text.value = "음원 활성화 상태 저장 완료" if saved else "음원 활성화 상태 저장 실패"
        _refresh_scan_sound_rule_controls()

    def _begin_edit_scan_sound_rule_name(rule_index: int) -> None:
        _set_selected_scan_sound_rule_index(rule_index)
        _refresh_scan_sound_rule_controls(push_update=False)
        focus_method = getattr(scan_sound_rule_name_field, "focus", None)
        if callable(focus_method):
            try:
                focus_method()
            except Exception:
                logger.debug("프로그램 표시 이름 필드 focus 실패", exc_info=True)
        page.update()

    def _reorder_scan_sound_rule(source_index: int, target_index: int) -> None:
        rules, selected_index = _reorder_scan_success_rules(
            _get_scan_sound_rules(),
            from_index=source_index,
            to_index=target_index,
            selected_index=_selected_scan_sound_rule_index(),
        )
        if source_index == target_index:
            return
        _set_scan_sound_rules(rules)
        _set_selected_scan_sound_rule_index(selected_index)
        saved = _save_settings_only(show_message=False)
        ticket_settings_status_text.value = "음원 순서 저장 완료" if saved else "음원 순서 저장 실패"
        _refresh_scan_sound_rule_controls()

    def _refresh_scan_sound_rule_controls(
        *,
        push_update: bool = True,
        preserve_weight_input: bool = False,
    ) -> None:
        rules = _get_scan_sound_rules()
        selected_index = _selected_scan_sound_rule_index()
        if selected_index is not None and selected_index >= len(rules):
            selected_index = len(rules) - 1 if rules else None
            _set_selected_scan_sound_rule_index(selected_index)

        selected_rule = _selected_scan_sound_rule()
        scan_sound_rules_state["syncing"] = True
        try:
            if not rules:
                scan_sound_rule_summary_text.value = _scan_success_rule_pool_summary(rules)
                scan_sound_rule_list.controls = [
                    ft.Text("음원을 추가하면 기본 랜덤 음원이 만들어집니다.", size=12, color="#999999")
                ]
                scan_sound_rule_name_field.value = ""
                scan_sound_path_field.value = ""
                scan_sound_rule_weight_field.value = format_scan_success_weight(100)
                scan_sound_rule_trigger_type_dropdown.value = "always"
                scan_sound_rule_trigger_value_field.value = ""
                scan_sound_rule_trigger_value_field.hint_text = _scan_success_trigger_value_hint("always")
                _set_scan_sound_editor_visibility(
                    trigger_type="always",
                    sound_rule_weight_field=scan_sound_rule_weight_field,
                    sound_rule_trigger_value_field=scan_sound_rule_trigger_value_field,
                )
                scan_sound_rule_enabled_switch.value = True
                scan_sound_rule_name_field.disabled = True
                scan_sound_rule_weight_field.disabled = True
                scan_sound_rule_trigger_type_dropdown.disabled = True
                scan_sound_rule_trigger_value_field.disabled = True
                btn_open_scan_sound_path.disabled = True
                btn_preview_scan_sound.disabled = True
                btn_remove_scan_sound_rule.disabled = True
                btn_clear_scan_sound.disabled = True
            else:
                scan_sound_rule_summary_text.value = _scan_success_rule_pool_summary(rules)
                scan_sound_rule_list.controls = [
                    _build_scan_success_sound_rule_card(
                        page=page,
                        rule=rule,
                        index=index,
                        selected_index=selected_index,
                        drag_group=scan_sound_drag_group,
                        on_select=lambda rule_index: (
                            _set_selected_scan_sound_rule_index(rule_index),
                            _refresh_scan_sound_rule_controls(),
                        ),
                        on_edit_name=_begin_edit_scan_sound_rule_name,
                        on_toggle_enabled=_set_scan_sound_rule_enabled,
                        on_reorder=_reorder_scan_sound_rule,
                    )
                    for index, rule in enumerate(rules)
                ]

                scan_sound_rule_name_field.value = _scan_success_rule_display_name(selected_rule) if selected_rule else ""
                scan_sound_path_field.value = (selected_rule.sound_path or "").strip() if selected_rule else ""
                btn_open_scan_sound_path.disabled = not bool(scan_sound_path_field.value.strip())
                if not preserve_weight_input:
                    scan_sound_rule_weight_field.value = (
                        format_scan_success_weight(selected_rule.weight)
                        if selected_rule
                        else format_scan_success_weight(100)
                    )
                scan_sound_rule_trigger_type_dropdown.value = (
                    selected_rule.trigger_type if selected_rule else "always"
                )
                scan_sound_rule_trigger_value_field.value = selected_rule.trigger_value if selected_rule else ""
                scan_sound_rule_trigger_value_field.hint_text = _scan_success_trigger_value_hint(
                    selected_rule.trigger_type if selected_rule else "always"
                )
                _set_scan_sound_editor_visibility(
                    trigger_type=selected_rule.trigger_type if selected_rule else "always",
                    sound_rule_weight_field=scan_sound_rule_weight_field,
                    sound_rule_trigger_value_field=scan_sound_rule_trigger_value_field,
                )
                scan_sound_rule_enabled_switch.value = bool(selected_rule.enabled) if selected_rule else True
                scan_sound_rule_name_field.disabled = False
                scan_sound_rule_weight_field.disabled = False
                scan_sound_rule_trigger_type_dropdown.disabled = False
                scan_sound_rule_trigger_value_field.disabled = False
                btn_preview_scan_sound.disabled = False
                btn_remove_scan_sound_rule.disabled = False
                btn_clear_scan_sound.disabled = False
        finally:
            scan_sound_rules_state["syncing"] = False

        if push_update:
            page.update()

    def _save_selected_scan_sound_rule(
        message: str = "스캔 사운드 규칙 저장 완료",
        *,
        rebalance_mode: str = "normalize",
        preserve_weight_input: bool = False,
    ) -> None:
        if scan_sound_rules_state.get("syncing"):
            return
        selected_rule = _selected_scan_sound_rule()
        selected_index = _selected_scan_sound_rule_index()
        if selected_rule is None:
            return

        display_name = (scan_sound_rule_name_field.value or "").strip() or _scan_success_rule_display_name(selected_rule)
        current_trigger_type = str(scan_sound_rule_trigger_type_dropdown.value or "always")
        current_enabled = bool(selected_rule.enabled)
        updated_rule = replace(
            selected_rule,
            name=display_name,
            enabled=current_enabled,
            weight=coerce_scan_success_weight(scan_sound_rule_weight_field.value or "0", default=0.0),
            trigger_type=current_trigger_type,  # type: ignore[arg-type]
            trigger_value=(scan_sound_rule_trigger_value_field.value or "").strip(),
        )
        _replace_selected_scan_sound_rule(updated_rule)
        rules = _get_scan_sound_rules()
        if rebalance_mode == "edit" and selected_index is not None:
            rules = _rebalance_scan_success_rules(
                rules,
                mode="edit",
                edited_index=selected_index,
                edited_weight=updated_rule.weight,
            )
        else:
            rules = _rebalance_scan_success_rules(rules, mode=rebalance_mode)
        _set_scan_sound_rules(rules)
        saved = _save_settings_only(show_message=False)
        ticket_settings_status_text.value = message if saved else "스캔 사운드 규칙 저장 실패"
        _refresh_scan_sound_rule_controls(preserve_weight_input=preserve_weight_input)

    def _build_settings_object(
        target_layout_path: str | None = None,
        *,
        target_layout_key: str | None = None,
    ) -> ReceiptSettings:
        layout_paths = dict(state["layout_paths"])  # type: ignore[arg-type]
        if target_layout_path:
            layout_paths[target_layout_key or _editor_layout_key()] = target_layout_path

        resolved_receipt_layout_path = _normalize_json_layout_path(
            str(layout_paths.get("receipt", DEFAULT_RECEIPT_LAYOUT_PATH)),
            DEFAULT_RECEIPT_LAYOUT_PATH,
        )
        resolved_product_layout_path = _normalize_json_layout_path(
            str(layout_paths.get("product", DEFAULT_PRODUCT_RECEIPT_LAYOUT_PATH)),
            DEFAULT_PRODUCT_RECEIPT_LAYOUT_PATH,
        )

        return ReceiptSettings(
            printer_name=(printer_dropdown.value or "").strip(),
            paper_width="58" if str(paper_width_dropdown.value) == "58" else "80",  # type: ignore[arg-type]
            show_qr=settings.show_qr,
            show_logo=settings.show_logo,
            template_path=resolved_receipt_layout_path,
            product_template_path=resolved_product_layout_path,
            logo_path=settings.logo_path,
            event_title=settings.event_title,
            qr_payload_template=settings.qr_payload_template,
            margin_top=max(0, _coerce_int(margin_top_field.value, 0)),
            margin_bottom=max(0, _coerce_int(margin_bottom_field.value, 0)),
            printer_dpi=_current_dpi(),
            print_product_receipt=bool(getattr(settings_store.load(), "print_product_receipt", False)),
            ticket_product_names=_selected_ticket_product_names(),
            qr_scan_success_sound_path=_primary_scan_success_sound_path(
                _rebalance_scan_success_rules(_get_scan_sound_rules(), mode="normalize")
            ),
            qr_scan_success_sound_rules=_rebalance_scan_success_rules(_get_scan_sound_rules(), mode="normalize"),
        )

    def _save_settings_only(*, show_message: bool = True) -> ReceiptSettings | None:
        try:
            settings_obj = _build_settings_object()
            settings_store.save(settings_obj)
            if show_message:
                ticket_settings_status_text.value = "티켓 확인 설정 저장 완료"
                page.update()
            return settings_obj
        except Exception as exc:
            if show_message:
                ticket_settings_status_text.value = f"설정 저장 실패: {exc}"
                page.update()
            return None

    def _save_current_layout(*, show_message: bool = True) -> ReceiptSettings | None:
        try:
            target_layout_path = _layout_path()
            if not target_layout_path.lower().endswith(".json"):
                target_layout_path = _resolve_editor_default_layout_path(_editor_layout_key())
            _set_layout_path(target_layout_path)

            paper_width = "58" if str(paper_width_dropdown.value) == "58" else "80"
            current_doc = _build_layout_document_for_save(
                _doc(),
                paper_width=paper_width,
            )
            _set_doc(current_doc)

            canvas_store.save_layout(target_layout_path, current_doc)
            settings_obj = _build_settings_object(
                target_layout_path,
                target_layout_key=_editor_layout_key(),
            )
            settings_store.save(settings_obj)
            if show_message:
                _show_status(f"{_editor_layout_label()} 레이아웃과 설정 저장 완료")
            return settings_obj
        except Exception as exc:
            if show_message:
                _show_status(f"저장 실패: {exc}")
            return None

    def _refresh_property_panel() -> None:
        selected = _find_selected_element()
        if not selected:
            property_panel.content = _build_property_panel_empty_state()
            return

        def commit_common(_: ft.ControlEvent | None = None) -> None:
            _commit_common_dimension_update(
                current=_find_selected_element(),
                x_value=x_field.value or "",
                y_value=y_field.value or "",
                w_value=w_field.value or "",
                h_value=h_field.value or "",
                coerce_int=_coerce_int,
                update_common_dimensions=_update_common_dimensions,
                upsert_element=_upsert_element,
                refresh_all=_refresh_all,
            )

        def commit_text(_: ft.ControlEvent | None = None) -> None:
            _commit_text_property_update(
                current=_find_selected_element(),
                text_template=text_template_field.value or "",
                font_size_value=font_size_field.value or "",
                bold=bool(bold_btn.selected),
                font_family=font_family_dropdown.value or "malgun",
                coerce_int=_coerce_int,
                upsert_element=_upsert_element,
                refresh_canvas=_refresh_canvas,
                push_update=page.update,
            )

        def commit_image(_: ft.ControlEvent | None = None) -> None:
            _commit_image_property_update(
                current=_find_selected_element(),
                asset_path=image_path_field.value or "",
                preserve_ratio=bool(preserve_ratio_switch.value),
                upsert_element=_upsert_element,
                refresh_canvas=_refresh_canvas,
                push_update=page.update,
            )

        def commit_qr(_: ft.ControlEvent | None = None) -> None:
            _commit_qr_property_update(
                current=_find_selected_element(),
                data_template=qr_data_field.value or "",
                box_size_value=box_size_field.value or "",
                coerce_int=_coerce_int,
                upsert_element=_upsert_element,
                refresh_all=_refresh_all,
                push_update=page.update,
            )

        def pick_image_for_selected(_: ft.ControlEvent) -> None:
            image_picker.pick_files(allow_multiple=False, dialog_title="이미지 선택")

        def _on_field_focus(_: ft.ControlEvent) -> None:
            _set_canvas_focus(False)

        x_field = ft.TextField(label="x", value=str(selected.x), width=100, on_blur=commit_common, on_focus=_on_field_focus)
        y_field = ft.TextField(label="y", value=str(selected.y), width=100, on_blur=commit_common, on_focus=_on_field_focus)
        w_field = ft.TextField(label="넓이", value=str(selected.w), width=100, on_blur=commit_common, on_focus=_on_field_focus)
        h_field = ft.TextField(label="높이", value=str(selected.h), width=100, on_blur=commit_common, on_focus=_on_field_focus)

        align_left_btn = ft.IconButton(icon=ICONS.FORMAT_ALIGN_LEFT_ROUNDED, on_click=lambda _: _apply_align_to_selected("left"), tooltip="좌측 정렬")
        align_center_btn = ft.IconButton(icon=ICONS.FORMAT_ALIGN_CENTER_ROUNDED, on_click=lambda _: _apply_align_to_selected("center"), tooltip="중앙 정렬")
        align_right_btn = ft.IconButton(icon=ICONS.FORMAT_ALIGN_RIGHT_ROUNDED, on_click=lambda _: _apply_align_to_selected("right"), tooltip="우측 정렬")

        controls = _build_property_panel_base_controls(
            selected=selected,
            x_field=x_field,
            y_field=y_field,
            w_field=w_field,
            h_field=h_field,
        )

        if selected.type == "text":
            text_template_field = ft.TextField(
                label="text_template",
                value=selected.text_template,
                multiline=True,
                min_lines=2,
                max_lines=5,
                on_change=commit_text,
                on_focus=lambda _e: (_set_active_binding_target("text_template"), _set_canvas_focus(False)),
            )
            font_family_dropdown = ft.Dropdown(
                label="폰트",
                width=180,
                value=selected.font_family,
                options=[ft.dropdown.Option(key, label) for key, label in FONT_OPTIONS],
                on_change=commit_text,
                dense=True,
            )
            font_size_field = ft.TextField(
                label="크기",
                value=str(selected.font_size),
                width=65,
                on_blur=commit_text,
                on_focus=_on_field_focus,
                dense=True,
            )
            bold_btn = ft.IconButton(
                content=ft.Text("B", size=14, weight=ft.FontWeight.BOLD),
                selected=selected.bold,
                style=ft.ButtonStyle(
                    bgcolor={
                        ft.ControlState.SELECTED: "#DDDDDD",
                        ft.ControlState.DEFAULT: "transparent",
                    },
                    shape=ft.RoundedRectangleBorder(radius=4),
                    side={
                        ft.ControlState.SELECTED: ft.BorderSide(1.5, "#333333"),
                        ft.ControlState.DEFAULT: ft.BorderSide(1, "#AAAAAA"),
                    },
                ),
                width=36,
                height=36,
                on_click=lambda _e: (
                    setattr(bold_btn, "selected", not bold_btn.selected),
                    commit_text(),
                ),
            )
            controls.extend(
                _build_property_panel_text_controls(
                    text_template_field=text_template_field,
                    font_family_dropdown=font_family_dropdown,
                    font_size_field=font_size_field,
                    bold_btn=bold_btn,
                    align_left_btn=align_left_btn,
                    align_center_btn=align_center_btn,
                    align_right_btn=align_right_btn,
                )
            )
        elif selected.type == "image":
            image_path_field = ft.TextField(
                label="asset_path",
                value=selected.asset_path,
                multiline=True,
                min_lines=2,
                max_lines=4,
                on_change=commit_image,
                on_focus=_on_field_focus,
            )
            preserve_ratio_switch = ft.Switch(
                label="비율 유지",
                value=selected.preserve_ratio,
                **_switch_theme_kwargs(),
                on_change=commit_image,
            )
            controls.extend(
                _build_property_panel_image_controls(
                    image_path_field=image_path_field,
                    replace_image_button=ft.ElevatedButton(
                        "이미지 교체",
                        icon=ICONS.IMAGE_SEARCH_ROUNDED,
                        on_click=pick_image_for_selected,
                    ),
                    preserve_ratio_switch=preserve_ratio_switch,
                )
            )
        elif selected.type == "qr":
            qr_data_field = ft.TextField(
                label="data_template",
                value=selected.data_template,
                multiline=True,
                min_lines=2,
                max_lines=5,
                on_change=commit_qr,
                on_focus=lambda _e: (_set_active_binding_target("data_template"), _set_canvas_focus(False)),
            )
            box_size_field = ft.TextField(
                label="QR 크기",
                value=str(selected.box_size),
                width=120,
                on_blur=commit_qr,
                on_focus=_on_field_focus,
            )
            controls.extend(
                _build_property_panel_qr_controls(
                    qr_data_field=qr_data_field,
                    box_size_field=box_size_field,
                )
            )
        elif selected.type == "divider":
            # 연결 필드 옵션 (visibility_tag용)
            _visibility_tag_options = [
                ft.dropdown.Option("", "(없음)"),
            ] + [
                ft.dropdown.Option(key, f"{key}({label})")
                for key, label in FIELD_BINDINGS
            ]

            def commit_divider(_: ft.ControlEvent | None = None) -> None:
                _commit_divider_property_update(
                    current=_find_selected_element(),
                    line_style=line_style_dropdown.value or "solid",
                    line_thickness_value=line_thickness_field.value or "",
                    text_template=divider_text_field.value or "",
                    font_size_value=div_font_size_field.value or "",
                    bold=bool(div_bold_btn.selected),
                    font_family=div_font_family_dropdown.value or "malgun",
                    visibility_tag=visibility_tag_dropdown.value or "",
                    coerce_int=_coerce_int,
                    upsert_element=_upsert_element,
                    refresh_canvas=_refresh_canvas,
                    push_update=page.update,
                )

            line_style_dropdown = ft.Dropdown(
                label="선 스타일",
                width=160,
                value=selected.line_style,
                options=[
                    ft.dropdown.Option("solid", "실선"),
                    ft.dropdown.Option("dashed", "파선"),
                    ft.dropdown.Option("dotted", "점선"),
                    ft.dropdown.Option("none", "선 없음"),
                ],
                on_change=commit_divider,
            )
            line_thickness_field = ft.TextField(
                label="선 굵기(px)",
                value=str(selected.line_thickness),
                width=120,
                keyboard_type=ft.KeyboardType.NUMBER,
                on_blur=commit_divider,
                on_focus=_on_field_focus,
            )
            divider_text_field = ft.TextField(
                label="구분선 텍스트",
                value=selected.text_template,
                hint_text="비워두면 선만 표시",
                on_change=commit_divider,
                on_focus=_on_field_focus,
            )
            div_font_family_dropdown = ft.Dropdown(
                label="폰트",
                width=180,
                value=selected.font_family,
                options=[ft.dropdown.Option(key, label) for key, label in FONT_OPTIONS],
                on_change=commit_divider,
                dense=True,
            )
            div_font_size_field = ft.TextField(
                label="크기",
                value=str(selected.font_size),
                width=65,
                on_blur=commit_divider,
                on_focus=_on_field_focus,
                dense=True,
            )
            div_bold_btn = ft.IconButton(
                content=ft.Text("B", size=14, weight=ft.FontWeight.BOLD),
                selected=selected.bold,
                style=ft.ButtonStyle(
                    bgcolor={
                        ft.ControlState.SELECTED: "#DDDDDD",
                        ft.ControlState.DEFAULT: "transparent",
                    },
                    shape=ft.RoundedRectangleBorder(radius=4),
                    side={
                        ft.ControlState.SELECTED: ft.BorderSide(1.5, "#333333"),
                        ft.ControlState.DEFAULT: ft.BorderSide(1, "#AAAAAA"),
                    },
                ),
                width=36,
                height=36,
                on_click=lambda _e: (
                    setattr(div_bold_btn, "selected", not div_bold_btn.selected),
                    commit_divider(),
                ),
            )
            visibility_tag_dropdown = ft.Dropdown(
                label="연결 필드",
                width=200,
                value=selected.visibility_tag,
                options=_visibility_tag_options,
                on_change=commit_divider,
            )
            controls.extend(
                _build_property_panel_divider_controls(
                    line_style_dropdown=line_style_dropdown,
                    line_thickness_field=line_thickness_field,
                    divider_text_field=divider_text_field,
                    div_font_family_dropdown=div_font_family_dropdown,
                    div_font_size_field=div_font_size_field,
                    div_bold_btn=div_bold_btn,
                    visibility_tag_dropdown=visibility_tag_dropdown,
                )
            )

        _apply_property_panel_controls(
            property_panel=property_panel,
            controls=controls,
        )

    def _drag_delta_from_event(
        e: ft.DragUpdateEvent,
        *,
        start_gx_key: str,
        start_gy_key: str,
        accum_dx_key: str,
        accum_dy_key: str,
    ) -> tuple[float, float]:
        """Return cumulative drag delta in preview pixels.

        한 드래그 시퀀스 내에서는 단일 이벤트 소스(global / delta / local)에 고정해
        Flet 버전·환경에 따라 좌표가 섞여 누적 오차가 생기는 문제를 방지한다.
        """
        source_key = f"{accum_dx_key}__src"
        source = state.get(source_key)

        global_gx = getattr(e, "global_x", None)
        global_gy = getattr(e, "global_y", None)
        start_gx = state.get(start_gx_key)
        start_gy = state.get(start_gy_key)
        delta_x = getattr(e, "delta_x", None)
        delta_y = getattr(e, "delta_y", None)
        local_delta = getattr(e, "local_delta", None)

        if source is None:
            if (
                global_gx is not None and global_gy is not None
                and isinstance(start_gx, (int, float))
                and isinstance(start_gy, (int, float))
            ):
                source = "global"
            elif delta_x is not None and delta_y is not None:
                source = "delta"
            elif local_delta is not None:
                source = "local"
            else:
                source = "none"
            state[source_key] = source

        if source == "global" and global_gx is not None and global_gy is not None \
                and isinstance(start_gx, (int, float)) and isinstance(start_gy, (int, float)):
            dx = float(global_gx) - float(start_gx)
            dy = float(global_gy) - float(start_gy)
            state[accum_dx_key] = dx
            state[accum_dy_key] = dy
            return dx, dy

        if source == "delta" and delta_x is not None and delta_y is not None:
            accum_dx = float(state[accum_dx_key]) + float(delta_x)
            accum_dy = float(state[accum_dy_key]) + float(delta_y)
            state[accum_dx_key] = accum_dx
            state[accum_dy_key] = accum_dy
            return accum_dx, accum_dy

        if source == "local" and local_delta is not None:
            dx = float(getattr(local_delta, "x", 0.0) or 0.0)
            dy = float(getattr(local_delta, "y", 0.0) or 0.0)
            state[accum_dx_key] = dx
            state[accum_dy_key] = dy
            return dx, dy

        return float(state[accum_dx_key]), float(state[accum_dy_key])

    def _start_resize(element_id: str, e: ft.DragStartEvent) -> None:
        """리사이즈 드래그 시작 시 원본 치수 저장"""
        _begin_undo_unit()
        current = next((item for item in _doc().elements if item.id == element_id), None)
        if current:
            state["resize_start_x"] = current.x
            state["resize_start_y"] = current.y
            state["resize_start_w"] = current.w
            state["resize_start_h"] = current.h
            state["resize_bottom_start_y"] = _fixed_bottom_anchor_y(moving_ids={element_id})
        state["resize_pointer_start_gx"] = float(e.global_x)
        state["resize_pointer_start_gy"] = float(e.global_y)
        state["resize_accum_dx"] = 0.0
        state["resize_accum_dy"] = 0.0
        state["resize_accum_dx__src"] = None

    def _handle_resize(element_id: str, corner: str, e: ft.DragUpdateEvent) -> None:
        """리사이즈 핸들 드래그 처리 (누적 delta로 부드러운 변환)"""
        current = next((item for item in _doc().elements if item.id == element_id), None)
        if current is None:
            return
        real_w = _real_canvas_width()
        preview_w = _preview_canvas_width()

        # 프리뷰 delta를 float로 누적
        accum_dx, accum_dy = _drag_delta_from_event(
            e,
            start_gx_key="resize_pointer_start_gx",
            start_gy_key="resize_pointer_start_gy",
            accum_dx_key="resize_accum_dx",
            accum_dy_key="resize_accum_dy",
        )

        dx = preview_to_real(accum_dx, real_width=real_w, preview_width=preview_w)
        dy = preview_to_real(accum_dy, real_width=real_w, preview_width=preview_w)
        resize_bottom_start_y = state.get("resize_bottom_start_y")
        if not isinstance(resize_bottom_start_y, int):
            resize_bottom_start_y = _fixed_bottom_anchor_y(moving_ids={element_id})

        sx = int(state["resize_start_x"])
        sy = int(state["resize_start_y"])
        sw = int(state["resize_start_w"])
        sh = int(state["resize_start_h"])

        new_x, new_y, new_w, new_h = sx, sy, sw, sh
        if corner == "nw":
            new_x = sx + dx; new_w = sw - dx; new_y = sy + dy; new_h = sh - dy
        elif corner == "ne":
            new_w = sw + dx; new_y = sy + dy; new_h = sh - dy
        elif corner == "sw":
            new_x = sx + dx; new_w = sw - dx; new_h = sh + dy
        elif corner == "se":
            new_w = sw + dx; new_h = sh + dy
        elif corner == "n":
            new_y = sy + dy; new_h = sh - dy
        elif corner == "s":
            new_h = sh + dy
        elif corner == "w":
            new_x = sx + dx; new_w = sw - dx
        elif corner == "e":
            new_w = sw + dx

        # 최소 크기 보장
        if new_w < 10:
            new_w = 10
            if corner in ("nw", "sw", "w"):
                new_x = sx + sw - 10
        if new_h < 10:
            new_h = 10
            if corner in ("nw", "ne", "n"):
                new_y = sy + sh - 10

        # 캔버스 경계 클램프 (폭만 제한, 높이는 자동 확장, 여백 반영)
        c_w = int(_doc().meta.canvas_width_px)
        new_x = max(0, new_x)
        new_y = _clamp_y_to_reserved_margins(new_y, new_h, bottom_start_y=resize_bottom_start_y)
        new_w = min(new_w, c_w - new_x)

        # 리사이즈 스냅 가이드 적용
        new_x, new_y, new_w, new_h, guides = _calc_resize_snap(
            element_id, new_x, new_y, new_w, new_h, corner,
        )
        new_y = _clamp_y_to_reserved_margins(new_y, new_h, bottom_start_y=resize_bottom_start_y)

        canvas_stack = state.get("canvas_stack")
        _replace_canvas_snap_guides(
            state=state,
            canvas_stack=canvas_stack if isinstance(canvas_stack, ft.Stack) else None,
            guides=guides,
            build_guide_lines=_build_guide_lines,
        )

        updated = _update_common_dimensions(
            current,
            x=new_x,
            y=new_y,
            w=new_w,
            h=new_h,
            bottom_start_y=resize_bottom_start_y,
        )
        _upsert_element(updated)

        # 리사이즈 중 요소 크기/위치를 시각적으로 실시간 반영
        det = state.get("selected_detector")
        bod = state.get("selected_body")
        if det and bod:
            det.left = real_to_preview(updated.x, real_width=real_w, preview_width=preview_w)
            det.top = real_to_preview(updated.y, real_width=real_w, preview_width=preview_w)
            bod.width = max(8, real_to_preview(updated.w, real_width=real_w, preview_width=preview_w))
            bod.height = max(8, real_to_preview(updated.h, real_width=real_w, preview_width=preview_w))

        _refresh_property_panel()
        page.update()

    def _end_resize(_: ft.DragEndEvent) -> None:
        """리사이즈 종료 시 스티키 정렬 + 전체 리프레시"""
        try:
            _resolve_overlaps_sticky()
            _refresh_all()
        except Exception:
            logger.exception("리사이즈 종료 처리 중 예외")
        finally:
            _reset_resize_interaction_state(state=state)
            state["resize_accum_dx__src"] = None

    def _calc_optimal_size(element: ReceiptCanvasElement) -> tuple[int, int]:
        """요소의 텍스트 내용에 맞는 최적 크기(w, h) 계산 (실제 px)"""
        padding_x, padding_y = 16, 16
        if element.type == "text":
            font = _load_font_for_measure(element.font_size, bold=element.bold)
            text = (element.text_template or "").strip()
            if not text:
                # 빈 텍스트: 현재 크기 유지 (최소 보장)
                return max(60, element.w), max(30, element.h)
            lines = text.split("\n")
            ascent, descent = font.getmetrics()
            line_h = ascent + descent
            max_w = 0
            for line in lines:
                bbox = font.getbbox(line or " ")
                max_w = max(max_w, bbox[2] - bbox[0])
            total_h = line_h * len(lines)
            return max(60, max_w + padding_x), max(30, total_h + padding_y)
        if element.type == "divider":
            return element.w, max(12, getattr(element, "line_thickness", 1) + 10)
        if element.type == "qr":
            size = max(element.w, element.h)
            return size, size
        return element.w, element.h

    def _load_font_for_measure(size: int, *, bold: bool = False):
        """텍스트 측정용 폰트 로드"""
        from PIL import ImageFont
        candidates: list[str] = []
        if bold:
            candidates.append(r"C:\Windows\Fonts\malgunbd.ttf")
        candidates.extend([r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\gulim.ttc"])
        for c in candidates:
            if Path(c).exists():
                try:
                    return ImageFont.truetype(c, max(8, int(size)))
                except Exception:
                    continue
        return ImageFont.load_default()

    def _auto_fit_edge(element_id: str, edge: str) -> None:
        """핸들 더블클릭 시 해당 방향 최적 크기 맞춤"""
        current = next((el for el in _doc().elements if el.id == element_id), None)
        if not current:
            return
        opt_w, opt_h = _calc_optimal_size(current)
        c_w = int(_doc().meta.canvas_width_px)
        mt = _margin_top_px()
        new_x, new_y, new_w, new_h = current.x, current.y, current.w, current.h

        if edge in ("n", "s", "nw", "ne", "sw", "se"):
            # 높이 맞춤
            if edge in ("n", "nw", "ne"):
                # 상단 핸들: 아래쪽 고정, 위로 확장/축소
                bottom = current.y + current.h
                new_h = opt_h
                new_y = max(mt, bottom - new_h)
            else:
                # 하단 핸들: 위쪽 고정, 아래로 확장/축소
                new_h = opt_h
        if edge in ("w", "e", "nw", "ne", "sw", "se"):
            # 폭 맞춤
            if edge in ("w", "nw", "sw"):
                # 좌측 핸들: 우측 고정, 좌로 확장/축소
                right = current.x + current.w
                new_w = min(opt_w, c_w)
                new_x = max(0, right - new_w)
            else:
                # 우측 핸들: 좌측 고정, 우로 확장/축소
                new_w = min(opt_w, c_w - current.x)

        updated = _update_common_dimensions(current, x=new_x, y=new_y, w=new_w, h=new_h)
        _upsert_element(updated)
        _resolve_overlaps()
        _refresh_all()

    def _auto_fit_to_content(element_id: str) -> None:
        """텍스트 변경 후 요소 크기를 내용에 맞춤 (x, y 유지, 우측/하단 확장)"""
        current = next((el for el in _doc().elements if el.id == element_id), None)
        if not current or current.type not in ("text", "divider"):
            return
        opt_w, opt_h = _calc_optimal_size(current)
        c_w = int(_doc().meta.canvas_width_px)
        new_w = min(opt_w, c_w - current.x)
        new_h = opt_h
        if new_w == current.w and new_h == current.h:
            return
        updated = _update_common_dimensions(current, w=new_w, h=new_h)
        _upsert_element(updated)
        _resolve_overlaps()

    def _build_resize_handles(element: ReceiptCanvasElement) -> list[ft.Control]:
        """선택된 요소의 4코너 리사이즈 핸들 생성"""
        real_w = _real_canvas_width()
        preview_w = _preview_canvas_width()
        preview_h = _preview_canvas_height()
        px = real_to_preview(element.x, real_width=real_w, preview_width=preview_w)
        py = real_to_preview(element.y, real_width=real_w, preview_width=preview_w)
        pw = max(8, real_to_preview(element.w, real_width=real_w, preview_width=preview_w))
        ph = max(8, real_to_preview(element.h, real_width=real_w, preview_width=preview_w))

        handle_size = 8
        half = handle_size // 2

        def _clamp_handle(hx: int, hy: int, hw: int, hh: int) -> tuple[int, int]:
            """핸들이 캔버스 영역 밖으로 나가지 않도록 클램핑"""
            clamped_x = max(0, min(hx, preview_w - hw))
            clamped_y = max(0, min(hy, preview_h - hh))
            return clamped_x, clamped_y

        # 꼭짓점 핸들 (정사각형) - 캔버스 경계 내로 클램핑
        corner_handles = {
            "nw": _clamp_handle(px - half, py - half, handle_size, handle_size),
            "ne": _clamp_handle(px + pw - half, py - half, handle_size, handle_size),
            "sw": _clamp_handle(px - half, py + ph - half, handle_size, handle_size),
            "se": _clamp_handle(px + pw - half, py + ph - half, handle_size, handle_size),
        }
        # 변 중간 핸들 (가로/세로 직사각형) - 캔버스 경계 내로 클램핑
        _n = _clamp_handle(px + pw // 2 - half, py - half, handle_size * 2, handle_size)
        _s = _clamp_handle(px + pw // 2 - half, py + ph - half, handle_size * 2, handle_size)
        _w = _clamp_handle(px - half, py + ph // 2 - half, handle_size, handle_size * 2)
        _e = _clamp_handle(px + pw - half, py + ph // 2 - half, handle_size, handle_size * 2)
        edge_handles = {
            "n": (*_n, handle_size * 2, handle_size),
            "s": (*_s, handle_size * 2, handle_size),
            "w": (*_w, handle_size, handle_size * 2),
            "e": (*_e, handle_size, handle_size * 2),
        }
        handles: list[ft.Control] = []
        for corner, (hx, hy) in corner_handles.items():
            handles.append(
                ft.GestureDetector(
                    on_pan_start=lambda e, _eid=element.id: _start_resize(_eid, e),
                    on_pan_update=lambda e, c=corner: _handle_resize(element.id, c, e),
                    on_pan_end=_end_resize,
                    on_double_tap=lambda _e, c=corner: _auto_fit_edge(element.id, c),
                    left=hx,
                    top=hy,
                    content=ft.Container(
                        width=handle_size,
                        height=handle_size,
                        bgcolor="#FF4B4B",
                        border=ft.border.all(1, "#CC0000"),
                        border_radius=2,
                    ),
                )
            )
        for edge, (hx, hy, hw, hh) in edge_handles.items():
            handles.append(
                ft.GestureDetector(
                    on_pan_start=lambda e, _eid=element.id: _start_resize(_eid, e),
                    on_pan_update=lambda e, c=edge: _handle_resize(element.id, c, e),
                    on_pan_end=_end_resize,
                    on_double_tap=lambda _e, c=edge: _auto_fit_edge(element.id, c),
                    left=hx,
                    top=hy,
                    content=ft.Container(
                        width=hw,
                        height=hh,
                        bgcolor="#FF4B4B",
                        border=ft.border.all(1, "#CC0000"),
                        border_radius=2,
                    ),
                )
            )
        return handles

    def _build_element_preview(element: ReceiptCanvasElement) -> ft.Control:
        preview_w = _preview_canvas_width()
        real_w = _real_canvas_width()
        preview_x = real_to_preview(element.x, real_width=real_w, preview_width=preview_w)
        preview_y = real_to_preview(element.y, real_width=real_w, preview_width=preview_w)
        preview_element_w = max(8, real_to_preview(element.w, real_width=real_w, preview_width=preview_w))
        preview_element_h = max(8, real_to_preview(element.h, real_width=real_w, preview_width=preview_w))

        is_selected = element.id == _selected_id()
        border_color = "#FF4B4B" if is_selected else "#A5A5A5"
        bg_color = "#FFF7E0" if is_selected else "#FFFFFF"

        if element.type == "text":
            is_inline_editing = state["inline_edit_id"] == element.id

            if is_inline_editing:
                # 인라인 편집 모드: TextField 표시
                def _commit_inline(e: ft.ControlEvent) -> None:
                    current = next((el for el in _doc().elements if el.id == element.id), None)
                    if current:
                        _upsert_element(
                            _update_element_text_template(
                                current,
                                text_template=e.control.value or "",
                            )
                        )
                    state["inline_edit_id"] = None
                    _refresh_all()

                def _inline_on_submit(e: ft.ControlEvent) -> None:
                    _commit_inline(e)

                inline_field = ft.TextField(
                    value=element.text_template,
                    multiline=True,
                    min_lines=1,
                    max_lines=5,
                    text_size=max(10, int(element.font_size * _preview_scale() * 0.9)),
                    text_style=ft.TextStyle(font_family=element.font_family),
                    text_align=_align_to_text_align(element.align),
                    border_color="#4A90D9",
                    focused_border_color="#4A90D9",
                    content_padding=ft.padding.all(4),
                    on_blur=_commit_inline,
                    on_submit=_inline_on_submit,
                    autofocus=True,
                )
                body = ft.Container(
                    width=preview_element_w,
                    height=preview_element_h,
                    bgcolor="#FFFDE7",
                    border=ft.border.all(2, "#4A90D9"),
                    border_radius=6,
                    padding=2,
                    alignment=_align_to_container_align(element.align),
                    content=inline_field,
                )
            else:
                # 일반 표시 모드
                text_preview = element.text_template or ""
                text_widget = ft.Text(
                    text_preview,
                    size=max(10, int(element.font_size * _preview_scale() * 0.9)),
                    font_family=element.font_family,
                    weight=ft.FontWeight.BOLD if element.bold else ft.FontWeight.NORMAL,
                    text_align=_align_to_text_align(element.align),
                    max_lines=5,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    width=preview_element_w - 12,
                )
                body = ft.Container(
                    width=preview_element_w,
                    height=preview_element_h,
                    bgcolor=bg_color,
                    border=ft.border.all(2 if is_selected else 1, border_color),
                    border_radius=6,
                    padding=6,
                    alignment=_align_to_container_align(element.align),
                    content=text_widget,
                )
        elif element.type == "divider":
            is_inline_editing = state["inline_edit_id"] == element.id

            if is_inline_editing:
                # 구분선 인라인 편집 모드
                def _commit_div_inline(e: ft.ControlEvent) -> None:
                    current = next((el for el in _doc().elements if el.id == element.id), None)
                    if current:
                        _upsert_element(
                            _update_element_text_template(
                                current,
                                text_template=e.control.value or "",
                            )
                        )
                    state["inline_edit_id"] = None
                    _refresh_all()

                div_inline_field = ft.TextField(
                    value=element.text_template,
                    text_size=max(9, int(12 * _preview_scale())),
                    text_style=ft.TextStyle(font_family=element.font_family),
                    text_align=ft.TextAlign.CENTER,
                    border_color="#4A90D9",
                    content_padding=ft.padding.all(2),
                    on_blur=_commit_div_inline,
                    on_submit=_commit_div_inline,
                    autofocus=True,
                )
                body = ft.Container(
                    width=preview_element_w,
                    height=preview_element_h,
                    bgcolor="#FFFDE7",
                    border=ft.border.all(2, "#4A90D9"),
                    border_radius=4,
                    alignment=ALIGN_CENTER,
                    content=div_inline_field,
                )
            else:
                # 구분선 일반 표시 모드
                line_w = preview_element_w - 12
                line_h = max(1, int(element.line_thickness * _preview_scale()))
                line_color = "#333333"
                style = element.line_style

                def _make_line_ctrl(w: float) -> ft.Control:
                    """선 스타일에 따른 프리뷰 컨트롤 생성"""
                    if style == "none":
                        # 선 없음: 투명 여백만 차지
                        return ft.Container(width=w, height=line_h)
                    if style == "dashed":
                        segs: list[ft.Control] = []
                        cx = 0.0
                        while cx < w:
                            seg_w = min(8.0, w - cx)
                            segs.append(ft.Container(width=seg_w, height=line_h, bgcolor=line_color))
                            cx += 12.0
                        return ft.Row(controls=segs, spacing=4, wrap=False)
                    if style == "dotted":
                        dots: list[ft.Control] = []
                        cx = 0.0
                        while cx < w:
                            dot_w = min(3.0, w - cx)
                            dots.append(ft.Container(width=dot_w, height=line_h, bgcolor=line_color, border_radius=line_h))
                            cx += 6.0
                        return ft.Row(controls=dots, spacing=3, wrap=False)
                    return ft.Container(width=w, height=line_h, bgcolor=line_color)

                div_text = (element.text_template or "").strip()

                if div_text:
                    # 선 + 텍스트: 좌측 선 — 텍스트 — 우측 선
                    divider_content = ft.Row(
                        controls=[
                            ft.Container(expand=True, content=_make_line_ctrl(line_w * 0.4), alignment=ALIGN_CENTER),
                            ft.Container(
                                padding=ft.padding.symmetric(horizontal=4),
                                content=ft.Text(
                                    div_text,
                                    size=max(9, int(element.font_size * _preview_scale() * 0.9)),
                                    font_family=element.font_family,
                                    weight=ft.FontWeight.BOLD if element.bold else ft.FontWeight.NORMAL,
                                    color="#333333",
                                    text_align=ft.TextAlign.CENTER,
                                    no_wrap=True,
                                ),
                            ),
                            ft.Container(expand=True, content=_make_line_ctrl(line_w * 0.4), alignment=ALIGN_CENTER),
                        ],
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    )
                else:
                    divider_content = _make_line_ctrl(line_w)

                body = ft.Container(
                    width=preview_element_w,
                    height=preview_element_h,
                    bgcolor=bg_color,
                    border=ft.border.all(2 if is_selected else 1, border_color),
                    border_radius=4,
                    padding=ft.padding.symmetric(horizontal=6),
                    alignment=ALIGN_CENTER,
                    content=divider_content,
                )
        elif element.type == "image":
            if element.asset_path and Path(element.asset_path).exists():
                image_fit = IMAGE_FIT_CONTAIN if element.preserve_ratio else IMAGE_FIT_FILL
                content = ft.Image(src=element.asset_path, fit=image_fit, width=preview_element_w - 8, height=preview_element_h - 8)
            else:
                content = ft.Column(
                    controls=[
                        ft.Icon(ICONS.IMAGE_NOT_SUPPORTED_ROUNDED, color="#909090"),
                        ft.Text("이미지 없음", size=11, color="#777777"),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=4,
                )
            body = ft.Container(
                width=preview_element_w,
                height=preview_element_h,
                bgcolor=bg_color,
                border=ft.border.all(2 if is_selected else 1, border_color),
                border_radius=6,
                padding=4,
                alignment=ALIGN_CENTER,
                content=content,
            )
        else:
            body = ft.Container(
                width=preview_element_w,
                height=preview_element_h,
                bgcolor=bg_color,
                border=ft.border.all(2 if is_selected else 1, border_color),
                border_radius=6,
                content=ft.Column(
                    controls=[
                        ft.Icon(ICONS.QR_CODE_ROUNDED, color="#4A4A4A"),
                        ft.Text("QR", size=12, color="#333333"),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=4,
                ),
            )

        # GestureDetector를 먼저 생성 (핸들러에서 직접 참조하기 위함)
        detector = ft.GestureDetector(
            left=preview_x,
            top=preview_y,
            content=body,
        )

        # 선택된 요소의 참조 저장 (리사이즈 시 시각 갱신용)
        if is_selected:
            state["selected_detector"] = detector
            state["selected_body"] = body

        def on_tap(_: ft.ControlEvent) -> None:
            _apply_element_tap_selection(
                state=state,
                element=element,
                current_selected_id=_selected_id(),
                tap_time=time.monotonic(),
                set_canvas_focus=_set_canvas_focus,
                set_selected_id=_set_selected_id,
                set_active_binding_target=_set_active_binding_target,
            )
            _refresh_all()

        def on_double_tap(_: ft.ControlEvent) -> None:
            """더블탭으로 텍스트/구분선 인라인 편집 진입"""
            if _apply_element_double_tap_edit(
                state=state,
                element=element,
                set_canvas_focus=_set_canvas_focus,
                set_selected_id=_set_selected_id,
            ):
                _refresh_all()

        def on_pan_start(e: ft.DragStartEvent) -> None:
            _set_canvas_focus(True)
            _set_selected_id(element.id)
            _begin_undo_unit()
            state["drag_bottom_start_y"] = _fixed_bottom_anchor_y(moving_ids={element.id})
            current = next((item for item in _doc().elements if item.id == element.id), None)
            if current:
                state["drag_start_x"] = current.x
                state["drag_start_y"] = current.y
            state["drag_pointer_start_gx"] = float(e.global_x)
            state["drag_pointer_start_gy"] = float(e.global_y)
            state["drag_accum_dx"] = 0.0
            state["drag_accum_dy"] = 0.0
            state["drag_accum_dx__src"] = None

        def on_pan_update(e: ft.DragUpdateEvent) -> None:
            moving_ids = {element.id}
            drag_bottom_start_y = state.get("drag_bottom_start_y")
            if not isinstance(drag_bottom_start_y, int):
                drag_bottom_start_y = _fixed_bottom_anchor_y(moving_ids=moving_ids)
            accum_dx, accum_dy = _drag_delta_from_event(
                e,
                start_gx_key="drag_pointer_start_gx",
                start_gy_key="drag_pointer_start_gy",
                accum_dx_key="drag_accum_dx",
                accum_dy_key="drag_accum_dy",
            )
            total_real_dx = preview_to_real(accum_dx, real_width=real_w, preview_width=preview_w)
            total_real_dy = preview_to_real(accum_dy, real_width=real_w, preview_width=preview_w)
            c_w = int(_doc().meta.canvas_width_px)

            current = next((item for item in _doc().elements if item.id == element.id), None)
            if current is None:
                return
            start_x = int(state["drag_start_x"])
            start_y = int(state["drag_start_y"])
            raw_x = start_x + total_real_dx
            raw_y = start_y + total_real_dy
            clamped_x = max(0, min(int(raw_x), max(0, c_w - max(1, current.w))))
            clamped_y = max(0, int(raw_y))
            new_x, new_y, guides = _calc_snap(
                current,
                clamped_x,
                clamped_y,
                exclude_ids=moving_ids,
                margin_bottom_start_y=drag_bottom_start_y,
            )
            new_x = max(0, min(int(new_x), max(0, c_w - max(1, current.w))))
            new_y = max(0, int(new_y))
            canvas_stack = state.get("canvas_stack")
            _replace_canvas_snap_guides(
                state=state,
                canvas_stack=canvas_stack if isinstance(canvas_stack, ft.Stack) else None,
                guides=guides,
                build_guide_lines=_build_guide_lines,
            )
            updated = _update_common_dimensions(current, x=new_x, y=new_y, bottom_start_y=drag_bottom_start_y)
            _upsert_element(updated)
            detector.left = real_to_preview(updated.x, real_width=real_w, preview_width=preview_w)
            detector.top = real_to_preview(updated.y, real_width=real_w, preview_width=preview_w)

            # 삽입 인디케이터 갱신
            slot_y = _calc_insertion_slot(updated, new_y, {element.id})
            _update_canvas_insertion_indicator(
                state=state,
                canvas_stack=canvas_stack if isinstance(canvas_stack, ft.Stack) else None,
                slot_y=slot_y,
                build_insertion_indicator=_build_insertion_indicator,
            )

            # 드래그 자동스크롤
            _auto_scroll_on_drag(
                real_to_preview(updated.y, real_width=real_w, preview_width=preview_w),
                real_to_preview(updated.h, real_width=real_w, preview_width=preview_w),
            )
            # 드래그 중 property panel 갱신 생략 (on_pan_end → _refresh_all에서 갱신)
            # 30fps(33ms) throttle: 매 프레임 page.update 대신 부하 절반으로 감소
            _now = time.monotonic()
            if _now - float(state.get("last_pan_update_time", 0.0)) >= 0.033:
                state["last_pan_update_time"] = _now
                page.update()

        def on_pan_end(_: ft.DragEndEvent) -> None:
            try:
                canvas_stack = state.get("canvas_stack")
                _clear_canvas_insertion_indicator(
                    state=state,
                    canvas_stack=canvas_stack if isinstance(canvas_stack, ft.Stack) else None,
                )

                target_y = _consume_canvas_insertion_target(state=state)
                if target_y is not None:
                    _apply_insertion_drop(element.id, target_y)
                else:
                    _enforce_margin_boundaries()   # 내부에서 _resolve_overlaps_sticky() 포함
                _refresh_all()
            except Exception:
                logger.exception("on_pan_end 처리 중 예외")
            finally:
                _reset_drag_interaction_state(state=state)
                state["drag_accum_dx__src"] = None

        detector.on_tap = on_tap
        detector.on_double_tap = on_double_tap
        detector.on_pan_start = on_pan_start
        detector.on_pan_update = on_pan_update
        detector.on_pan_end = on_pan_end
        return detector

    def _calc_snap(
        dragging: ReceiptCanvasElement,
        new_x: int,
        new_y: int,
        threshold: int = 8,
        *,
        exclude_ids: set[str] | None = None,
        margin_bottom_start_y: int | None = None,
    ) -> tuple[int, int, list[dict]]:
        """드래그 중인 요소와 다른 visible 요소 + 여백 경계 간 스냅 계산"""
        guides: list[dict] = []
        snapped_x, snapped_y = new_x, new_y
        dw, dh = dragging.w, dragging.h
        excluded = set(exclude_ids or set())
        excluded.add(dragging.id)

        # 드래그 요소의 6축 좌표
        d_xs = [new_x, new_x + dw // 2, new_x + dw]
        d_ys = [new_y, new_y + dh // 2, new_y + dh]

        best_dx: int | None = None
        best_dy: int | None = None
        best_dist_x = threshold + 1
        best_dist_y = threshold + 1
        snap_line_x: int | None = None
        snap_line_y: int | None = None

        # 여백 경계를 Y축 스냅 대상에 추가
        mt = _margin_top_px()
        bottom_start = (
            int(margin_bottom_start_y)
            if margin_bottom_start_y is not None
            else _bottom_margin_start_y(exclude_ids=excluded)
        )

        # 상단 여백: 요소 top이 여백 경계 아래에 붙음
        if mt > 0:
            dist = abs(d_ys[0] - mt)
            if dist < best_dist_y:
                best_dist_y = dist
                best_dy = mt
                snap_line_y = mt

        # 하단 여백: 요소 bottom이 여백 경계 위에 붙음
        if bottom_start is not None:
            dist = abs(d_ys[2] - bottom_start)
            if dist < best_dist_y:
                best_dist_y = dist
                best_dy = bottom_start - dh
                snap_line_y = bottom_start

        for el in _doc().elements:
            if el.id in excluded or not el.visible:
                continue
            t_xs = [el.x, el.x + el.w // 2, el.x + el.w]
            t_ys = [el.y, el.y + el.h // 2, el.y + el.h]

            # X축 매칭
            for di, dx_val in enumerate(d_xs):
                for tx_val in t_xs:
                    dist = abs(dx_val - tx_val)
                    if dist < best_dist_x:
                        best_dist_x = dist
                        best_dx = tx_val - [0, dw // 2, dw][di]
                        snap_line_x = tx_val

            # Y축 매칭
            for di, dy_val in enumerate(d_ys):
                for ty_val in t_ys:
                    dist = abs(dy_val - ty_val)
                    if dist < best_dist_y:
                        best_dist_y = dist
                        best_dy = ty_val - [0, dh // 2, dh][di]
                        snap_line_y = ty_val

        if best_dx is not None and best_dist_x <= threshold:
            snapped_x = best_dx
            guides.append({"axis": "vertical", "pos": snap_line_x})
        if best_dy is not None and best_dist_y <= threshold:
            snapped_y = best_dy
            guides.append({"axis": "horizontal", "pos": snap_line_y})

        return snapped_x, snapped_y, guides

    def _calc_resize_snap(
        element_id: str,
        x: int, y: int, w: int, h: int,
        corner: str,
        threshold: int = 8,
    ) -> tuple[int, int, int, int, list[dict]]:
        """리사이즈 시 움직이는 변(edge)을 다른 요소에 스냅"""
        guides: list[dict] = []

        # 코너별 스냅 대상 edge 결정
        # nw: left, top 이동 / ne: right, top / sw: left, bottom / se: right, bottom
        snap_left = corner in ("nw", "sw", "w")
        snap_right = corner in ("ne", "se", "e")
        snap_top = corner in ("nw", "ne", "n")
        snap_bottom = corner in ("sw", "se", "s")

        # 현재 요소의 edge 좌표
        edges_x = []
        if snap_left:
            edges_x.append(("left", x))
        if snap_right:
            edges_x.append(("right", x + w))
        edges_y = []
        if snap_top:
            edges_y.append(("top", y))
        if snap_bottom:
            edges_y.append(("bottom", y + h))

        best_snap_x: tuple[str, int, int] | None = None  # (edge, snap_to, dist)
        best_snap_y: tuple[str, int, int] | None = None
        best_dist_x = threshold + 1
        best_dist_y = threshold + 1

        for el in _doc().elements:
            if el.id == element_id or not el.visible:
                continue
            t_xs = [el.x, el.x + el.w // 2, el.x + el.w]
            t_ys = [el.y, el.y + el.h // 2, el.y + el.h]

            for edge_name, edge_val in edges_x:
                for tx in t_xs:
                    dist = abs(edge_val - tx)
                    if dist < best_dist_x:
                        best_dist_x = dist
                        best_snap_x = (edge_name, tx, dist)

            for edge_name, edge_val in edges_y:
                for ty in t_ys:
                    dist = abs(edge_val - ty)
                    if dist < best_dist_y:
                        best_dist_y = dist
                        best_snap_y = (edge_name, ty, dist)

        new_x, new_y, new_w, new_h = x, y, w, h

        if best_snap_x and best_snap_x[2] <= threshold:
            edge_name, snap_to, _ = best_snap_x
            if edge_name == "left":
                new_w = w + (x - snap_to)
                new_x = snap_to
            else:  # right
                new_w = snap_to - x
            guides.append({"axis": "vertical", "pos": snap_to})

        if best_snap_y and best_snap_y[2] <= threshold:
            edge_name, snap_to, _ = best_snap_y
            if edge_name == "top":
                new_h = h + (y - snap_to)
                new_y = snap_to
            else:  # bottom
                new_h = snap_to - y
            guides.append({"axis": "horizontal", "pos": snap_to})

        # 최소 크기 보장
        if new_w < 10:
            new_w = 10
            if snap_left:
                new_x = x + w - 10
        if new_h < 10:
            new_h = 10
            if snap_top:
                new_y = y + h - 10

        return new_x, new_y, new_w, new_h, guides

    def _calc_insertion_slot(
        dragging: ReceiptCanvasElement,
        drag_y: int,
        exclude_ids: set[str],
    ) -> int | None:
        """드래그 중심 Y 기준, X겹침 요소들 사이 삽입 슬롯 계산"""
        gap = 0
        threshold = 30
        drag_center_y = drag_y + dragging.h // 2

        # X겹침이 있는 visible 요소만 수집 (자신 제외)
        neighbors: list[ReceiptCanvasElement] = []
        for el in _doc().elements:
            if el.id in exclude_ids or not el.visible:
                continue
            if el.x >= dragging.x + dragging.w or dragging.x >= el.x + el.w:
                continue
            neighbors.append(el)

        if not neighbors:
            return None

        neighbors.sort(key=lambda e: e.y)

        # 슬롯 후보: 첫 요소 위, 요소 사이, 마지막 요소 아래
        slots: list[int] = []
        slots.append(neighbors[0].y - gap)  # 첫 요소 위
        for idx in range(len(neighbors) - 1):
            mid = neighbors[idx].y + neighbors[idx].h + gap
            slots.append(mid)
        slots.append(neighbors[-1].y + neighbors[-1].h + gap)  # 마지막 요소 아래

        # 드래그 중심에서 가장 가까운 슬롯 선택
        best_slot: int | None = None
        best_dist = threshold + 1
        for s in slots:
            dist = abs(drag_center_y - s)
            if dist < best_dist:
                best_dist = dist
                best_slot = s
        return best_slot

    def _build_insertion_indicator(slot_y: int) -> ft.Container:
        """삽입 위치 표시선 (주황색 수평선)"""
        preview_w = _preview_canvas_width()
        real_w = _real_canvas_width()
        preview_y = real_to_preview(slot_y, real_width=real_w, preview_width=preview_w)
        return ft.Container(
            left=0,
            top=preview_y - 1,
            width=preview_w,
            height=3,
            bgcolor="#FF8C00",
            opacity=0.85,
            border_radius=1,
        )

    def _apply_insertion_drop(dragged_id: str, slot_y: int) -> None:
        """드래그된 요소를 슬롯 위치로 이동 후 겹침 해소"""
        el = next((e for e in _doc().elements if e.id == dragged_id), None)
        if not el:
            return
        updated = replace(el, y=slot_y)
        # 삽입 위치에 겹치는 기존 요소들을 먼저 아래로 밀어냄 (gap=0, resolve와 일관성)
        gap = 0
        push_below = slot_y + updated.h + gap
        elements = list(_doc().elements)
        changed = False
        for i, other in enumerate(elements):
            if other.id == dragged_id or not other.visible:
                continue
            if other.x >= updated.x + updated.w or updated.x >= other.x + other.w:
                continue
            if other.y >= slot_y and other.y < push_below:
                elements[i] = replace(other, y=push_below)
                changed = True
        if changed:
            _set_doc(replace(_doc(), elements=elements))
        _upsert_element(updated)
        _resolve_overlaps_sticky()   # pull-up + push-down: 삽입 후 전체 빈 공간 제거

    def _build_guide_lines(guides: list[dict]) -> list[ft.Control]:
        """스냅 가이드선 컨트롤 생성"""
        preview_w = _preview_canvas_width()
        preview_h = _preview_canvas_height()
        real_w = _real_canvas_width()
        controls: list[ft.Control] = []
        for g in guides:
            pos = real_to_preview(g["pos"], real_width=real_w, preview_width=preview_w)
            if g["axis"] == "vertical":
                controls.append(ft.Container(
                    left=pos, top=0, width=1, height=preview_h,
                    bgcolor="#4A90D9", opacity=0.7,
                ))
            else:
                controls.append(ft.Container(
                    left=0, top=pos, height=1, width=preview_w,
                    bgcolor="#4A90D9", opacity=0.7,
                ))
        return controls

    def _refresh_canvas() -> None:
        preview_w = _preview_canvas_width()
        preview_h = _preview_canvas_height()
        scale = _preview_scale()
        max_viewport_h = 500
        viewport_h = min(preview_h, max_viewport_h)

        canvas_stack, _canvas_frame, canvas_frame_body, scrollable_canvas, canvas_meta_text = _ensure_canvas_scaffold()

        mt_preview = max(0, int(_margin_top_px() * scale))
        mb_preview = max(0, int(_margin_bottom_px() * scale))
        selected_el = _find_selected_element()
        canvas_controls = _build_canvas_preview_controls(
            preview_width=preview_w,
            preview_height=preview_h,
            margin_top_preview=mt_preview,
            margin_bottom_preview=mb_preview,
            visible_elements=[element for element in _doc().elements if element.visible],
            selected_element=selected_el,
            snap_guides=list(state["snap_guides"]),
            build_element_preview=_build_element_preview,
            build_resize_handles=_build_resize_handles,
        )

        _apply_canvas_stack_view_state(
            canvas_stack=canvas_stack,
            canvas_controls=canvas_controls,
            preview_width=preview_w,
            preview_height=preview_h,
            state=state,
        )
        _apply_canvas_frame_body_view_state(
            canvas_frame_body=canvas_frame_body,
            canvas_stack=canvas_stack,
            preview_width=preview_w,
            preview_height=preview_h,
        )
        _apply_canvas_scroll_view_state(
            scrollable_canvas=scrollable_canvas,
            scroll_gutter=state.get("scroll_gutter_ctrl"),
            preview_height=preview_h,
            viewport_height=viewport_h,
            max_viewport_height=max_viewport_h,
        )

        canvas_meta_text.value = _format_canvas_meta_text(
            preview_width=preview_w,
            preview_height=preview_h,
            real_canvas_width=_doc().meta.canvas_width_px,
            margin_top=_margin_top_px(),
            margin_bottom=_margin_bottom_px(),
        )

    def _refresh_all(push_update: bool = True) -> None:
        _refresh_editor_view_state(
            refresh_canvas=_refresh_canvas,
            refresh_property_panel=_refresh_property_panel,
            page=page,
            push_update=push_update,
        )

    def on_add_text(_: ft.ControlEvent) -> None:
        _add_element(_new_default_element("text"))
        _set_active_binding_target("text_template")
        _refresh_all()

    def on_add_divider(_: ft.ControlEvent) -> None:
        _add_element(_new_default_element("divider"))
        _refresh_all()

    def on_add_image(_: ft.ControlEvent) -> None:
        image_picker.pick_files(allow_multiple=False, dialog_title="이미지 선택")

    def on_save(_: ft.ControlEvent) -> None:
        saved = _save_current_layout(show_message=True)
        if saved:
            _show_status("저장 완료")

    def _save_portable_layout(path: str | None) -> None:
        if not path:
            return
        target_path = path if path.lower().endswith(".json") else f"{path}.json"
        try:
            canvas_store.export_portable(target_path, _doc())
            _show_status(f"포터블 저장 완료: {Path(target_path).name}")
        except Exception as exc:
            _show_status(f"포터블 저장 실패: {exc}")
        _refresh_all()

    def on_save_as(_: ft.ControlEvent) -> None:
        save_as_picker.save_file(
            dialog_title="템플릿 다른이름으로 저장",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["json"],
            file_name=(
                "product_receipt_layout.json"
                if _editor_layout_key() == "product"
                else "receipt_layout.json"
            ),
        )

    def _load_layout_from_file(path: str | None) -> None:
        if not path:
            _show_status("불러오기 실패: 선택한 파일 경로를 읽을 수 없습니다.")
            return
        try:
            doc = canvas_store.load_layout(path)
            # 외부 템플릿은 기본 경로에 저장하여 asset 경로 불일치 방지
            default_layout_path = _resolve_editor_default_layout_path(
                _editor_layout_key()
            )
            _apply_loaded_layout_document(
                doc=doc,
                default_layout_path=default_layout_path,
                set_doc=_set_doc,
                set_layout_path=_set_layout_path,
                set_selected_id=_set_selected_id,
                paper_width_dropdown=paper_width_dropdown,
                save_layout=canvas_store.save_layout,
                refresh_all=_refresh_all,
            )
            _show_status(f"불러오기 완료: {Path(path).name}")
        except Exception as exc:
            _show_status(f"불러오기 실패: {exc}")

    def on_load(_: ft.ControlEvent) -> None:
        load_picker.pick_files(
            dialog_title="템플릿 불러오기",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["json"],
            allow_multiple=False,
        )

    def on_test_print(_: ft.ControlEvent) -> None:
        saved = _save_current_layout(show_message=False)
        if not saved:
            _show_status("테스트 출력 전 저장에 실패했습니다.")
            return
        try:
            print_test_receipt(saved, printer_service=printer_svc)
            _show_status("테스트 출력 완료")
        except Exception as exc:
            _show_status(f"테스트 출력 실패: {exc}")

    def on_test_preview(_: ft.ControlEvent) -> None:
        """현재 레이아웃으로 더미 주문 영수증 미리보기를 표시한다."""
        saved = _save_current_layout(show_message=False)
        if not saved:
            _show_status("미리보기 전 저장에 실패했습니다.")
            return
        try:
            from views.dashboard_flet_view import build_receipt_preview_dialog

            dummy_order = Order(
                order_number="TEST-ORDER-01",
                name="테스트 사용자",
                phone="010-2007-0831",
                seat="A-466\nB-467",
                goods=["티켓 상품 x2", "일반 상품 x1"],
            )
            preview_base64 = render_receipt_preview_base64(
                dummy_order,
                saved,
                template_path=_layout_path(),
            )[0][1]
            preview_items = [(_editor_layout_label(), preview_base64)]

            def _close_preview(_e: ft.ControlEvent) -> None:
                page.dialog.open = False
                page.update()

            page.dialog = build_receipt_preview_dialog(
                preview_items=preview_items, on_close=_close_preview,
            )
            page.dialog.open = True
            page.update()
        except Exception as exc:
            _show_status(f"미리보기 실패: {exc}")

    def _handle_scan_sound_files(files: list[ft.FilePickerFile]) -> None:
        if not files:
            return
        updated_rules = _get_scan_sound_rules()
        copied_names: list[str] = []
        for file in files:
            src_path = file.path
            if not src_path:
                ticket_settings_status_text.value = "알림음 선택 실패: 파일 경로를 읽을 수 없습니다."
                page.update()
                return
            try:
                copied_path = _copy_scan_sound_file_to_resources(src_path)
            except Exception as exc:
                ticket_settings_status_text.value = f"알림음 복사 실패: {exc}"
                page.update()
                return
            updated_rules.append(_build_scan_success_sound_rule(sound_path=copied_path))
            copied_names.append(Path(copied_path).name)

        if not copied_names:
            return

        _set_scan_sound_rules(_rebalance_scan_success_rules(updated_rules, mode="equal"))
        _set_selected_scan_sound_rule_index(len(updated_rules) - 1)
        saved = _save_settings_only(show_message=False)
        ticket_settings_status_text.value = (
            f"알림음 {len(copied_names)}개 추가 완료" if saved else "알림음 규칙 저장 실패"
        )
        _refresh_scan_sound_rule_controls()

    def on_pick_scan_sound(_: ft.ControlEvent) -> None:
        scan_sound_picker.pick_files(
            allow_multiple=True,
            dialog_title="QR 스캔 완료 알림음 추가",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["mp3", "wav", "m4a"],
        )

    def on_preview_scan_sound(_: ft.ControlEvent) -> None:
        selected_rule = _selected_scan_sound_rule()
        path = "" if selected_rule is None else (selected_rule.sound_path or "").strip()
        if not path:
            ticket_settings_status_text.value = "미리 들을 알림음을 먼저 선택하세요."
            page.update()
            return
        if audio_svc.play_file(path):
            ticket_settings_status_text.value = f"미리 듣기 재생: {Path(path).name}"
        else:
            ticket_settings_status_text.value = "알림음 재생 실패: 파일 또는 형식을 확인하세요."
        page.update()

    def on_open_scan_sound_path(_: ft.ControlEvent) -> None:
        path = (scan_sound_path_field.value or "").strip()
        if _open_path_in_explorer(path):
            ticket_settings_status_text.value = "파일 위치 열기 완료"
        else:
            ticket_settings_status_text.value = "유효한 음원 경로가 없습니다."
        page.update()

    def on_remove_scan_sound_rule(_: ft.ControlEvent) -> None:
        selected_index = _selected_scan_sound_rule_index()
        rules = _get_scan_sound_rules()
        if selected_index is None or selected_index < 0 or selected_index >= len(rules):
            ticket_settings_status_text.value = "삭제할 규칙을 먼저 선택하세요."
            page.update()
            return

        removed_rule = rules.pop(selected_index)
        if _scan_success_rule_is_general_pool_member(removed_rule):
            rules = _rebalance_scan_success_rules(rules, mode="equal")
        else:
            rules = _rebalance_scan_success_rules(rules, mode="normalize")
        _set_scan_sound_rules(rules)
        if not rules:
            _set_selected_scan_sound_rule_index(None)
        else:
            _set_selected_scan_sound_rule_index(min(selected_index, len(rules) - 1))
        saved = _save_settings_only(show_message=False)
        ticket_settings_status_text.value = "선택 규칙 삭제 완료" if saved else "선택 규칙 삭제 실패"
        _refresh_scan_sound_rule_controls()

    def on_clear_scan_sound(_: ft.ControlEvent) -> None:
        _set_scan_sound_rules([])
        _set_selected_scan_sound_rule_index(None)
        saved = _save_settings_only(show_message=False)
        ticket_settings_status_text.value = "스캔 사운드 규칙 초기화 완료" if saved else "스캔 사운드 규칙 초기화 실패"
        _refresh_scan_sound_rule_controls()

    def on_scan_sound_rule_trigger_type_change(_: ft.ControlEvent) -> None:
        scan_sound_rule_trigger_value_field.hint_text = _scan_success_trigger_value_hint(
            str(scan_sound_rule_trigger_type_dropdown.value or "always")
        )
        _save_selected_scan_sound_rule(rebalance_mode="equal")

    def on_scan_sound_rule_field_blur(_: ft.ControlEvent) -> None:
        selected_rule = _selected_scan_sound_rule()
        if selected_rule is None:
            return
        if str(scan_sound_rule_trigger_type_dropdown.value or "always") == "always" and bool(selected_rule.enabled):
            _save_selected_scan_sound_rule(rebalance_mode="edit")
            return
        _save_selected_scan_sound_rule()

    def on_scan_sound_rule_enabled_change(_: ft.ControlEvent) -> None:
        _save_selected_scan_sound_rule(rebalance_mode="equal")

    def on_scan_sound_rule_weight_change(_: ft.ControlEvent) -> None:
        selected_rule = _selected_scan_sound_rule()
        if scan_sound_rules_state.get("syncing") or selected_rule is None:
            return
        if not _can_live_apply_scan_success_weight(scan_sound_rule_weight_field.value):
            return
        if str(scan_sound_rule_trigger_type_dropdown.value or "always") == "always" and bool(selected_rule.enabled):
            _save_selected_scan_sound_rule(
                rebalance_mode="edit",
                preserve_weight_input=True,
            )
            return
        _save_selected_scan_sound_rule(preserve_weight_input=True)

    def on_paper_width_change(_: ft.ControlEvent) -> None:
        _set_paper_width(str(paper_width_dropdown.value or "80"))

    def on_dpi_change(_: ft.ControlEvent) -> None:
        """DPI 변경 (인쇄 해상도만 변경, 캔버스 레이아웃 유지)"""
        _show_status(f"인쇄 DPI: {_current_dpi()} (레이아웃 비율 유지)")
        page.update()

    def _handle_image_files(files: list[ft.FilePickerFile]) -> None:
        if not files:
            return
        path = files[0].path
        if not path:
            _show_status("이미지 추가 실패: 선택한 파일 경로를 읽을 수 없습니다.")
            return
        try:
            imported = canvas_store.import_image_asset(path)
            selected = _find_selected_element()
            if selected and selected.type == "image":
                _upsert_element(replace(selected, asset_path=imported))
            else:
                _add_element(_new_default_element("image", asset_path=imported))
            _refresh_all()
            _show_status("이미지를 자산 폴더로 가져왔습니다.")
        except Exception as exc:
            _show_status(f"이미지 추가 실패: {exc}")

    def _on_image_picker_result(event) -> None:
        _handle_image_files(_coerce_picker_files(event))

    def _on_save_as_picker_result(event) -> None:
        _save_portable_layout(_coerce_picker_path(event))

    def _on_load_picker_result(event) -> None:
        files = _coerce_picker_files(event)
        if not files:
            return
        _load_layout_from_file(files[0].path)

    def _on_scan_sound_picker_result(event) -> None:
        _handle_scan_sound_files(_coerce_picker_files(event))

    setattr(image_picker, "on_result", _on_image_picker_result)
    setattr(save_as_picker, "on_result", _on_save_as_picker_result)
    setattr(load_picker, "on_result", _on_load_picker_result)
    setattr(scan_sound_picker, "on_result", _on_scan_sound_picker_result)

    btn_add_text.on_click = on_add_text
    btn_add_image.on_click = on_add_image
    btn_add_divider.on_click = on_add_divider
    btn_delete.on_click = lambda _e: _remove_selected_element()
    btn_save.on_click = on_save
    btn_save_as.on_click = on_save_as
    btn_load.on_click = on_load
    btn_test_print.on_click = on_test_print
    btn_test_preview.on_click = on_test_preview
    btn_pick_scan_sound.on_click = on_pick_scan_sound
    btn_open_scan_sound_path.on_click = on_open_scan_sound_path
    btn_preview_scan_sound.on_click = on_preview_scan_sound
    btn_remove_scan_sound_rule.on_click = on_remove_scan_sound_rule
    btn_clear_scan_sound.on_click = on_clear_scan_sound
    scan_sound_rule_name_field.on_blur = on_scan_sound_rule_field_blur
    scan_sound_rule_name_field.on_submit = on_scan_sound_rule_field_blur
    scan_sound_rule_weight_field.on_change = on_scan_sound_rule_weight_change
    scan_sound_rule_weight_field.on_blur = on_scan_sound_rule_field_blur
    scan_sound_rule_weight_field.on_submit = on_scan_sound_rule_field_blur
    scan_sound_rule_trigger_value_field.on_blur = on_scan_sound_rule_field_blur
    scan_sound_rule_trigger_value_field.on_submit = on_scan_sound_rule_field_blur
    scan_sound_rule_trigger_type_dropdown.on_change = on_scan_sound_rule_trigger_type_change
    scan_sound_rule_enabled_switch.on_change = on_scan_sound_rule_enabled_change
    paper_width_dropdown.on_change = on_paper_width_change
    dpi_dropdown.on_change = on_dpi_change
    # 여백 필드 변경 시 캔버스 갱신 (실시간 + blur)
    def _on_margin_change(_e) -> None:
        _enforce_margin_boundaries()
        _refresh_all()

    margin_top_field.on_change = _on_margin_change
    margin_bottom_field.on_change = _on_margin_change
    margin_top_field.on_blur = lambda _e: (_enforce_margin_boundaries(), _refresh_all())
    margin_bottom_field.on_blur = lambda _e: (_enforce_margin_boundaries(), _refresh_all())

    field_chip_row = ft.Row(
        controls=[
            ft.OutlinedButton(
                label,
                height=30,
                on_click=lambda _e, key=field_key: _insert_binding(key),
            )
            for field_key, label in FIELD_BINDINGS
        ],
        wrap=True,
        spacing=6,
        run_spacing=6,
    )
    _refresh_scan_sound_rule_controls(push_update=False)
    # --- QR 코드 생성기 접이식 섹션 ---
    _qr_type_map = {
        "URL": QrType.URL,
        "Wi-Fi": QrType.WIFI,
        "안내문": QrType.TEXT,
    }
    qr_type_dropdown = ft.Dropdown(
        label="QR 유형",
        value="URL",
        options=[ft.dropdown.Option(k) for k in _qr_type_map],
        width=200,
        height=48,
        border_radius=8,
        content_padding=ft.padding.symmetric(horizontal=10, vertical=0),
    )
    # URL 입력
    qr_url_field = ft.TextField(label="URL", hint_text="https://example.com", border_radius=8)
    qr_url_container = ft.Container(content=qr_url_field, visible=True)
    # Wi-Fi 입력
    qr_wifi_ssid = ft.TextField(label="SSID (네트워크명)", border_radius=8)
    qr_wifi_pw = ft.TextField(label="비밀번호", password=True, can_reveal_password=True, border_radius=8)
    qr_wifi_enc = ft.Dropdown(
        label="암호화",
        value="WPA",
        options=[ft.dropdown.Option(v) for v in ("WPA", "WPA2", "WEP", "없음")],
        width=120,
        height=48,
        border_radius=8,
        content_padding=ft.padding.symmetric(horizontal=10, vertical=0),
    )
    qr_wifi_container = ft.Container(
        content=ft.Column([qr_wifi_ssid, qr_wifi_pw, qr_wifi_enc], spacing=6),
        visible=False,
    )
    # 안내문 입력
    qr_text_field = ft.TextField(label="안내문 내용", multiline=True, min_lines=2, max_lines=5, border_radius=8)
    qr_text_container = ft.Container(content=qr_text_field, visible=False)
    qr_status_text = ft.Text("", size=12, color="#444444")

    def _on_qr_type_changed(_: ft.ControlEvent) -> None:
        selected = qr_type_dropdown.value
        qr_url_container.visible = selected == "URL"
        qr_wifi_container.visible = selected == "Wi-Fi"
        qr_text_container.visible = selected == "안내문"
        qr_status_text.value = ""
        page.update()

    qr_type_dropdown.on_change = _on_qr_type_changed

    def _on_qr_insert_to_canvas(_: ft.ControlEvent) -> None:
        """QR 페이로드를 생성하여 캔버스에 QR 요소로 삽입."""
        selected_label = qr_type_dropdown.value or "URL"
        qr_type = _qr_type_map[selected_label]
        try:
            config = QrConfig(
                qr_type=qr_type,
                url=qr_url_field.value or "",
                ssid=qr_wifi_ssid.value or "",
                password=qr_wifi_pw.value or "",
                encryption=qr_wifi_enc.value or "WPA",
                text=qr_text_field.value or "",
            )
            payload = build_payload(config)
        except ValueError as exc:
            qr_status_text.value = str(exc)
            qr_status_text.color = "#DD4444"
            page.update()
            return

        # 캔버스에 QR 요소 생성 후 data_template에 페이로드 설정
        new_el = _new_default_element("qr")
        new_el = replace(new_el, data_template=payload)
        _add_element(new_el)
        _refresh_all()
        qr_status_text.value = "QR 요소가 캔버스에 삽입되었습니다."
        qr_status_text.color = ACCENT_PRIMARY_DARK
        page.update()

    qr_insert_btn = ft.ElevatedButton(
        "QR 생성하여 캔버스에 삽입",
        icon=ICONS.QR_CODE_2_ROUNDED,
        style=ft.ButtonStyle(
            bgcolor=ACCENT_PRIMARY,
            color="#FFFFFF",
            shape=ft.RoundedRectangleBorder(radius=8),
        ),
        on_click=_on_qr_insert_to_canvas,
    )

    qr_expansion_tile = ft.ExpansionTile(
        title=ft.Text("QR 코드 생성기", weight=ft.FontWeight.BOLD),
        leading=ft.Icon(ICONS.QR_CODE_2_ROUNDED, color=ACCENT_PRIMARY_DARK),
        initially_expanded=False,
        controls=[
            ft.Container(
                content=ft.Column(
                    controls=[
                        qr_type_dropdown,
                        qr_url_container,
                        qr_wifi_container,
                        qr_text_container,
                        qr_status_text,
                        qr_insert_btn,
                    ],
                    spacing=12,
                ),
                padding=ft.padding.only(left=8, right=8, top=8, bottom=16),
            ),
        ],
    )

    # --- 티켓 분류 설정 접이식 섹션 ---
    ticket_checkboxes: list[ft.Checkbox] = []
    ticket_checkbox_list = ft.ListView(spacing=6, padding=0, expand=True)

    def _load_ticket_checkboxes() -> None:
        """상품 목록을 로드하여 티켓 분류 체크박스를 생성한다."""
        ticket_checkboxes.clear()
        product_names = _load_excel_product_names()
        latest = settings_store.load()
        current_ticket_names = set(latest.ticket_product_names)

        def _on_ticket_check(_e: ft.ControlEvent) -> None:
            selected_names = [cb.label for cb in ticket_checkboxes if cb.value]
            settings.ticket_product_names = selected_names
            _save_settings_only(show_message=False)
            ticket_settings_status_text.value = "티켓 분류 설정 저장 완료"
            page.update()

        for name in product_names:
            cb = ft.Checkbox(
                label=name,
                value=name in current_ticket_names,
                on_change=_on_ticket_check,
            )
            ticket_checkboxes.append(cb)

        ticket_checkbox_list.controls = list(ticket_checkboxes) if ticket_checkboxes else [
            ft.Text("상품 컬럼이 없습니다.", size=12, color="#999999"),
        ]

    _load_ticket_checkboxes()

    btn_receipt_editor_tab = ft.TextButton("영수증", icon=ICONS.RECEIPT_LONG_ROUNDED)
    btn_product_editor_tab = ft.TextButton("상품 영수증", icon=ICONS.RECEIPT_LONG_ROUNDED)

    def _apply_editor_layout(push_update: bool = True) -> None:
        _apply_editor_layout_tab_styles(
            active_layout=_editor_layout_key(),
            receipt_tab_button=btn_receipt_editor_tab,
            product_tab_button=btn_product_editor_tab,
        )
        _reset_editor_layout_transient_state(
            set_selected_id=_set_selected_id,
            set_active_binding_target=_set_active_binding_target,
            state=state,
        )
        _sync_editor_layout_display(
            current_doc_paper_width=_doc().meta.paper_width,
            selected_paper_width=paper_width_dropdown.value,
            current_template_text=current_template_text,
            editor_layout_label_text=_editor_layout_label(),
            layout_path_text=_layout_path(),
            set_paper_width=_set_paper_width,
            refresh_all=_refresh_all,
        )

        if push_update:
            page.update()

    def _set_editor_layout(layout_key: str) -> None:
        editor_layout["value"] = "product" if layout_key == "product" else "receipt"
        _apply_editor_layout()

    left_controls_panel = _build_receipt_editor_left_controls_panel(
        printer_dropdown=printer_dropdown,
        paper_width_dropdown=paper_width_dropdown,
        dpi_dropdown=dpi_dropdown,
        margin_top_field=margin_top_field,
        margin_bottom_field=margin_bottom_field,
        btn_save=btn_save,
        btn_save_as=btn_save_as,
        btn_load=btn_load,
        btn_test_print=btn_test_print,
        btn_test_preview=btn_test_preview,
        current_template_text=current_template_text,
        btn_add_text=btn_add_text,
        btn_add_image=btn_add_image,
        btn_add_divider=btn_add_divider,
        btn_delete=btn_delete,
        field_chip_row=field_chip_row,
        status_text=status_text,
        qr_expansion_tile=qr_expansion_tile,
    )

    right_workspace = _build_receipt_editor_workspace(
        btn_receipt_editor_tab=btn_receipt_editor_tab,
        btn_product_editor_tab=btn_product_editor_tab,
        property_panel=property_panel,
        canvas_host=canvas_host,
    )

    main_split_layout = _build_receipt_editor_split_layout(
        left_controls_panel=left_controls_panel,
        right_workspace=right_workspace,
    )

    ticket_settings_panel = _build_receipt_ticket_settings_panel(
        scan_sound_path_field=scan_sound_path_field,
        btn_pick_scan_sound=btn_pick_scan_sound,
        btn_preview_scan_sound=btn_preview_scan_sound,
        btn_clear_scan_sound=btn_clear_scan_sound,
        scan_sound_rules_management_panel=scan_sound_rules_management_panel,
        ticket_settings_status_text=ticket_settings_status_text,
        ticket_checkbox_list=ticket_checkbox_list,
    )

    receipt_section_placeholder = _build_receipt_placeholder_panel(
        title_size=24,
        outer_border_radius=16,
        inner_border_color="#D9DDE5",
        subtitle_text="이 영역은 추후 설정 기능이 들어갈 공간입니다.",
        description_text="여기에 관련 설정 UI를 배치할 수 있습니다.",
        icon_size=42,
    )

    (
        settings_section,
        settings_content_host,
        btn_ticket_settings_section,
        btn_receipt_layout_section,
    ) = _build_receipt_settings_section_controls(
        initial_section=initial_section,
    )

    def _apply_settings_section(push_update: bool = True) -> None:
        _apply_settings_section_switch(
            active_section=settings_section["value"],
            ticket_button=btn_ticket_settings_section,
            receipt_button=btn_receipt_layout_section,
            content_host=settings_content_host,
            ticket_content=ticket_settings_panel,
            receipt_content=_select_receipt_settings_section_content(
                active_section=settings_section["value"],
                receipt_section_mode=receipt_section_mode,
                ticket_content=ticket_settings_panel,
                receipt_placeholder_content=receipt_section_placeholder,
                receipt_editor_content=main_split_layout,
            ),
        )
        if push_update:
            page.update()

    def _set_settings_section(section_key: str) -> None:
        settings_section["value"] = section_key
        _apply_settings_section()

    # 키보드 이벤트: Delete/Esc/화살표 이동/Ctrl+Z·Y (캔버스 포커스 시에만 동작)
    def _on_keyboard(e: ft.KeyboardEvent) -> None:
        if not state["canvas_focused"]:
            return

        key = str(getattr(e, "key", "") or "")
        ctrl = bool(getattr(e, "ctrl", False))
        shift = bool(getattr(e, "shift", False))

        # Undo/Redo
        if ctrl and key.upper() == "Z" and not shift:
            if _undo():
                _refresh_all()
                _show_status("되돌리기")
            return
        if ctrl and (key.upper() == "Y" or (key.upper() == "Z" and shift)):
            if _redo():
                _refresh_all()
                _show_status("다시 실행")
            return

        if key == "Escape":
            if _selected_id():
                _clear_canvas_selection(
                    set_selected_id=_set_selected_id,
                    set_active_binding_target=_set_active_binding_target,
                )
                _refresh_all()
            return

        if key == "Delete" and _selected_id():
            _remove_selected_element()
            return

        # 화살표 키 미세 이동 (Shift = 10px)
        nudge_map = {
            "Arrow Up": (0, -1),
            "ArrowUp": (0, -1),
            "Arrow Down": (0, 1),
            "ArrowDown": (0, 1),
            "Arrow Left": (-1, 0),
            "ArrowLeft": (-1, 0),
            "Arrow Right": (1, 0),
            "ArrowRight": (1, 0),
        }
        if key in nudge_map and _selected_id():
            selected = _find_selected_element()
            if selected is None:
                return
            step = 10 if shift else 1
            dx, dy = nudge_map[key]
            new_x = selected.x + dx * step
            new_y = selected.y + dy * step
            canvas_w = _real_canvas_width()
            canvas_h = _calc_real_canvas_height()
            new_x, new_y = clamp_element_position(
                x=new_x, y=new_y, w=selected.w, h=selected.h,
                canvas_w=canvas_w, canvas_h=canvas_h,
            )
            if new_x == selected.x and new_y == selected.y:
                return
            # 연속 화살표 입력은 0.5초 윈도우로 단일 undo 단위로 묶음
            now = time.monotonic()
            last_nudge = state.get("last_nudge_time")
            last_nudge_val = float(last_nudge) if isinstance(last_nudge, (int, float)) else 0.0
            if now - last_nudge_val > 0.5:
                _begin_undo_unit()
            state["last_nudge_time"] = now
            _upsert_element(replace(selected, x=new_x, y=new_y))
            _refresh_all()

    _wire_receipt_settings_navigation_handlers(
        page=page,
        bind_keyboard_events=bind_keyboard_events,
        keyboard_handler=_on_keyboard,
        ticket_section_button=btn_ticket_settings_section,
        receipt_section_button=btn_receipt_layout_section,
        receipt_editor_tab_button=btn_receipt_editor_tab,
        product_editor_tab_button=btn_product_editor_tab,
        set_settings_section=_set_settings_section,
        set_editor_layout=_set_editor_layout,
    )

    _initialize_receipt_settings_panel_state(
        current_doc_paper_width=_doc().meta.paper_width,
        selected_paper_width=paper_width_dropdown.value,
        current_template_text=current_template_text,
        editor_layout_label_text=_editor_layout_label(),
        layout_path_text=_layout_path(),
        set_paper_width=_set_paper_width,
        apply_editor_layout=_apply_editor_layout,
        apply_settings_section=_apply_settings_section,
    )

    panel = _build_receipt_settings_panel_shell(
        show_section_tabs=show_section_tabs,
        active_section=settings_section["value"],
        receipt_section_mode=receipt_section_mode,
        ticket_content=ticket_settings_panel,
        receipt_placeholder_content=receipt_section_placeholder,
        receipt_editor_content=main_split_layout,
        settings_content_host=settings_content_host,
        ticket_button=btn_ticket_settings_section,
        receipt_button=btn_receipt_layout_section,
        padding=12,
        spacing=12,
    )
    return _attach_ticket_product_reload_hook(panel, _load_ticket_checkboxes)


def build_receipt_sidebar_settings_panel(
    page: ft.Page,
    *,
    store_path: str = ".runtime/receipt_settings.json",
) -> ft.Control:
    settings_store = ReceiptSettingsStore(store_path)
    settings = settings_store.load()
    product_receipt_switch = ft.Switch(
        label="상품 영수증 추가 출력",
        value=bool(getattr(settings, "print_product_receipt", False)),
        **_switch_theme_kwargs(),
    )

    def _on_product_receipt_switch(_: ft.ControlEvent) -> None:
        latest = settings_store.load()
        latest.print_product_receipt = bool(product_receipt_switch.value)
        settings_store.save(latest)
        page.update()

    product_receipt_switch.on_change = _on_product_receipt_switch

    return _build_receipt_sidebar_output_panel(
        product_receipt_switch=product_receipt_switch,
    )


def build_app_settings_panel(
    page: ft.Page,
    *,
    store_path: str = ".runtime/receipt_settings.json",
    debug_store_path: str = ".runtime/ticket_debug_settings.json",
    audio_service: WindowsAudioService | None = None,
    on_apply_scanner_focus_settings: Callable[[str, float | None], str | None] | None = None,
    on_ticket_products_changed: Callable[[list[str]], None] | None = None,
    on_scan_sound_rules_changed: Callable[[], None] | None = None,
    show_section_tabs: bool = True,
    show_receipt_section: bool = True,
    camera_selector_row: ft.Control | None = None,
    focus_capability_badge: ft.Control | None = None,
    focus_section_title: str = "카메라 초점 설정",
    focus_description: str = "수동 초점 설정은 다음 앱 시작 후 적용됩니다.",
    show_title: bool = True,
) -> ft.Control:
    """Build lightweight app settings panel for the dashboard modal."""
    settings_store = ReceiptSettingsStore(store_path)
    debug_settings_store = TicketDebugSettingsStore(debug_store_path)
    debug_tools_service = TicketDebugToolsService(debug_settings_store)
    audio_svc = audio_service or WindowsAudioService()
    settings = settings_store.load()
    debug_settings = debug_tools_service.load_settings()

    sound_picker = ft.FilePicker()
    _attach_page_service(page, sound_picker)

    settings_status_text = ft.Text("변경 시 자동 저장됩니다.", size=12, color="#64748B")
    scan_sound_rule_name_field = ft.TextField(
        label="프로그램 표시 이름",
        value="",
        hint_text="예: 일본어 감사음",
        border_radius=10,
    )
    sound_path_field = ft.TextField(
        label="음원 파일 주소",
        value="",
        read_only=True,
        border_radius=10,
    )
    btn_open_sound_path = ft.IconButton(
        icon=ICONS.FOLDER_OPEN_ROUNDED,
        tooltip="파일 탐색기에서 위치 열기",
        icon_color="#2563EB",
        disabled=True,
    )
    focus_mode_dropdown = ft.Dropdown(
        label="초점 모드",
        value=settings.scanner_focus_mode,
        options=[
            ft.dropdown.Option(key="auto", text="자동 초점"),
            ft.dropdown.Option(key="manual", text="수동 초점"),
        ],
        border_radius=10,
    )
    manual_focus_value_field = ft.TextField(
        label="수동 초점 값",
        value="" if settings.scanner_manual_focus_value is None else str(settings.scanner_manual_focus_value),
        hint_text="예: 8.0",
        border_radius=10,
    )
    ticket_checkbox_list = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO)
    ticket_checkboxes: list[ft.Checkbox] = []

    btn_pick_sound = ft.ElevatedButton("음원 선택", icon=ICONS.AUDIO_FILE_ROUNDED)
    btn_preview_sound = ft.OutlinedButton("미리 듣기", icon=ICONS.PLAY_ARROW_ROUNDED)
    btn_remove_sound_rule = ft.OutlinedButton("선택 삭제", icon=ICONS.DELETE_OUTLINE_ROUNDED)
    btn_clear_sound = ft.OutlinedButton("초기화", icon=ICONS.DELETE_OUTLINE_ROUNDED)
    debug_count_scan_success_switch = ft.Switch(
        label="QR 스캔 성공 시 누적 카운트 반영",
        value=debug_settings.count_scan_success_as_processed,
        **_switch_theme_kwargs(),
    )
    debug_duplicate_sound_switch = ft.Switch(
        label="중복 스캔 시 효과음 재생",
        value=debug_settings.play_sound_for_duplicate_received_qr,
        **_switch_theme_kwargs(),
    )
    debug_offline_scan_switch = ft.Switch(
        label="오프라인 스캔 테스트 모드",
        value=debug_settings.offline_scan_mode,
        **_switch_theme_kwargs(),
    )
    debug_qr_order_input = ft.TextField(
        label="주문번호",
        hint_text="예: WFLM7QSDTC_69D53CU23685",
        expand=True,
        height=48,
    )
    btn_generate_qr = ft.ElevatedButton(text="QR 생성", height=48)
    debug_qr_image = ft.Image(visible=False, width=180, height=180, fit=ft.ImageFit.CONTAIN)
    debug_qr_status_text = ft.Text("", size=12, color="#64748B")
    debug_qr_section = ft.Container(
        bgcolor="#F0F7FF",
        border_radius=12,
        border=ft.border.all(1, "#B8D4F5"),
        padding=12,
        content=ft.Column(
            controls=[
                ft.Text("테스트 QR 코드 생성", size=14, weight=ft.FontWeight.W_600, color="#1E40AF"),
                ft.Text(
                    "오프라인 스캔 모드에서 사용할 테스트 QR 코드를 생성합니다. "
                    "data 파일에 있는 주문번호를 입력하면 카메라로 스캔 가능한 QR 이미지를 만들어드립니다.",
                    size=12,
                    color="#1E3A8A",
                ),
                ft.Row([debug_qr_order_input, btn_generate_qr], spacing=8),
                debug_qr_status_text,
                debug_qr_image,
            ],
            spacing=8,
        ),
    )
    debug_status_summary_text = ft.Text(size=12, color="#475569", selectable=True)
    scan_sound_rules_state: dict[str, object] = {
        "rules": _load_scan_success_sound_rules(settings),
        "selected_index": 0 if _load_scan_success_sound_rules(settings) else None,
        "syncing": False,
    }
    scan_sound_drag_group = "app-settings-scan-sound-rules"
    scan_sound_rule_summary_text = ft.Text(size=12, color="#64748B", selectable=True)
    scan_sound_rule_list = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO)
    scan_sound_rule_weight_field = ft.TextField(
        label="확률(%)",
        width=160,
        value="100",
        hint_text="예: 25 / 33.33",
    )
    scan_sound_rule_trigger_type_dropdown = ft.Dropdown(
        label="조건 타입",
        width=170,
        value="always",
        options=[ft.dropdown.Option(key=key, text=label) for key, label in SCAN_SOUND_TRIGGER_OPTIONS],
    )
    scan_sound_rule_trigger_value_field = ft.TextField(
        label="조건값",
        hint_text=_scan_success_trigger_value_hint("always"),
        expand=True,
    )
    scan_sound_rule_enabled_switch = ft.Switch(label="활성", value=True, **_switch_theme_kwargs())
    debug_tools_panel = _build_ticket_debug_tools_panel(
        debug_status_summary_text=debug_status_summary_text,
        debug_count_scan_success_switch=debug_count_scan_success_switch,
        debug_duplicate_sound_switch=debug_duplicate_sound_switch,
        debug_offline_scan_switch=debug_offline_scan_switch,
        debug_qr_section=debug_qr_section,
    )
    scan_sound_rules_management_panel = _build_scan_success_sound_management_panel(
        summary_text=scan_sound_rule_summary_text,
        sound_rule_list=scan_sound_rule_list,
        sound_rule_name_field=scan_sound_rule_name_field,
        sound_path_field=sound_path_field,
        btn_open_sound_path=btn_open_sound_path,
        sound_rule_weight_field=scan_sound_rule_weight_field,
        sound_rule_trigger_type_dropdown=scan_sound_rule_trigger_type_dropdown,
        sound_rule_trigger_value_field=scan_sound_rule_trigger_value_field,
        sound_rule_enabled_switch=scan_sound_rule_enabled_switch,
        btn_pick_sound=btn_pick_sound,
        btn_preview_sound=btn_preview_sound,
        btn_remove_sound_rule=btn_remove_sound_rule,
        btn_clear_sound_rules=btn_clear_sound,
        compact=True,
    )

    def _load_latest_settings() -> ReceiptSettings:
        return settings_store.load()

    def _load_latest_debug_settings() -> TicketDebugSettings:
        return debug_tools_service.load_settings()

    def _refresh_debug_settings_summary(*, push_update: bool = True) -> None:
        enabled_items: list[str] = []
        if bool(debug_count_scan_success_switch.value):
            enabled_items.append("QR 스캔 성공 시 누적 카운트 반영")
        if bool(debug_duplicate_sound_switch.value):
            enabled_items.append("중복 스캔 시 효과음 재생")
        if bool(debug_offline_scan_switch.value):
            enabled_items.append("오프라인 스캔 테스트 모드")

        if enabled_items:
            debug_status_summary_text.value = "현재 활성 디버그 기능: " + ", ".join(enabled_items)
        else:
            debug_status_summary_text.value = "현재 활성 디버그 기능: 없음"

        if push_update:
            page.update()

    def _selected_ticket_names() -> list[str]:
        return [str(cb.label) for cb in ticket_checkboxes if cb.value]

    def _get_scan_sound_rules() -> list[ScanSuccessSoundRule]:
        return list(scan_sound_rules_state["rules"])  # type: ignore[arg-type, return-value]

    def _set_scan_sound_rules(rules: list[ScanSuccessSoundRule]) -> None:
        scan_sound_rules_state["rules"] = list(rules)

    def _selected_scan_sound_rule_index() -> int | None:
        raw = scan_sound_rules_state.get("selected_index")
        return raw if isinstance(raw, int) else None

    def _set_selected_scan_sound_rule_index(index: int | None) -> None:
        scan_sound_rules_state["selected_index"] = index

    def _selected_scan_sound_rule() -> ScanSuccessSoundRule | None:
        rules = _get_scan_sound_rules()
        index = _selected_scan_sound_rule_index()
        if index is None or index < 0 or index >= len(rules):
            return None
        return rules[index]

    def _replace_selected_scan_sound_rule(rule: ScanSuccessSoundRule) -> None:
        rules = _get_scan_sound_rules()
        index = _selected_scan_sound_rule_index()
        if index is None or index < 0 or index >= len(rules):
            return
        rules[index] = rule
        _set_scan_sound_rules(rules)

    def _set_scan_sound_rule_enabled(rule_index: int, enabled: bool) -> None:
        rules = _get_scan_sound_rules()
        if rule_index < 0 or rule_index >= len(rules):
            return
        rules[rule_index] = replace(rules[rule_index], enabled=bool(enabled))
        rules = _rebalance_scan_success_rules(rules, mode="equal")
        _set_scan_sound_rules(rules)
        _save_modal_settings("음원 활성화 상태 저장 완료")
        _refresh_scan_sound_rule_controls()

    def _begin_edit_scan_sound_rule_name(rule_index: int) -> None:
        _set_selected_scan_sound_rule_index(rule_index)
        _refresh_scan_sound_rule_controls(push_update=False)
        focus_method = getattr(scan_sound_rule_name_field, "focus", None)
        if callable(focus_method):
            try:
                focus_method()
            except Exception:
                logger.debug("프로그램 표시 이름 필드 focus 실패", exc_info=True)
        page.update()

    def _reorder_scan_sound_rule(source_index: int, target_index: int) -> None:
        rules, selected_index = _reorder_scan_success_rules(
            _get_scan_sound_rules(),
            from_index=source_index,
            to_index=target_index,
            selected_index=_selected_scan_sound_rule_index(),
        )
        if source_index == target_index:
            return
        _set_scan_sound_rules(rules)
        _set_selected_scan_sound_rule_index(selected_index)
        _save_modal_settings("음원 순서 저장 완료")
        _refresh_scan_sound_rule_controls()

    def _refresh_scan_sound_rule_controls(
        *,
        push_update: bool = True,
        preserve_weight_input: bool = False,
    ) -> None:
        rules = _get_scan_sound_rules()
        selected_index = _selected_scan_sound_rule_index()
        if selected_index is not None and selected_index >= len(rules):
            selected_index = len(rules) - 1 if rules else None
            _set_selected_scan_sound_rule_index(selected_index)

        selected_rule = _selected_scan_sound_rule()
        scan_sound_rules_state["syncing"] = True
        try:
            if not rules:
                scan_sound_rule_summary_text.value = _scan_success_rule_pool_summary(rules)
                scan_sound_rule_list.controls = [
                    ft.Text("음원을 추가하면 기본 랜덤 음원이 만들어집니다.", size=12, color="#999999")
                ]
                scan_sound_rule_name_field.value = ""
                sound_path_field.value = ""
                scan_sound_rule_weight_field.value = format_scan_success_weight(100)
                scan_sound_rule_trigger_type_dropdown.value = "always"
                scan_sound_rule_trigger_value_field.value = ""
                scan_sound_rule_trigger_value_field.hint_text = _scan_success_trigger_value_hint("always")
                _set_scan_sound_editor_visibility(
                    trigger_type="always",
                    sound_rule_weight_field=scan_sound_rule_weight_field,
                    sound_rule_trigger_value_field=scan_sound_rule_trigger_value_field,
                )
                scan_sound_rule_enabled_switch.value = True
                scan_sound_rule_name_field.disabled = True
                scan_sound_rule_weight_field.disabled = True
                scan_sound_rule_trigger_type_dropdown.disabled = True
                scan_sound_rule_trigger_value_field.disabled = True
                btn_open_sound_path.disabled = True
                btn_preview_sound.disabled = True
                btn_remove_sound_rule.disabled = True
                btn_clear_sound.disabled = True
            else:
                scan_sound_rule_summary_text.value = _scan_success_rule_pool_summary(rules)
                scan_sound_rule_list.controls = [
                    _build_scan_success_sound_rule_card(
                        page=page,
                        rule=rule,
                        index=index,
                        selected_index=selected_index,
                        drag_group=scan_sound_drag_group,
                        on_select=lambda rule_index: (
                            _set_selected_scan_sound_rule_index(rule_index),
                            _refresh_scan_sound_rule_controls(),
                        ),
                        on_edit_name=_begin_edit_scan_sound_rule_name,
                        on_toggle_enabled=_set_scan_sound_rule_enabled,
                        on_reorder=_reorder_scan_sound_rule,
                    )
                    for index, rule in enumerate(rules)
                ]
                scan_sound_rule_name_field.value = _scan_success_rule_display_name(selected_rule) if selected_rule else ""
                sound_path_field.value = (selected_rule.sound_path or "").strip() if selected_rule else ""
                btn_open_sound_path.disabled = not bool(sound_path_field.value.strip())
                if not preserve_weight_input:
                    scan_sound_rule_weight_field.value = (
                        format_scan_success_weight(selected_rule.weight)
                        if selected_rule
                        else format_scan_success_weight(100)
                    )
                scan_sound_rule_trigger_type_dropdown.value = selected_rule.trigger_type if selected_rule else "always"
                scan_sound_rule_trigger_value_field.value = selected_rule.trigger_value if selected_rule else ""
                scan_sound_rule_trigger_value_field.hint_text = _scan_success_trigger_value_hint(
                    selected_rule.trigger_type if selected_rule else "always"
                )
                _set_scan_sound_editor_visibility(
                    trigger_type=selected_rule.trigger_type if selected_rule else "always",
                    sound_rule_weight_field=scan_sound_rule_weight_field,
                    sound_rule_trigger_value_field=scan_sound_rule_trigger_value_field,
                )
                scan_sound_rule_enabled_switch.value = bool(selected_rule.enabled) if selected_rule else True
                scan_sound_rule_name_field.disabled = False
                scan_sound_rule_weight_field.disabled = False
                scan_sound_rule_trigger_type_dropdown.disabled = False
                scan_sound_rule_trigger_value_field.disabled = False
                btn_preview_sound.disabled = False
                btn_remove_sound_rule.disabled = False
                btn_clear_sound.disabled = False
        finally:
            scan_sound_rules_state["syncing"] = False

        if push_update:
            page.update()

    def _save_selected_scan_sound_rule(
        message: str = "스캔 사운드 규칙 저장 완료",
        *,
        rebalance_mode: str = "normalize",
        preserve_weight_input: bool = False,
    ) -> None:
        if scan_sound_rules_state.get("syncing"):
            return
        selected_rule = _selected_scan_sound_rule()
        selected_index = _selected_scan_sound_rule_index()
        if selected_rule is None:
            return

        display_name = (scan_sound_rule_name_field.value or "").strip() or _scan_success_rule_display_name(selected_rule)
        current_trigger_type = str(scan_sound_rule_trigger_type_dropdown.value or "always")
        current_enabled = bool(selected_rule.enabled)
        updated_rule = replace(
            selected_rule,
            name=display_name,
            enabled=current_enabled,
            weight=coerce_scan_success_weight(scan_sound_rule_weight_field.value or "0", default=0.0),
            trigger_type=current_trigger_type,  # type: ignore[arg-type]
            trigger_value=(scan_sound_rule_trigger_value_field.value or "").strip(),
        )
        _replace_selected_scan_sound_rule(updated_rule)
        rules = _get_scan_sound_rules()
        if rebalance_mode == "edit" and selected_index is not None:
            rules = _rebalance_scan_success_rules(
                rules,
                mode="edit",
                edited_index=selected_index,
                edited_weight=updated_rule.weight,
            )
        else:
            rules = _rebalance_scan_success_rules(rules, mode=rebalance_mode)
        _set_scan_sound_rules(rules)
        _save_modal_settings(message)
        _refresh_scan_sound_rule_controls(preserve_weight_input=preserve_weight_input)

    def _sync_focus_field_state() -> None:
        manual_focus_value_field.disabled = (focus_mode_dropdown.value or "auto") != "manual"

    def _parse_manual_focus_value() -> float | None:
        raw = (manual_focus_value_field.value or "").strip()
        if not raw:
            return None
        return float(raw)

    def _save_modal_settings(message: str) -> ReceiptSettings:
        latest = _load_latest_settings()
        latest.ticket_product_names = _selected_ticket_names()
        latest.qr_scan_success_sound_rules = _rebalance_scan_success_rules(_get_scan_sound_rules(), mode="normalize")
        latest.qr_scan_success_sound_path = _primary_scan_success_sound_path(latest.qr_scan_success_sound_rules)
        parsed_manual_focus_value = _parse_manual_focus_value()
        requested_manual_focus = (focus_mode_dropdown.value or "auto") == "manual"
        latest.scanner_focus_mode = "manual" if requested_manual_focus and parsed_manual_focus_value is not None else "auto"
        latest.scanner_manual_focus_value = parsed_manual_focus_value if latest.scanner_focus_mode == "manual" else None
        settings_store.save(latest)
        settings_status_text.value = message
        if on_scan_sound_rules_changed is not None:
            try:
                on_scan_sound_rules_changed()
            except Exception:
                logger.warning("스캔 사운드 규칙 변경 콜백 실행 실패", exc_info=True)
        page.update()
        return latest

    def _save_debug_settings(message: str) -> TicketDebugSettings:
        latest_debug = _load_latest_debug_settings()
        latest_debug.count_scan_success_as_processed = bool(debug_count_scan_success_switch.value)
        latest_debug.play_sound_for_duplicate_received_qr = bool(debug_duplicate_sound_switch.value)
        latest_debug.offline_scan_mode = bool(debug_offline_scan_switch.value)
        debug_tools_service.save_settings(latest_debug)
        _refresh_debug_settings_summary(push_update=False)
        settings_status_text.value = message
        page.update()
        return latest_debug

    def on_debug_count_scan_success_change(_: ft.ControlEvent) -> None:
        _refresh_debug_settings_summary(push_update=False)
        _save_debug_settings("디버그 누적 카운트 반영 설정 저장 완료")

    def on_debug_duplicate_sound_change(_: ft.ControlEvent) -> None:
        _refresh_debug_settings_summary(push_update=False)
        _save_debug_settings("디버그 중복 스캔 효과음 설정 저장 완료")

    def on_debug_offline_scan_change(_: ft.ControlEvent) -> None:
        _refresh_debug_settings_summary(push_update=False)
        _save_debug_settings("오프라인 스캔 테스트 모드 설정 저장 완료")

    def _make_test_qr_base64(order_number: str) -> str:
        import base64
        import io

        import qrcode  # type: ignore[import-untyped]

        url = f"https://witchform.com/qrcode_link.php?test_order={order_number}"
        qr = qrcode.QRCode(box_size=6, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def on_generate_test_qr(_: ft.ControlEvent) -> None:
        order_num = (debug_qr_order_input.value or "").strip().upper()
        if not order_num:
            debug_qr_status_text.value = "주문번호를 입력하세요."
            debug_qr_image.visible = False
            page.update()
            return
        try:
            b64 = _make_test_qr_base64(order_num)
            debug_qr_image.src_base64 = b64
            debug_qr_image.visible = True
            debug_qr_status_text.value = f"생성 완료: {order_num}"
        except Exception as e:
            debug_qr_status_text.value = f"QR 생성 실패: {e}"
            debug_qr_image.visible = False
        page.update()

    def _save_and_apply_focus_settings() -> None:
        latest = _save_modal_settings("카메라 초점 설정 저장 완료 (다음 앱 시작 후 적용)")
        if on_apply_scanner_focus_settings is None:
            return
        runtime_message = on_apply_scanner_focus_settings(
            latest.scanner_focus_mode,
            latest.scanner_manual_focus_value,
        )
        if runtime_message:
            settings_status_text.value = runtime_message
            page.update()

    def _load_ticket_checkboxes() -> None:
        ticket_checkboxes.clear()
        latest = _load_latest_settings()
        current_ticket_names = set(latest.ticket_product_names)
        product_names = _load_excel_product_names()

        def _on_ticket_check(_e: ft.ControlEvent) -> None:
            latest = _save_modal_settings("티켓 분류 설정 저장 완료")
            if on_ticket_products_changed is not None:
                on_ticket_products_changed(list(latest.ticket_product_names))

        for name in product_names:
            cb = ft.Checkbox(label=name, value=name in current_ticket_names, on_change=_on_ticket_check)
            ticket_checkboxes.append(cb)

        ticket_checkbox_list.controls = list(ticket_checkboxes) if ticket_checkboxes else [
            ft.Text("상품 컬럼이 없습니다.", size=12, color="#999999"),
        ]

    _load_ticket_checkboxes()
    _sync_focus_field_state()
    _refresh_scan_sound_rule_controls(push_update=False)
    _refresh_debug_settings_summary(push_update=False)

    def _handle_sound_files(files: list[ft.FilePickerFile]) -> None:
        if not files:
            return
        updated_rules = _get_scan_sound_rules()
        copied_names: list[str] = []
        for file in files:
            path = file.path
            if not path:
                settings_status_text.value = "음원 선택 실패: 파일 경로를 읽을 수 없습니다."
                page.update()
                return
            try:
                copied_path = _copy_scan_sound_file_to_resources(path)
            except Exception as exc:
                settings_status_text.value = f"음원 복사 실패: {exc}"
                page.update()
                return
            updated_rules.append(_build_scan_success_sound_rule(sound_path=copied_path))
            copied_names.append(Path(copied_path).name)

        if not copied_names:
            return

        _set_scan_sound_rules(_rebalance_scan_success_rules(updated_rules, mode="equal"))
        _set_selected_scan_sound_rule_index(len(updated_rules) - 1)
        _save_modal_settings(f"음원 {len(copied_names)}개 추가 완료")
        _refresh_scan_sound_rule_controls()

    def on_pick_sound(_: ft.ControlEvent) -> None:
        sound_picker.pick_files(
            allow_multiple=True,
            dialog_title="QR 스캔 완료 음원 선택",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["mp3", "wav", "m4a"],
        )

    def on_preview_sound(_: ft.ControlEvent) -> None:
        selected_rule = _selected_scan_sound_rule()
        path = "" if selected_rule is None else (selected_rule.sound_path or "").strip()
        if not path:
            settings_status_text.value = "미리 들을 음원을 먼저 선택하세요."
            page.update()
            return
        if audio_svc.play_file(path):
            settings_status_text.value = f"미리 듣기 재생: {Path(path).name}"
        else:
            settings_status_text.value = "음원 재생 실패: 파일 또는 형식을 확인하세요."
        page.update()

    def on_remove_sound_rule(_: ft.ControlEvent) -> None:
        selected_index = _selected_scan_sound_rule_index()
        rules = _get_scan_sound_rules()
        if selected_index is None or selected_index < 0 or selected_index >= len(rules):
            settings_status_text.value = "삭제할 규칙을 먼저 선택하세요."
            page.update()
            return

        removed_rule = rules.pop(selected_index)
        if _scan_success_rule_is_general_pool_member(removed_rule):
            rules = _rebalance_scan_success_rules(rules, mode="equal")
        else:
            rules = _rebalance_scan_success_rules(rules, mode="normalize")
        _set_scan_sound_rules(rules)
        if not rules:
            _set_selected_scan_sound_rule_index(None)
        else:
            _set_selected_scan_sound_rule_index(min(selected_index, len(rules) - 1))
        _save_modal_settings("선택 규칙 삭제 완료")
        _refresh_scan_sound_rule_controls()

    def on_clear_sound(_: ft.ControlEvent) -> None:
        _set_scan_sound_rules([])
        _set_selected_scan_sound_rule_index(None)
        _save_modal_settings("음원 규칙 초기화 완료")
        _refresh_scan_sound_rule_controls()

    def on_open_sound_path(_: ft.ControlEvent) -> None:
        path = (sound_path_field.value or "").strip()
        if _open_path_in_explorer(path):
            settings_status_text.value = "파일 위치 열기 완료"
        else:
            settings_status_text.value = "유효한 음원 경로가 없습니다."
        page.update()

    def on_scan_sound_rule_trigger_type_change(_: ft.ControlEvent) -> None:
        scan_sound_rule_trigger_value_field.hint_text = _scan_success_trigger_value_hint(
            str(scan_sound_rule_trigger_type_dropdown.value or "always")
        )
        _save_selected_scan_sound_rule(rebalance_mode="equal")

    def on_scan_sound_rule_field_blur(_: ft.ControlEvent) -> None:
        selected_rule = _selected_scan_sound_rule()
        if selected_rule is None:
            return
        if str(scan_sound_rule_trigger_type_dropdown.value or "always") == "always" and bool(selected_rule.enabled):
            _save_selected_scan_sound_rule(rebalance_mode="edit")
            return
        _save_selected_scan_sound_rule()

    def on_scan_sound_rule_enabled_change(_: ft.ControlEvent) -> None:
        _save_selected_scan_sound_rule(rebalance_mode="equal")

    def on_scan_sound_rule_weight_change(_: ft.ControlEvent) -> None:
        selected_rule = _selected_scan_sound_rule()
        if scan_sound_rules_state.get("syncing") or selected_rule is None:
            return
        if not _can_live_apply_scan_success_weight(scan_sound_rule_weight_field.value):
            return
        if str(scan_sound_rule_trigger_type_dropdown.value or "always") == "always" and bool(selected_rule.enabled):
            _save_selected_scan_sound_rule(
                rebalance_mode="edit",
                preserve_weight_input=True,
            )
            return
        _save_selected_scan_sound_rule(preserve_weight_input=True)

    def on_focus_mode_change(_: ft.ControlEvent) -> None:
        _sync_focus_field_state()
        try:
            _save_and_apply_focus_settings()
        except ValueError:
            settings_status_text.value = "초점 값은 숫자로 입력하세요."
            page.update()

    def on_manual_focus_value_blur(_: ft.ControlEvent) -> None:
        try:
            _save_and_apply_focus_settings()
        except ValueError:
            settings_status_text.value = "초점 값은 숫자로 입력하세요."
            page.update()

    def on_manual_focus_value_submit(_: ft.ControlEvent) -> None:
        try:
            _save_and_apply_focus_settings()
        except ValueError:
            settings_status_text.value = "초점 값은 숫자로 입력하세요."
            page.update()

    def _on_sound_picker_result(event) -> None:
        _handle_sound_files(_coerce_picker_files(event))

    setattr(sound_picker, "on_result", _on_sound_picker_result)

    btn_pick_sound.on_click = on_pick_sound
    btn_open_sound_path.on_click = on_open_sound_path
    btn_preview_sound.on_click = on_preview_sound
    btn_remove_sound_rule.on_click = on_remove_sound_rule
    btn_clear_sound.on_click = on_clear_sound
    scan_sound_rule_name_field.on_blur = on_scan_sound_rule_field_blur
    scan_sound_rule_name_field.on_submit = on_scan_sound_rule_field_blur
    scan_sound_rule_weight_field.on_change = on_scan_sound_rule_weight_change
    scan_sound_rule_weight_field.on_blur = on_scan_sound_rule_field_blur
    scan_sound_rule_weight_field.on_submit = on_scan_sound_rule_field_blur
    scan_sound_rule_trigger_value_field.on_blur = on_scan_sound_rule_field_blur
    scan_sound_rule_trigger_value_field.on_submit = on_scan_sound_rule_field_blur
    scan_sound_rule_trigger_type_dropdown.on_change = on_scan_sound_rule_trigger_type_change
    scan_sound_rule_enabled_switch.on_change = on_scan_sound_rule_enabled_change
    focus_mode_dropdown.on_change = on_focus_mode_change
    manual_focus_value_field.on_blur = on_manual_focus_value_blur
    manual_focus_value_field.on_submit = on_manual_focus_value_submit
    debug_count_scan_success_switch.on_change = on_debug_count_scan_success_change
    debug_duplicate_sound_switch.on_change = on_debug_duplicate_sound_change
    debug_offline_scan_switch.on_change = on_debug_offline_scan_change
    btn_generate_qr.on_click = on_generate_test_qr

    ticket_settings_panel = _build_app_settings_ticket_panel(
        sound_path_field=sound_path_field,
        btn_pick_sound=btn_pick_sound,
        btn_preview_sound=btn_preview_sound,
        btn_clear_sound=btn_clear_sound,
        sound_rules_management_panel=scan_sound_rules_management_panel,
        debug_tools_panel=debug_tools_panel,
        camera_selector_row=camera_selector_row,
        focus_mode_dropdown=focus_mode_dropdown,
        manual_focus_value_field=manual_focus_value_field,
        focus_capability_badge=focus_capability_badge,
        focus_section_title=focus_section_title,
        focus_description=focus_description,
        settings_status_text=settings_status_text,
        ticket_checkbox_list=ticket_checkbox_list,
        show_title=show_title,
    )

    if not show_section_tabs and not show_receipt_section:
        return _attach_ticket_product_reload_hook(ticket_settings_panel, _load_ticket_checkboxes)

    receipt_placeholder_panel = _build_receipt_placeholder_panel(
        title_size=26,
        outer_border_radius=18,
        inner_border_color="#D8E2F0",
        description_text="현재는 공간만 준비되어 있습니다.",
        icon_size=44,
    )

    section = {"value": "ticket"}
    section_host = ft.Container(expand=True)
    btn_ticket_section = ft.TextButton("티켓 확인 설정", icon=ICONS.CONFIRMATION_NUMBER_ROUNDED)
    btn_receipt_section = ft.TextButton("영수증 양식 설정", icon=ICONS.RECEIPT_LONG_ROUNDED)

    def _apply_section(push_update: bool = True) -> None:
        _apply_settings_section_switch(
            active_section=section["value"],
            ticket_button=btn_ticket_section,
            receipt_button=btn_receipt_section,
            content_host=section_host,
            ticket_content=ticket_settings_panel,
            receipt_content=receipt_placeholder_panel,
        )
        if push_update:
            page.update()

    btn_ticket_section.on_click = lambda _e: (section.__setitem__("value", "ticket"), _apply_section())
    btn_receipt_section.on_click = lambda _e: (section.__setitem__("value", "receipt"), _apply_section())
    _apply_section(push_update=False)

    if not show_section_tabs:
        return _attach_ticket_product_reload_hook(section_host, _load_ticket_checkboxes)

    panel = _build_settings_section_shell(
        ticket_button=btn_ticket_section,
        receipt_button=btn_receipt_section,
        content_host=section_host,
        padding=8,
        spacing=14,
    )
    return _attach_ticket_product_reload_hook(panel, _load_ticket_checkboxes)


class SettingsFletView:
    """Standalone settings window (compat mode)."""

    def __init__(self, store_path: str = ".runtime/receipt_settings.json"):
        self._store_path = store_path

    def run(self) -> None:
        ft.app(target=self._build_page)

    def _build_page(self, page: ft.Page) -> None:
        page.title = "Receipt Settings"
        page.window.width = 1480
        page.window.height = 940
        page.scroll = ft.ScrollMode.AUTO
        page.bgcolor = "#ECECEC"
        page.add(build_receipt_settings_panel(page, store_path=self._store_path))


def run_settings_app() -> None:
    SettingsFletView().run()


if __name__ == "__main__":
    run_settings_app()
