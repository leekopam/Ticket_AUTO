
"""Flet receipt settings panel with drag-and-drop canvas editor."""
from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

# Add project root to sys.path so direct execution works
sys.path.append(str(Path(__file__).parent.parent))

import flet as ft

from models.receipt_canvas_model import (
    ReceiptCanvasDocument,
    ReceiptCanvasElement,
    create_default_document,
    make_element_id,
    paper_width_to_px,
)
from models.receipt_settings_model import ReceiptSettings
from services.receipt_canvas_editor_state import (
    clamp_element_position,
    preview_to_real,
    real_to_preview,
    remove_element_by_id,
    update_element_in_list,
)
from services.receipt_canvas_store import ReceiptCanvasStore
from services.receipt_print_pipeline import print_test_receipt
from services.receipt_settings_store import ReceiptSettingsStore
from services.windows_printer_service import WindowsPrinterService


ICONS = getattr(ft, "Icons", ft.icons)
FIELD_BINDINGS = [
    ("order_number", "주문번호"),
    ("buyer_name", "주문자명"),
    ("buyer_phone", "연락처"),
    ("seat", "좌석번호"),
    ("goods_lines", "상품목록"),
]


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
        return ft.alignment.center
    if align == "right":
        return ft.alignment.center_right
    return ft.alignment.center_left


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


