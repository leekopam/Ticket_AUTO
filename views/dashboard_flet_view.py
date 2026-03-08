"""Integrated Flet control dashboard for Ticket_AUTO."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 직접 실행 시 프로젝트 루트를 sys.path에 추가
sys.path.append(str(Path(__file__).parent.parent))

import flet as ft

import threading

logger = logging.getLogger(__name__)

from models.order_model import Order
from services.excel_service import ExcelService
from services.receipt_print_pipeline import print_order_receipt
from services.receipt_settings_store import ReceiptSettingsStore
from services.ticket_runtime_manager import TicketRuntimeManager
from views.settings_flet_view import build_app_settings_panel, build_receipt_settings_panel

ICONS = getattr(ft, "Icons", ft.icons)
SEARCH_DEBOUNCE_SEC = 0.25
EXCEL_WATCH_INTERVAL_SEC = 0.5


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
    """Return True when ticket search results should refresh automatically."""
    return tab_key == "ticket" and runtime_state not in {"STARTING", "STOPPING"}


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
) -> tuple[object, ...]:
    """Build a comparable snapshot so unchanged refreshes do not redraw the UI."""
    return (
        keyword.strip(),
        filter_value,
        tuple(ticket_names),
        tuple(
            (
                order.order_number,
                order.name,
                order.phone,
                order.seat,
                tuple(order.goods or []),
                order.received_at or "",
            )
            for order in orders
        ),
    )


class DashboardFletView:
    """Main control center UI."""

    def __init__(self, runtime_manager: TicketRuntimeManager | None = None):
        self._runtime_manager = runtime_manager or TicketRuntimeManager()

    def run(self) -> None:
        ft.app(target=self._build_page)

    def _build_page(self, page: ft.Page) -> None:
        page.title = "Ticket_AUTO Control Center"
        page.window.width = 1440
        page.window.height = 920
        page.window.resizable = False
        page.padding = 0
        page.bgcolor = "#EDEDED"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = ft.Theme(font_family="Segoe UI")

        # 캔버스 미리보기용 폰트 등록
        page.fonts = {
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

        current_tab = {"value": "ticket"}
        current_state = {"value": "IDLE"}
        data_file_path = Path("data.xlsx")
        excel_service = ExcelService()
        excel_service.ensure_seat_column()
        excel_service.ensure_receipt_column()
        settings_store = ReceiptSettingsStore(".runtime/receipt_settings.json")

        state_text = ft.Text("IDLE", size=18, weight=ft.FontWeight.BOLD, color="#1F1F1F")
        state_badge = ft.Container(
            content=state_text,
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
            bgcolor="#DDE8FF",
            border_radius=10,
        )
        last_event_text = ft.Text("마지막 이벤트: -", color="#505050")
        runtime_hint_text = ft.Text("런타임 대기 중", color="#606060", size=13)

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
                ft.dropdown.Option("수령완료"),
                ft.dropdown.Option("미수령"),
            ],
            width=120,
            height=42,
            border_radius=8,
            content_padding=ft.padding.symmetric(horizontal=10, vertical=0),
        )
        filter_count_text = ft.Text("", size=13, color="#666666")
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
                    _build_header_cell("처리완료", 2),
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
                bgcolor="#2A7FFF",
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        btn_relogin = ft.OutlinedButton(
            "재로그인",
            icon=ICONS.LOGIN_ROUNDED,
        )

        btn_ticket_tab = ft.TextButton("티켓 확인", icon=ICONS.CONFIRMATION_NUMBER_ROUNDED)
        btn_receipt_tab = ft.TextButton("영수증 양식", icon=ICONS.RECEIPT_LONG_ROUNDED)
        content_host = ft.Container(expand=True, padding=ft.padding.all(16))

        # 주문 선택 출력용 Dropdown + 버튼
        orders_map: dict[str, Order] = {}
        last_search_signature = {"value": None}
        search_refresh_lock = threading.Lock()
        search_refresh_timer: threading.Timer | None = None
        search_refresh_stop = threading.Event()
        print_order_dropdown = ft.Dropdown(
            hint_text="주문번호 선택",
            width=200,
            height=42,
            border_radius=8,
            content_padding=ft.padding.symmetric(horizontal=10, vertical=0),
            options=[],
        )
        btn_print_selected = ft.ElevatedButton(
            "출력",
            icon=ICONS.PRINT_ROUNDED,
            style=ft.ButtonStyle(
                bgcolor="#2A7FFF",
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        def _on_print_selected_click(_e) -> None:
            """Dropdown에서 선택된 주문의 영수증을 출력한다."""
            key = print_order_dropdown.value
            order = orders_map.get(key) if key else None
            if not order:
                page.snack_bar = ft.SnackBar(
                    ft.Text("주문번호를 선택해주세요."), bgcolor="#FFF0CE",
                )
                page.snack_bar.open = True
                page.update()
                return
            _on_order_print(order)

        btn_print_selected.on_click = _on_print_selected_click

        def _on_order_print(order: Order) -> None:
            """주문번호 클릭 시 해당 주문의 영수증을 출력한다."""
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
                    page.snack_bar = ft.SnackBar(ft.Text(msg), bgcolor=color)
                    page.snack_bar.open = True
                    page.update()

                try:
                    page.call_from_thread(_show_snack)
                except Exception:
                    _show_snack()

            threading.Thread(target=_do_print, daemon=True).start()

        def do_search(_=None, push_update: bool = True) -> None:
            """검색어 + 필터 조건으로 주문 조회 후 테이블 갱신."""
            keyword = search_field.value or ""
            filter_value = filter_dropdown.value or "전체"
            previous_order_value = print_order_dropdown.value
            try:
                orders = excel_service.search_orders(keyword)
            except Exception:
                orders = []
            # 인원수 카운트 산출
            total = len(orders)
            received = sum(1 for o in orders if o.is_received)
            unreceived = total - received
            if filter_value == "전체":
                filter_count_text.value = f"{total}명"
            elif filter_value == "수령완료":
                filter_count_text.value = f"{received} / {total}명"
            else:
                filter_count_text.value = f"{unreceived} / {total - received}명"
            # 수령 상태 필터 적용
            if filter_value == "수령완료":
                orders = [o for o in orders if o.is_received]
            elif filter_value == "미수령":
                orders = [o for o in orders if not o.is_received]

            try:
                receipt_settings = settings_store.load()
                ticket_names = receipt_settings.ticket_product_names
            except Exception:
                ticket_names = []

            search_signature = build_order_search_signature(
                keyword,
                filter_value,
                ticket_names,
                orders,
            )
            if search_signature == last_search_signature["value"]:
                return

            # Dropdown 옵션 갱신
            orders_map.clear()
            dropdown_options: list[ft.dropdown.Option] = []
            rows: list[ft.Container] = []

            for i, o in enumerate(orders):
                orders_map[o.order_number] = o
                dropdown_options.append(ft.dropdown.Option(o.order_number))
                
                general_goods = []
                ticket_goods = []
                for g in (o.goods or []):
                    if any(t_name in g for t_name in ticket_names):
                        ticket_goods.append(g)
                    else:
                        general_goods.append(g)
                
                goods_text = "\n".join(general_goods) if general_goods else "-"
                ticket_text = "\n".join(ticket_goods) if ticket_goods else "-"
                
                def _build_data_cell(control: ft.Control, expand: int) -> ft.Container:
                    return ft.Container(content=control, expand=expand, alignment=ft.alignment.center_left)
                
                row_bg = "#FFFFFF" if i % 2 == 0 else "#FAFAFA"
                
                rows.append(ft.Container(
                    content=ft.Row(
                        controls=[
                            _build_data_cell(ft.Text(o.order_number, size=13, color="#2A7FFF"), 3),
                            _build_data_cell(ft.Text(o.name, size=13), 2),
                            _build_data_cell(ft.Text(o.phone, size=13), 3),
                            _build_data_cell(ft.Text(o.seat, size=13), 2),
                            _build_data_cell(ft.Text(goods_text, size=13), 4),
                            _build_data_cell(ft.Text(ticket_text, size=13), 3),
                            _build_data_cell(ft.Text(o.received_at if o.received_at else "-", size=13), 2),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    bgcolor=row_bg,
                    padding=ft.padding.symmetric(horizontal=16, vertical=12),
                    border=ft.border.only(bottom=ft.border.BorderSide(1, "#EEEEEE")),
                ))
            print_order_dropdown.options = dropdown_options
            print_order_dropdown.value = resolve_preserved_order_selection(
                previous_order_value,
                set(orders_map),
            )
            search_result_list.controls = rows
            last_search_signature["value"] = search_signature
            if push_update:
                page.update()

        def cancel_scheduled_search_refresh() -> None:
            nonlocal search_refresh_timer
            with search_refresh_lock:
                timer = search_refresh_timer
                search_refresh_timer = None
            if timer is not None:
                timer.cancel()

        def refresh_search_results_from_thread(push_update: bool = True) -> None:
            try:
                page.call_from_thread(lambda: do_search(push_update=push_update))
            except Exception:
                do_search(push_update=push_update)

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
            last_seen_mtime_ns = read_file_mtime_ns(data_file_path)
            while not search_refresh_stop.wait(EXCEL_WATCH_INTERVAL_SEC):
                current_mtime_ns = read_file_mtime_ns(data_file_path)
                workbook_changed = has_file_timestamp_changed(last_seen_mtime_ns, current_mtime_ns)
                if current_mtime_ns is not None:
                    last_seen_mtime_ns = current_mtime_ns
                if not should_auto_refresh_order_views(current_tab["value"], current_state["value"]):
                    continue
                if workbook_changed:
                    schedule_search_refresh(delay_sec=0)
                    continue
                refresh_search_results_from_thread(push_update=True)

        search_field.on_change = lambda _e: schedule_search_refresh()
        search_field.on_submit = do_search
        filter_dropdown.on_change = do_search

        def set_tab(tab_key: str, push_update: bool = True) -> None:
            current_tab["value"] = tab_key
            content_host.content = resolve_tab_content(tab_key, ticket_panel, receipt_settings_panel)
            apply_sidebar_styles()
            if tab_key == "ticket":
                do_search(push_update=False)
                if push_update:
                    page.update()
                return
            if push_update:
                page.update()

        def state_to_color(state: str) -> str:
            if state == "RUNNING":
                return "#D8F4E3"
            if state == "RECOVERING":
                return "#FFF0CE"
            if state == "ERROR":
                return "#FFD6D6"
            if state == "STOPPING":
                return "#E5E5E5"
            if state == "STARTING":
                return "#DDE8FF"
            return "#DDE8FF"

        def apply_button_state(state: str) -> None:
            start_enabled, stop_enabled, relogin_enabled = compute_button_enabled(state)
            btn_relogin.disabled = not relogin_enabled
            # 실행 중 → 중지 버튼 / 그 외 → 시작 버튼으로 토글
            if stop_enabled:
                btn_start_stop.text = "중지"
                btn_start_stop.icon = ICONS.STOP_CIRCLE_ROUNDED
                btn_start_stop.style = ft.ButtonStyle(
                    bgcolor="#DD4C4C", color="#FFFFFF",
                    shape=ft.RoundedRectangleBorder(radius=8),
                )
                btn_start_stop.disabled = False
                btn_start_stop.on_click = on_stop
            else:
                btn_start_stop.text = "티켓 확인 시작"
                btn_start_stop.icon = ICONS.PLAY_ARROW_ROUNDED
                btn_start_stop.style = ft.ButtonStyle(
                    bgcolor="#2A7FFF", color="#FFFFFF",
                    shape=ft.RoundedRectangleBorder(radius=8),
                )
                btn_start_stop.disabled = not start_enabled
                btn_start_stop.on_click = on_start

        def apply_sidebar_styles() -> None:
            active_bg = "#DDE8FF"
            inactive_bg = "#00000000"
            btn_ticket_tab.style = ft.ButtonStyle(
                bgcolor=active_bg if current_tab["value"] == "ticket" else inactive_bg,
                color="#1B1B1B",
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.padding.symmetric(horizontal=14, vertical=12),
            )
            btn_receipt_tab.style = ft.ButtonStyle(
                bgcolor=active_bg if current_tab["value"] == "receipt" else inactive_bg,
                color="#1B1B1B",
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.padding.symmetric(horizontal=14, vertical=12),
            )

        def refresh_status(
            state: str,
            message: str,
            timestamp: str,
            *,
            push_update: bool = True,
        ) -> None:
            current_state["value"] = state
            state_text.value = state
            state_badge.bgcolor = state_to_color(state)
            runtime_hint_text.value = message
            last_event_text.value = f"마지막 이벤트: {timestamp}"
            apply_button_state(state)
            if push_update:
                page.update()

        def on_runtime_event(state: str, message: str, timestamp: str) -> None:
            def update_ui() -> None:
                refresh_status(state, message, timestamp, push_update=False)
                if should_auto_refresh_order_views(current_tab["value"], state):
                    do_search(push_update=False)
                page.update()

            try:
                page.call_from_thread(update_ui)
            except Exception:
                update_ui()

        def on_start(_: ft.ControlEvent) -> None:
            self._runtime_manager.start()

        def on_stop(_: ft.ControlEvent) -> None:
            self._runtime_manager.stop()

        def on_relogin(_: ft.ControlEvent) -> None:
            self._runtime_manager.relogin()

        btn_start_stop.on_click = on_start
        btn_relogin.on_click = on_relogin
        btn_ticket_tab.on_click = lambda _: set_tab("ticket")
        btn_receipt_tab.on_click = lambda _: set_tab("receipt")

        camera_view = ft.Image(width=400, height=300, fit=ft.ImageFit.CONTAIN, visible=False)

        def on_camera_frame(b64_str: str) -> None:
            camera_view.src_base64 = b64_str
            camera_view.visible = True
            try:
                page.call_from_thread(camera_view.update)
            except Exception:
                try:
                    camera_view.update()
                except Exception:
                    pass

        self._runtime_manager.set_camera_frame_listener(on_camera_frame)

        top_controls_col = ft.Column(
            controls=[
                ft.Text("티켓 확인 제어", size=28, weight=ft.FontWeight.BOLD, color="#1D1D1D"),
                ft.Container(height=8),
                ft.Row(controls=[btn_start_stop, btn_relogin], spacing=10),
            ],
            expand=False,
        )

        # 구매자 정보 표시 UI
        buyer_name_text = ft.Text("", size=18, weight=ft.FontWeight.BOLD)
        buyer_phone_text = ft.Text("", size=16, color="#333333")
        buyer_seat_text = ft.Text("", size=16, color="#333333")
        buyer_goods_text = ft.Text("", size=14, color="#444444")
        buyer_ticket_text = ft.Text("", size=14, color="#2A7FFF", weight=ft.FontWeight.BOLD)
        buyer_received_text = ft.Text("", size=13, color="#888888")
        buyer_empty_hint = ft.Text(
            "QR 스캔 시 구매자 정보가 표시됩니다",
            size=14, color="#AAAAAA", text_align=ft.TextAlign.CENTER,
        )
        current_buyer_order: dict[str, Order | None] = {"value": None}

        btn_buyer_print = ft.ElevatedButton(
            "영수증 출력",
            icon=ICONS.PRINT_ROUNDED,
            disabled=True,
            style=ft.ButtonStyle(
                bgcolor="#2A7FFF",
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )

        def _on_buyer_print_click(_e) -> None:
            order = current_buyer_order["value"]
            if not order:
                return
            btn_buyer_print.disabled = True
            page.update()
            _on_order_print(order)
            btn_buyer_print.disabled = False
            page.update()

        btn_buyer_print.on_click = _on_buyer_print_click

        buyer_detail_col = ft.Column(
            controls=[
                buyer_name_text,
                buyer_phone_text,
                buyer_seat_text,
                ft.Divider(height=1, color="#E0E0E0"),
                buyer_goods_text,
                buyer_ticket_text,
                buyer_received_text,
                ft.Container(height=4),
                btn_buyer_print,
            ],
            spacing=6,
            visible=False,
        )

        def update_buyer_info(order: Order) -> None:
            """구매자 정보 패널을 주문 데이터로 갱신한다."""
            current_buyer_order["value"] = order
            buyer_name_text.value = f"주문자명: {order.name}"
            buyer_phone_text.value = f"연락처: {order.phone}"
            buyer_seat_text.value = f"좌석번호: {order.seat}"

            try:
                receipt_settings = settings_store.load()
                ticket_names = set(receipt_settings.ticket_product_names)
            except Exception:
                ticket_names = set()

            general, ticket = [], []
            for item in (order.goods or []):
                product_name = item.rsplit(" x", 1)[0] if " x" in item else item
                if product_name in ticket_names:
                    ticket.append(item)
                else:
                    general.append(item)

            buyer_goods_text.value = "상품: " + (", ".join(general) if general else "-")
            if ticket:
                buyer_ticket_text.value = "티켓: " + ", ".join(ticket)
                buyer_ticket_text.visible = True
            else:
                buyer_ticket_text.value = ""
                buyer_ticket_text.visible = False

            if order.is_received:
                buyer_received_text.value = f"수령완료: {order.received_at}"
                buyer_received_text.visible = True
            else:
                buyer_received_text.value = ""
                buyer_received_text.visible = False

            btn_buyer_print.disabled = False
            buyer_detail_col.visible = True
            buyer_empty_hint.visible = False

        def on_order_event(order: Order) -> None:
            """Application에서 전달된 주문 정보를 UI에 반영한다."""
            try:
                page.call_from_thread(lambda: _apply_order_update(order))
            except Exception:
                _apply_order_update(order)

        def _apply_order_update(order: Order) -> None:
            update_buyer_info(order)
            page.update()

        self._runtime_manager.set_order_listener(on_order_event)

        buyer_info_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("구매자 정보", weight=ft.FontWeight.BOLD, size=16),
                    buyer_empty_hint,
                    buyer_detail_col,
                ],
                spacing=8,
            ),
            bgcolor="#FFFFFF",
            border_radius=8,
            border=ft.border.all(1, "#D2D2D2"),
            padding=ft.padding.all(16),
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

        ticket_panel = ft.Container(
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
                    ft.Container(
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
                                            on_click=do_search,
                                            style=ft.ButtonStyle(
                                                bgcolor="#2A7FFF",
                                                color="#FFFFFF",
                                                shape=ft.RoundedRectangleBorder(radius=8),
                                            ),
                                        ),
                                        ft.Container(width=16),
                                        print_order_dropdown,
                                        btn_print_selected,
                                    ],
                                    spacing=8,
                                ),
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
                    ),
                ],
                spacing=8,
                scroll=ft.ScrollMode.AUTO,
            ),
            padding=ft.padding.all(8),
            expand=True,
        )

        receipt_settings_panel = build_receipt_settings_panel(
            page,
            initial_section="receipt",
            show_section_tabs=False,
        )
        app_settings_panel = build_app_settings_panel(page)

        settings_dlg = ft.AlertDialog(
            title=ft.Text("설정", size=20, weight=ft.FontWeight.BOLD),
            content=ft.Container(
                width=1120,
                height=760,
                content=app_settings_panel,
            ),
            actions=[
                ft.TextButton("닫기", on_click=lambda e: close_settings_dialog())
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        def close_settings_dialog():
            settings_dlg.open = False
            page.update()

        def open_settings_dialog(e):
            page.dialog = settings_dlg
            settings_dlg.open = True
            page.update()

        sidebar = ft.Container(
            width=260,
            bgcolor="#F3F4F5",
            border=ft.border.only(right=ft.BorderSide(1, "#DDDDDD")),
            padding=ft.padding.symmetric(horizontal=16, vertical=18),
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ICONS.TUNE_ROUNDED, color="#2A7FFF"),
                            ft.Text("Magical Play", size=32, weight=ft.FontWeight.W_700),
                        ],
                        spacing=10,
                    ),
                    ft.Container(height=20),
                    btn_ticket_tab,
                    btn_receipt_tab,
                    ft.Container(expand=True),
                    ft.TextButton(
                        text="Settings",
                        icon=ICONS.SETTINGS,
                        icon_color="#666666",
                        on_click=open_settings_dialog,
                        style=ft.ButtonStyle(
                            color="#666666",
                            padding=ft.padding.symmetric(horizontal=8, vertical=4),
                            shape=ft.RoundedRectangleBorder(radius=6),
                        ),
                    ),
                    ft.Text("v1 Control Center", color="#888888", size=12),
                ],
                spacing=8,
            ),
        )

        apply_button_state("IDLE")
        apply_sidebar_styles()

        page.add(
            ft.Row(
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
        )

        set_tab("ticket", push_update=True)
        self._runtime_manager.subscribe(on_runtime_event)
        threading.Thread(target=watch_excel_changes, daemon=True).start()

        def on_window_event(event: ft.WindowEvent) -> None:
            if event.data == "close":
                search_refresh_stop.set()
                cancel_scheduled_search_refresh()
                self._runtime_manager.stop(timeout_sec=4.0)
                page.window.destroy()

        page.window.on_event = on_window_event


def run_dashboard_app() -> None:
    DashboardFletView().run()


if __name__ == "__main__":
    run_dashboard_app()