def build_receipt_settings_panel(
    page: ft.Page,
    *,
    store_path: str = ".runtime/receipt_settings.json",
    printer_service: WindowsPrinterService | None = None,
) -> ft.Control:
    """Build reusable settings panel control."""
    settings_store = ReceiptSettingsStore(store_path)
    canvas_store = ReceiptCanvasStore()
    printer_svc = printer_service or WindowsPrinterService()
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

    selected_printer = settings.printer_name
    if selected_printer not in printers:
        selected_printer = default_printer if default_printer in printers else (printers[0] if printers else "")

    layout_path = settings.template_path.strip() or "templates/receipt_layout.json"
    if not layout_path.lower().endswith(".json"):
        layout_path = "templates/receipt_layout.json"

    try:
        document = canvas_store.load_layout(layout_path)
    except Exception:
        document = create_default_document(settings.paper_width)

    state: dict[str, object] = {
        "doc": document,
        "selected_id": None,
        "active_binding_target": None,
        "layout_path": layout_path,
        "canvas_focused": False,
        "drag_start_x": 0,
        "drag_start_y": 0,
        "drag_accum_dx": 0.0,
        "drag_accum_dy": 0.0,
        "drag_bottom_start_y": None,    # 드래그 시작 시 고정한 하단 여백 앵커 Y
        "resize_start_x": 0,
        "resize_start_y": 0,
        "resize_start_w": 0,
        "resize_start_h": 0,
        "resize_accum_dx": 0.0,
        "resize_accum_dy": 0.0,
        "resize_bottom_start_y": None,  # 리사이즈 시작 시 고정한 하단 여백 앵커 Y
        "canvas_scroll_ctrl": None,
        "scroll_gutter_ctrl": None,
        "canvas_stack_ctrl": None,
        "canvas_meta_text_ctrl": None,
        "canvas_frame_ctrl": None,
        "canvas_frame_body_ctrl": None,
        "snap_guides": [],
        "inline_edit_id": None,
        "selected_ids": set(),          # 다중 선택 ID 집합
        "multi_drag_origins": {},       # {id: (x, y)} 다중 드래그 원본 좌표
        "ctrl_pressed": False,          # Ctrl 키 눌림 상태
        "element_detectors": {},        # {id: detector} 다중 드래그 시 위치 갱신용
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
        value=f"활성 템플릿: {state['layout_path']}",
        color="#5F5F5F",
        size=12,
        selectable=True,
    )

    btn_add_text = ft.ElevatedButton("텍스트 추가", icon=ICONS.TEXT_FIELDS_ROUNDED)
    btn_add_image = ft.ElevatedButton("이미지 추가", icon=ICONS.IMAGE_ROUNDED)
    btn_add_qr = ft.ElevatedButton("QR 추가", icon=ICONS.QR_CODE_2_ROUNDED)
    btn_add_divider = ft.ElevatedButton("구분선 추가", icon=ICONS.HORIZONTAL_RULE_ROUNDED)
    btn_delete = ft.OutlinedButton("삭제", icon=ICONS.DELETE_OUTLINE_ROUNDED)
    btn_save = ft.ElevatedButton("저장", icon=ICONS.SAVE_ROUNDED)
    btn_save_as = ft.ElevatedButton("다른이름으로 저장", icon=ICONS.SAVE_AS_ROUNDED)
    btn_load = ft.ElevatedButton("불러오기", icon=ICONS.FOLDER_OPEN_ROUNDED)
    btn_test_print = ft.ElevatedButton("테스트 출력", icon=ICONS.PRINT_ROUNDED)

    canvas_host = ft.Container(expand=True)
    property_panel = ft.Container(bgcolor="#FFFFFF", border_radius=8, padding=12)

    image_picker = ft.FilePicker()
    save_as_picker = ft.FilePicker()
    load_picker = ft.FilePicker()
    page.overlay.append(image_picker)
    page.overlay.append(save_as_picker)
    page.overlay.append(load_picker)

    def _doc() -> ReceiptCanvasDocument:
        return state["doc"]  # type: ignore[return-value]

    def _set_doc(doc: ReceiptCanvasDocument) -> None:
        state["doc"] = doc

    def _selected_id() -> str | None:
        return state["selected_id"]  # type: ignore[return-value]

    def _set_selected_id(value: str | None) -> None:
        # 선택 요소 변경 시 인라인 편집 종료
        if state["selected_id"] != value:
            state["inline_edit_id"] = None
        state["selected_id"] = value
        # 단일 선택 시 selected_ids 동기화
        state["selected_ids"] = {value} if value else set()

    def _selected_ids() -> set[str]:
        return state["selected_ids"]  # type: ignore[return-value]

    def _is_multi_selected() -> bool:
        return len(state["selected_ids"]) >= 2  # type: ignore[arg-type]

    def _toggle_selection(element_id: str) -> None:
        """Ctrl+클릭 시 선택 토글"""
        ids: set[str] = state["selected_ids"]  # type: ignore[assignment]
        if element_id in ids:
            ids.discard(element_id)
            # primary 선택도 갱신
            state["selected_id"] = next(iter(ids), None) if ids else None
        else:
            ids.add(element_id)
            state["selected_id"] = element_id
        state["inline_edit_id"] = None

    def _clear_multi_selection() -> None:
        state["selected_ids"] = set()
        state["element_detectors"] = {}

    def _active_binding_target() -> str | None:
        return state["active_binding_target"]  # type: ignore[return-value]

    def _set_active_binding_target(value: str | None) -> None:
        state["active_binding_target"] = value

    def _layout_path() -> str:
        return state["layout_path"]  # type: ignore[return-value]

    def _set_canvas_focus(focused: bool) -> None:
        state["canvas_focused"] = focused

    def _set_layout_path(path: str) -> None:
        state["layout_path"] = path
        current_template_text.value = f"활성 템플릿: {path}"

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
            if time.monotonic() - float(state["last_element_tap_time"]) < 0.05:
                return
            _set_selected_id(None)
            _clear_multi_selection()
            _set_active_binding_target(None)
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
        )
        scrollable_canvas = ft.Column(
            controls=[canvas_with_gutter],
            height=viewport_h,
            scroll=ft.ScrollMode.AUTO if needs_scroll else None,
        )
        canvas_meta_text = ft.Text(color="#666666", size=12)

        canvas_host.content = ft.Container(
            alignment=ft.alignment.top_center,
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
        """요소 간 겹침 해소: Y 순서대로 아래 요소를 밀어냄 (gap=4px)"""
        elements = list(_doc().elements)
        if len(elements) < 2:
            return
        # Y 기준 정렬된 인덱스
        order = sorted(range(len(elements)), key=lambda i: (elements[i].y, elements[i].x))
        gap = 4
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
        _set_doc(replace(_doc(), elements=elements))
        _resolve_overlaps_sticky()

    def _resolve_overlaps_sticky() -> None:
        """리사이즈 후 스티키 정렬: 위로 당김 + 아래로 밀기 (2-패스)"""
        elements = list(_doc().elements)
        if len(elements) < 2:
            return
        gap = 4
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
                # 위에 X겹침 요소가 없으면 상단 여백 아래로 당김
                mt = _margin_top_px()
                if mt > 0 and cur.y > mt + gap:
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

    def _add_element(element: ReceiptCanvasElement) -> None:
        _set_doc(replace(_doc(), elements=[*_doc().elements, element]))
        _resolve_overlaps()
        _set_selected_id(element.id)

    def _remove_selected_element() -> None:
        ids = _selected_ids()
        if not ids:
            selected = _selected_id()
            if not selected:
                _show_status("삭제할 요소를 먼저 선택하세요.")
                return
            ids = {selected}
        # 다중/단일 삭제 통합
        elements = _doc().elements
        for eid in ids:
            elements = remove_element_by_id(elements, eid)
        _set_doc(replace(_doc(), elements=elements))
        _set_selected_id(None)
        _clear_multi_selection()
        _set_active_binding_target(None)
        _refresh_all()
        count = len(ids)
        _show_status(f"{count}개 요소를 삭제했습니다." if count > 1 else "선택 요소를 삭제했습니다.")

    def _apply_align_to_selected(align: str) -> None:
        element = _find_selected_element()
        if not element:
            _show_status("정렬할 요소를 먼저 선택하세요.")
            return

        updated = replace(element, align=align)
        _upsert_element(updated)
        _refresh_all()

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
            data_template="{{order_number}}|{{url}}",
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
        token = f"{{{{{field_key}}}}}"
        label = next((lbl for fk, lbl in FIELD_BINDINGS if fk == field_key), field_key)
        selected = _find_selected_element()

        # 선택된 텍스트/QR 요소가 있으면 해당 요소에 필드 추가
        if selected and selected.type in ("text", "qr"):
            target = _active_binding_target()
            if target is None:
                target = "data_template" if selected.type == "qr" else "text_template"
            insert_text = f"{label}: {token}"
            if target == "data_template" and selected.type == "qr":
                updated = replace(selected, data_template=f"{selected.data_template}{insert_text}")
            else:
                updated = replace(selected, text_template=f"{selected.text_template}{insert_text}")
            _upsert_element(updated)
            _refresh_all()
            return

        # 선택된 요소가 없으면 새 텍스트 요소 생성
        new_el = _new_default_element("text")
        new_el = replace(new_el, text_template=f"{label}: {token}")
        _add_element(new_el)
        _set_active_binding_target("text_template")
        _refresh_all()
    def _set_paper_width(new_width: str) -> None:
        if new_width not in {"58", "80"}:
            return

        doc = _doc()
        old_real_width = max(1, int(doc.meta.canvas_width_px))
        new_real_width = paper_width_to_px(new_width)
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
        _refresh_all()

    def _save_current_layout(*, show_message: bool = True) -> ReceiptSettings | None:
        try:
            target_layout_path = _layout_path()
            if not target_layout_path.lower().endswith(".json"):
                target_layout_path = "templates/receipt_layout.json"
            _set_layout_path(target_layout_path)

            current_doc = _doc()
            paper_width = "58" if str(paper_width_dropdown.value) == "58" else "80"
            current_doc = replace(
                current_doc,
                meta=replace(
                    current_doc.meta,
                    paper_width=paper_width,
                    canvas_width_px=paper_width_to_px(paper_width),
                ),
            )
            _set_doc(current_doc)

            canvas_store.save_layout(target_layout_path, current_doc)

            settings_obj = ReceiptSettings(
                printer_name=(printer_dropdown.value or "").strip(),
                paper_width=paper_width,  # type: ignore[arg-type]
                show_qr=settings.show_qr,
                show_logo=settings.show_logo,
                template_path=target_layout_path,
                logo_path=settings.logo_path,
                event_title=settings.event_title,
                qr_payload_template=settings.qr_payload_template,
                margin_top=max(0, _coerce_int(margin_top_field.value, 0)),
                margin_bottom=max(0, _coerce_int(margin_bottom_field.value, 0)),
            )
            settings_store.save(settings_obj)
            if show_message:
                _show_status("레이아웃과 설정 저장 완료")
            return settings_obj
        except Exception as exc:
            if show_message:
                _show_status(f"저장 실패: {exc}")
            return None

    def _refresh_property_panel() -> None:
        # 다중 선택 시 요약 표시
        if _is_multi_selected():
            count = len(_selected_ids())
            property_panel.content = ft.Column(
                controls=[
                    ft.Text("속성", size=20, weight=ft.FontWeight.BOLD),
                    ft.Text(f"{count}개 요소 선택됨", size=14, color="#333333"),
                    ft.Text("개별 속성 편집은 단일 선택 시 가능합니다.", size=12, color="#666666"),
                ],
                spacing=8,
            )
            return

        selected = _find_selected_element()
        if not selected:
            property_panel.content = ft.Column(
                controls=[
                    ft.Text("속성", size=20, weight=ft.FontWeight.BOLD),
                    ft.Text("캔버스에서 요소를 선택하세요.", color="#666666"),
                ],
                spacing=8,
            )
            return

        def commit_common(_: ft.ControlEvent | None = None) -> None:
            current = _find_selected_element()
            if not current:
                return
            updated = _update_common_dimensions(
                current,
                x=_coerce_int(x_field.value, current.x),
                y=_coerce_int(y_field.value, current.y),
                w=_coerce_int(w_field.value, current.w, minimum=10),
                h=_coerce_int(h_field.value, current.h, minimum=10),
                align=current.align,
            )
            _upsert_element(updated)
            _refresh_all()

        def commit_text(_: ft.ControlEvent | None = None) -> None:
            current = _find_selected_element()
            if not current or current.type != "text":
                return
            updated = replace(
                current,
                text_template=text_template_field.value or "",
                font_size=_coerce_int(font_size_field.value, current.font_size, minimum=8),
                bold=bool(bold_switch.value),
            )
            _upsert_element(updated)
            _auto_fit_to_content(updated.id)
            _refresh_canvas()
            _refresh_property_panel()
            page.update()

        def commit_image(_: ft.ControlEvent | None = None) -> None:
            current = _find_selected_element()
            if not current or current.type != "image":
                return
            updated = replace(
                current,
                asset_path=image_path_field.value or "",
                preserve_ratio=bool(preserve_ratio_switch.value),
            )
            _upsert_element(updated)
            _refresh_canvas()
            page.update()

        def commit_qr(_: ft.ControlEvent | None = None) -> None:
            current = _find_selected_element()
            if not current or current.type != "qr":
                return
            updated = replace(
                current,
                data_template=qr_data_field.value or "",
                box_size=_coerce_int(box_size_field.value, current.box_size, minimum=1),
            )
            _upsert_element(updated)
            _refresh_canvas()
            page.update()

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

        controls: list[ft.Control] = [
            ft.Text("속성", size=20, weight=ft.FontWeight.BOLD),
            ft.Text(f"요소 ID: {selected.id}", size=12, color="#666666", selectable=True),
            ft.Row(controls=[x_field, y_field, w_field, h_field], spacing=8),
            ft.Divider(),
        ]

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
            font_size_field = ft.TextField(
                label="font_size",
                value=str(selected.font_size),
                width=120,
                on_blur=commit_text,
                on_focus=_on_field_focus,
            )
            bold_switch = ft.Switch(label="bold", value=selected.bold, on_change=commit_text)
            controls.extend([
                text_template_field,
                ft.Row([font_size_field, bold_switch, align_left_btn, align_center_btn, align_right_btn], spacing=8),
            ])
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
                label="preserve_ratio",
                value=selected.preserve_ratio,
                on_change=commit_image,
            )
            controls.extend(
                [
                    image_path_field,
                    ft.Row(
                        controls=[
                            ft.ElevatedButton("이미지 교체", icon=ICONS.IMAGE_SEARCH_ROUNDED, on_click=pick_image_for_selected),
                            preserve_ratio_switch,
                        ],
                        spacing=10,
                    ),
                ]
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
                label="box_size",
                value=str(selected.box_size),
                width=120,
                on_blur=commit_qr,
                on_focus=_on_field_focus,
            )
            controls.extend([qr_data_field, box_size_field])
        elif selected.type == "divider":
            def commit_divider(_: ft.ControlEvent | None = None) -> None:
                current = _find_selected_element()
                if not current or current.type != "divider":
                    return
                updated = replace(
                    current,
                    line_style=line_style_dropdown.value or "solid",
                    line_thickness=_coerce_int(line_thickness_field.value, current.line_thickness, minimum=1),
                    text_template=divider_text_field.value or "",
                    font_size=_coerce_int(div_font_size_field.value, current.font_size, minimum=8),
                    bold=bool(div_bold_switch.value),
                )
                _upsert_element(updated)
                _refresh_canvas()
                page.update()

            line_style_dropdown = ft.Dropdown(
                label="선 스타일",
                width=160,
                value=selected.line_style,
                options=[
                    ft.dropdown.Option("solid", "실선"),
                    ft.dropdown.Option("dashed", "파선"),
                    ft.dropdown.Option("dotted", "점선"),
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
            div_font_size_field = ft.TextField(
                label="font_size",
                value=str(selected.font_size),
                width=120,
                on_blur=commit_divider,
                on_focus=_on_field_focus,
            )
            div_bold_switch = ft.Switch(label="bold", value=selected.bold, on_change=commit_divider)
            controls.extend([
                ft.Row([line_style_dropdown, line_thickness_field], spacing=10),
                divider_text_field,
                ft.Row([div_font_size_field, div_bold_switch], spacing=10),
            ])

        property_panel.content = ft.Column(controls=controls, spacing=8, scroll=ft.ScrollMode.AUTO)

    def _start_resize(element_id: str, _: ft.DragStartEvent) -> None:
        """리사이즈 드래그 시작 시 원본 치수 저장"""
        current = next((item for item in _doc().elements if item.id == element_id), None)
        if current:
            state["resize_start_x"] = current.x
            state["resize_start_y"] = current.y
            state["resize_start_w"] = current.w
            state["resize_start_h"] = current.h
            state["resize_bottom_start_y"] = _fixed_bottom_anchor_y(moving_ids={element_id})
        state["resize_accum_dx"] = 0.0
        state["resize_accum_dy"] = 0.0

    def _handle_resize(element_id: str, corner: str, e: ft.DragUpdateEvent) -> None:
        """리사이즈 핸들 드래그 처리 (누적 delta로 부드러운 변환)"""
        current = next((item for item in _doc().elements if item.id == element_id), None)
        if current is None:
            return
        real_w = _real_canvas_width()
        preview_w = _preview_canvas_width()

        # 프리뷰 delta를 float로 누적
        accum_dx = float(state["resize_accum_dx"]) + e.delta_x
        accum_dy = float(state["resize_accum_dy"]) + e.delta_y
        state["resize_accum_dx"] = accum_dx
        state["resize_accum_dy"] = accum_dy

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

        old_guides = state["snap_guides"]
        new_guide_ctrls = _build_guide_lines(guides)
        canvas_stack = state.get("canvas_stack")
        if canvas_stack and old_guides:
            for g in old_guides:
                if g in canvas_stack.controls:
                    canvas_stack.controls.remove(g)
        state["snap_guides"] = new_guide_ctrls
        if canvas_stack and new_guide_ctrls:
            canvas_stack.controls.extend(new_guide_ctrls)

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
        state["snap_guides"] = []
        state["resize_bottom_start_y"] = None
        _resolve_overlaps_sticky()
        _refresh_all()

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

        is_selected = element.id == _selected_id() or element.id in _selected_ids()
        border_color = "#FF4B4B" if is_selected else "#A5A5A5"
        bg_color = "#FFF7E0" if is_selected else "#FFFFFF"

        if element.type == "text":
            is_inline_editing = state["inline_edit_id"] == element.id

            if is_inline_editing:
                # 인라인 편집 모드: TextField 표시
                def _commit_inline(e: ft.ControlEvent) -> None:
                    current = next((el for el in _doc().elements if el.id == element.id), None)
                    if current:
                        _upsert_element(replace(current, text_template=e.control.value or ""))
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
                        _upsert_element(replace(current, text_template=e.control.value or ""))
                    state["inline_edit_id"] = None
                    _refresh_all()

                div_inline_field = ft.TextField(
                    value=element.text_template,
                    text_size=max(9, int(12 * _preview_scale())),
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
                    alignment=ft.alignment.center,
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
                            ft.Container(expand=True, content=_make_line_ctrl(line_w * 0.4), alignment=ft.alignment.center),
                            ft.Container(
                                padding=ft.padding.symmetric(horizontal=4),
                                content=ft.Text(
                                    div_text,
                                    size=max(9, int(element.font_size * _preview_scale() * 0.9)),
                                    weight=ft.FontWeight.BOLD if element.bold else ft.FontWeight.NORMAL,
                                    color="#333333",
                                    text_align=ft.TextAlign.CENTER,
                                    no_wrap=True,
                                ),
                            ),
                            ft.Container(expand=True, content=_make_line_ctrl(line_w * 0.4), alignment=ft.alignment.center),
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
                    alignment=ft.alignment.center,
                    content=divider_content,
                )
        elif element.type == "image":
            if element.asset_path and Path(element.asset_path).exists():
                image_fit = ft.ImageFit.CONTAIN if element.preserve_ratio else ft.ImageFit.FILL
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
                alignment=ft.alignment.center,
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

        # 선택된 요소의 참조 저장 (리사이즈/다중 드래그 시 시각 갱신용)
        if is_selected:
            state["selected_detector"] = detector
            state["selected_body"] = body
            state["element_detectors"][element.id] = detector

        def on_tap(_: ft.ControlEvent) -> None:
            state["last_element_tap_time"] = time.monotonic()
            _set_canvas_focus(True)
            if state["ctrl_pressed"]:
                # Ctrl+클릭: 다중 선택 토글
                _toggle_selection(element.id)
                _refresh_all()
                return
            already_selected = _selected_id() == element.id
            _set_selected_id(element.id)
            if element.type == "qr":
                _set_active_binding_target("data_template")
            else:
                _set_active_binding_target("text_template")
            # 다중 선택 상태가 아닌 단일 선택일 때만 인라인 편집 진입
            if not _is_multi_selected() and already_selected and element.type in ("text", "divider") and state["inline_edit_id"] != element.id:
                state["inline_edit_id"] = element.id
            _refresh_all()

        def on_double_tap(_: ft.ControlEvent) -> None:
            """더블탭으로 텍스트/구분선 인라인 편집 진입"""
            if element.type in ("text", "divider"):
                _set_selected_id(element.id)
                _set_canvas_focus(True)
                state["inline_edit_id"] = element.id
                _refresh_all()

        def on_pan_start(_: ft.DragStartEvent) -> None:
            _set_canvas_focus(True)
            ids = _selected_ids()
            if element.id in ids and len(ids) >= 2:
                # 다중 선택 상태에서 선택된 요소를 드래그 → 다중 드래그
                state["selected_id"] = element.id
                origins: dict[str, tuple[int, int]] = {}
                for el in _doc().elements:
                    if el.id in ids:
                        origins[el.id] = (el.x, el.y)
                state["multi_drag_origins"] = origins
                moving_ids = set(origins.keys()) if origins else {element.id}
            else:
                # 단일 드래그 (선택도 단일로 전환)
                _set_selected_id(element.id)
                state["multi_drag_origins"] = {}
                moving_ids = {element.id}
            state["drag_bottom_start_y"] = _fixed_bottom_anchor_y(moving_ids=moving_ids)
            current = next((item for item in _doc().elements if item.id == element.id), None)
            if current:
                state["drag_start_x"] = current.x
                state["drag_start_y"] = current.y
            state["drag_accum_dx"] = 0.0
            state["drag_accum_dy"] = 0.0

        def on_pan_update(e: ft.DragUpdateEvent) -> None:
            origins: dict[str, tuple[int, int]] = state["multi_drag_origins"]  # type: ignore[assignment]
            moving_ids = set(origins.keys()) if origins else {element.id}
            drag_bottom_start_y = state.get("drag_bottom_start_y")
            if not isinstance(drag_bottom_start_y, int):
                drag_bottom_start_y = _fixed_bottom_anchor_y(moving_ids=moving_ids)
            accum_dx = float(state["drag_accum_dx"]) + e.delta_x
            accum_dy = float(state["drag_accum_dy"]) + e.delta_y
            state["drag_accum_dx"] = accum_dx
            state["drag_accum_dy"] = accum_dy
            total_real_dx = preview_to_real(accum_dx, real_width=real_w, preview_width=preview_w)
            total_real_dy = preview_to_real(accum_dy, real_width=real_w, preview_width=preview_w)
            c_w = int(_doc().meta.canvas_width_px)

            if origins:
                # === 다중 드래그 모드 ===
                primary = next((el for el in _doc().elements if el.id == element.id), None)
                if not primary:
                    return
                p_origin = origins.get(element.id, (primary.x, primary.y))
                p_raw_x = p_origin[0] + total_real_dx
                p_raw_y = p_origin[1] + total_real_dy
                p_clamped_x = max(0, min(int(p_raw_x), max(0, c_w - max(1, primary.w))))
                p_clamped_y = max(0, int(p_raw_y))
                p_snapped_x, p_snapped_y, guides = _calc_snap(
                    primary,
                    p_clamped_x,
                    p_clamped_y,
                    exclude_ids=moving_ids,
                    margin_bottom_start_y=drag_bottom_start_y,
                )
                p_snapped_x = max(0, min(int(p_snapped_x), max(0, c_w - max(1, primary.w))))
                p_snapped_y = max(0, int(p_snapped_y))
                snap_offset_x = p_snapped_x - p_clamped_x
                snap_offset_y = p_snapped_y - p_clamped_y

                old_guides = state["snap_guides"]
                new_guides = _build_guide_lines(guides)
                canvas_stack = state.get("canvas_stack")
                if canvas_stack and old_guides:
                    for g in old_guides:
                        if g in canvas_stack.controls:
                            canvas_stack.controls.remove(g)
                state["snap_guides"] = new_guides
                if canvas_stack and new_guides:
                    canvas_stack.controls.extend(new_guides)

                detectors: dict = state["element_detectors"]  # type: ignore[assignment]
                for eid, (ox, oy) in origins.items():
                    el = next((item for item in _doc().elements if item.id == eid), None)
                    if not el:
                        continue
                    raw_x = ox + total_real_dx
                    raw_y = oy + total_real_dy
                    cx = max(0, min(int(raw_x), max(0, c_w - max(1, el.w))))
                    cy = max(0, int(raw_y))
                    nx = max(0, min(cx + snap_offset_x, max(0, c_w - max(1, el.w))))
                    ny = max(0, int(cy + snap_offset_y))
                    updated = _update_common_dimensions(el, x=nx, y=ny, bottom_start_y=drag_bottom_start_y)
                    _upsert_element(updated)
                    det = detectors.get(eid)
                    if det:
                        det.left = real_to_preview(updated.x, real_width=real_w, preview_width=preview_w)
                        det.top = real_to_preview(updated.y, real_width=real_w, preview_width=preview_w)
                # 드래그 자동스크롤
                _auto_scroll_on_drag(
                    real_to_preview(p_snapped_y, real_width=real_w, preview_width=preview_w),
                    real_to_preview(primary.h, real_width=real_w, preview_width=preview_w),
                )
                _refresh_property_panel()
                page.update()
            else:
                # === 단일 드래그 모드 ===
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
                old_guides = state["snap_guides"]
                new_guides = _build_guide_lines(guides)
                canvas_stack = state.get("canvas_stack")
                if canvas_stack and old_guides:
                    for g in old_guides:
                        if g in canvas_stack.controls:
                            canvas_stack.controls.remove(g)
                state["snap_guides"] = new_guides
                if canvas_stack and new_guides:
                    canvas_stack.controls.extend(new_guides)
                updated = _update_common_dimensions(current, x=new_x, y=new_y, bottom_start_y=drag_bottom_start_y)
                _upsert_element(updated)
                detector.left = real_to_preview(updated.x, real_width=real_w, preview_width=preview_w)
                detector.top = real_to_preview(updated.y, real_width=real_w, preview_width=preview_w)

                # 삽입 인디케이터 갱신
                canvas_stack = state.get("canvas_stack")
                old_indicator = state["insertion_indicator"]
                if canvas_stack and old_indicator and old_indicator in canvas_stack.controls:
                    canvas_stack.controls.remove(old_indicator)
                    state["insertion_indicator"] = None

                slot_y = _calc_insertion_slot(updated, new_y, {element.id})
                if slot_y is not None:
                    indicator = _build_insertion_indicator(slot_y)
                    state["insertion_indicator"] = indicator
                    state["insertion_target_y"] = slot_y
                    if canvas_stack:
                        canvas_stack.controls.append(indicator)
                else:
                    state["insertion_target_y"] = None

                # 드래그 자동스크롤
                _auto_scroll_on_drag(
                    real_to_preview(updated.y, real_width=real_w, preview_width=preview_w),
                    real_to_preview(updated.h, real_width=real_w, preview_width=preview_w),
                )
                _refresh_property_panel()
                page.update()

        def on_pan_end(_: ft.DragEndEvent) -> None:
            state["snap_guides"] = []
            state["multi_drag_origins"] = {}
            state["drag_bottom_start_y"] = None

            # 삽입 인디케이터 제거
            canvas_stack = state.get("canvas_stack")
            old_indicator = state["insertion_indicator"]
            if canvas_stack and old_indicator and old_indicator in canvas_stack.controls:
                canvas_stack.controls.remove(old_indicator)
            state["insertion_indicator"] = None

            target_y = state["insertion_target_y"]
            state["insertion_target_y"] = None
            if target_y is not None:
                _apply_insertion_drop(element.id, target_y)
            else:
                _enforce_margin_boundaries()
                _resolve_overlaps()
            _refresh_all()

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
        gap = 4
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
        _upsert_element(replace(el, y=slot_y))
        _resolve_overlaps()

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
        # 여백 오버레이 (요소 아래에 배치하여 이벤트 차단 방지)
        canvas_controls: list[ft.Control] = []
        if mt_preview > 0:
            canvas_controls.append(ft.Container(
                left=0, top=0, width=preview_w, height=mt_preview,
                bgcolor="#E8E0D0", opacity=0.55,
            ))
        if mb_preview > 0:
            canvas_controls.append(ft.Container(
                left=0, top=preview_h - mb_preview, width=preview_w, height=mb_preview,
                bgcolor="#E8E0D0", opacity=0.55,
            ))

        # 요소 프리뷰 (여백 위에 표시되어 클릭/드래그 가능)
        canvas_controls.extend(_build_element_preview(element) for element in _doc().elements if element.visible)

        # 단일 선택일 때만 리사이즈 핸들 추가 (다중 선택 시 비활성)
        selected_el = _find_selected_element()
        if selected_el and selected_el.visible and not _is_multi_selected():
            canvas_controls.extend(_build_resize_handles(selected_el))

        # 스냅 가이드선 추가
        canvas_controls.extend(state["snap_guides"])

        canvas_stack.controls = canvas_controls
        canvas_stack.width = preview_w
        canvas_stack.height = preview_h
        state["canvas_stack"] = canvas_stack

        canvas_frame_body.width = preview_w
        canvas_frame_body.height = preview_h
        canvas_frame_body.content = canvas_stack

        total_h = preview_h
        needs_scroll = total_h > max_viewport_h
        scrollable_canvas.height = viewport_h
        scrollable_canvas.scroll = ft.ScrollMode.AUTO if needs_scroll else None
        # 스크롤바 여백 갱신
        scroll_gutter = state.get("scroll_gutter_ctrl")
        if scroll_gutter is not None:
            scroll_gutter.width = 14 if needs_scroll else 0
            scroll_gutter.height = preview_h

        canvas_meta_text.value = (
            f"편집 캔버스 ({preview_w}x{preview_h}) / 실제폭 {_doc().meta.canvas_width_px}px"
            + (f" / 여백 상:{_margin_top_px()} 하:{_margin_bottom_px()}" if _margin_top_px() or _margin_bottom_px() else "")
        )

    def _refresh_all() -> None:
        _refresh_canvas()
        _refresh_property_panel()
        page.update()

    def on_add_text(_: ft.ControlEvent) -> None:
        _add_element(_new_default_element("text"))
        _set_active_binding_target("text_template")
        _refresh_all()

    def on_add_qr(_: ft.ControlEvent) -> None:
        _add_element(_new_default_element("qr"))
        _set_active_binding_target("data_template")
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

    def on_save_as(_: ft.ControlEvent) -> None:
        save_as_picker.save_file(
            dialog_title="템플릿 다른이름으로 저장",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["json"],
            file_name="receipt_layout.json",
        )

    def on_save_as_result(event: ft.FilePickerResultEvent) -> None:
        if not event.path:
            return
        path = event.path
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            canvas_store.export_portable(path, _doc())
            _show_status(f"포터블 저장 완료: {Path(path).name}")
        except Exception as exc:
            _show_status(f"포터블 저장 실패: {exc}")
        _refresh_all()

    save_as_picker.on_result = on_save_as_result

    def on_load(_: ft.ControlEvent) -> None:
        load_picker.pick_files(
            dialog_title="템플릿 불러오기",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["json"],
            allow_multiple=False,
        )

    def on_load_result(event: ft.FilePickerResultEvent) -> None:
        if not event.files:
            return
        path = event.files[0].path
        try:
            doc = canvas_store.load_layout(path)
            _set_doc(doc)
            _set_layout_path(path)
            paper_width_dropdown.value = doc.meta.paper_width
            state["selected_id"] = None
            state["selected_ids"] = set()
            _refresh_all()
            _show_status(f"불러오기 완료: {Path(path).name}")
        except Exception as exc:
            _show_status(f"불러오기 실패: {exc}")

    load_picker.on_result = on_load_result

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

    def on_paper_width_change(_: ft.ControlEvent) -> None:
        _set_paper_width(str(paper_width_dropdown.value or "80"))

    def on_image_picked(event: ft.FilePickerResultEvent) -> None:
        if not event.files:
            return
        try:
            imported = canvas_store.import_image_asset(event.files[0].path)
            selected = _find_selected_element()
            if selected and selected.type == "image":
                _upsert_element(replace(selected, asset_path=imported))
            else:
                _add_element(_new_default_element("image", asset_path=imported))
            _refresh_all()
            _show_status("이미지를 자산 폴더로 가져왔습니다.")
        except Exception as exc:
            _show_status(f"이미지 추가 실패: {exc}")

    image_picker.on_result = on_image_picked

    btn_add_text.on_click = on_add_text
    btn_add_image.on_click = on_add_image
    btn_add_qr.on_click = on_add_qr
    btn_add_divider.on_click = on_add_divider
    btn_delete.on_click = lambda _e: _remove_selected_element()
    btn_save.on_click = on_save
    btn_save_as.on_click = on_save_as
    btn_load.on_click = on_load
    btn_test_print.on_click = on_test_print
    paper_width_dropdown.on_change = on_paper_width_change
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
                text=label,
                height=30,
                on_click=lambda _e, key=field_key: _insert_binding(key),
            )
            for field_key, label in FIELD_BINDINGS
        ],
        wrap=True,
        spacing=6,
        run_spacing=6,
    )
    left_controls_panel = ft.Container(
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
                        btn_add_qr,
                        btn_add_divider,
                        btn_delete,
                    ],
                    spacing=8,
                    wrap=True,
                ),
                ft.Text("필드칩: 클릭하면 선택한 텍스트/QR 템플릿에 변수 삽입", size=12, color="#666666"),
                field_chip_row,
                status_text,
            ],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        ),
    )

    canvas_container = ft.Container(
        bgcolor="#F7F7F7",
        border_radius=8,
        padding=10,
        content=canvas_host,
        alignment=ft.alignment.top_center,
    )

    property_wrapper = ft.Container(
        bgcolor="#F7F7F7",
        border_radius=8,
        padding=10,
        content=property_panel,
        alignment=ft.alignment.top_center,
    )

    right_workspace = ft.Container(
        expand=3,
        content=ft.Column(
            controls=[
                property_wrapper,
                canvas_container,
            ],
            spacing=12,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )

    main_split_layout = ft.Row(
        controls=[
            left_controls_panel,
            right_workspace,
        ],
        spacing=12,
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    # 키보드 이벤트: Ctrl 추적 + Del 삭제
    def _on_keyboard(e: ft.KeyboardEvent) -> None:
        # Ctrl 키 상태를 이벤트의 ctrl 속성으로 추적
        state["ctrl_pressed"] = bool(e.ctrl)
        # Del 키: 단일/다중 선택 요소 삭제
        if e.key == "Delete" and state["canvas_focused"] and (_selected_id() or _selected_ids()):
            _remove_selected_element()

    page.on_keyboard_event = _on_keyboard

    # 윈도우 포커스 복귀 시 Ctrl 상태 리셋 (고착 방지)
    def _on_window_event(e):
        if str(getattr(e, "data", "")) == "focus":
            state["ctrl_pressed"] = False

    page.on_window_event = _on_window_event

    if _doc().meta.paper_width != str(paper_width_dropdown.value):
        _set_paper_width(str(paper_width_dropdown.value or "80"))
    _set_layout_path(layout_path)
    _refresh_all()

    return ft.Container(
        padding=ft.padding.all(12),
        expand=True,
        content=main_split_layout,
    )


class SettingsFletView:
    """Standalone settings window (compat mode)."""

    def __init__(self, store_path: str = ".runtime/receipt_settings.json"):
        self._store_path = store_path

    def run(self) -> None:
        ft.app(target=self._build_page)

    def _build_page(self, page: ft.Page) -> None:
        page.title = "Receipt Settings"
        page.window_width = 1480
        page.window_height = 940
        page.scroll = ft.ScrollMode.AUTO
        page.bgcolor = "#ECECEC"
        page.add(build_receipt_settings_panel(page, store_path=self._store_path))


def run_settings_app() -> None:
    SettingsFletView().run()


if __name__ == "__main__":
    run_settings_app()
