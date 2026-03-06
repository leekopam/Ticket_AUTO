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
from views.settings_flet_view import build_receipt_settings_panel

ICONS = getattr(ft, "Icons", ft.icons)


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


class DashboardFletView:
    """Main control center UI."""

    def __init__(self, runtime_manager: TicketRuntimeManager | None = None):
        self._runtime_manager = runtime_manager or TicketRuntimeManager()

    def run(self) -> None:
        ft.app(target=self._build_page)

    def _build_page(self, page: ft.Page) -> None:
        page.title = "Ticket_AUTO Control Center"
        page.window_width = 1440
        page.window_height = 920
        page.window_resizable = False
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
        search_result_table = ft.DataTable(
            columns=[
                ft.DataColumn(ft.Text("주문번호", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("이름", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("연락처", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("좌석번호", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("상품목록", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("티켓", weight=ft.FontWeight.BOLD)),
                ft.DataColumn(ft.Text("처리완료", weight=ft.FontWeight.BOLD)),
            ],
            rows=[],
            column_spacing=30,
            data_row_max_height=float("inf"),
            expand=True,
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
        btn_receipt_tab = ft.TextButton("영수증 양식 설정", icon=ICONS.RECEIPT_LONG_ROUNDED)
        content_host = ft.Container(expand=True, padding=ft.padding.all(16))

        # 주문 선택 출력용 Dropdown + 버튼
        orders_map: dict[str, Order] = {}
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

        def do_search(_=None) -> None:
            """검색어 + 필터 조건으로 주문 조회 후 테이블 갱신."""
            keyword = search_field.value or ""
            filter_value = filter_dropdown.value or "전체"
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

            # Dropdown 옵션 갱신
            orders_map.clear()
            dropdown_options: list[ft.dropdown.Option] = []
            rows: list[ft.DataRow] = []
            
            receipt_settings = settings_store.load()
            ticket_names = receipt_settings.ticket_product_names
            
            for o in orders:
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
                
                rows.append(ft.DataRow(cells=[
                    ft.DataCell(ft.Text(o.order_number, size=13, color="#2A7FFF")),
                    ft.DataCell(ft.Text(o.name, size=13)),
                    ft.DataCell(ft.Text(o.phone, size=13)),
                    ft.DataCell(ft.Text(o.seat, size=13)),
                    ft.DataCell(ft.Text(goods_text, size=13, no_wrap=False)),
                    ft.DataCell(ft.Text(ticket_text, size=13, no_wrap=False)),
                    ft.DataCell(ft.Text(o.received_at if o.received_at else "-", size=13)),
                ]))
            print_order_dropdown.options = dropdown_options
            print_order_dropdown.value = None
            search_result_table.rows = rows
            page.update()

        search_field.on_submit = do_search
        filter_dropdown.on_change = do_search

        def set_tab(tab_key: str) -> None:
            current_tab["value"] = tab_key
            content_host.content = resolve_tab_content(tab_key, ticket_panel, receipt_settings_panel)
            apply_sidebar_styles()
            if tab_key == "ticket":
                do_search()
                return
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

        def refresh_status(state: str, message: str, timestamp: str) -> None:
            current_state["value"] = state
            state_text.value = state
            state_badge.bgcolor = state_to_color(state)
            runtime_hint_text.value = message
            last_event_text.value = f"마지막 이벤트: {timestamp}"
            apply_button_state(state)
            page.update()

        def on_runtime_event(state: str, message: str, timestamp: str) -> None:
            def update_ui() -> None:
                refresh_status(state, message, timestamp)

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

        ticket_panel = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("티켓 확인 제어", size=28, weight=ft.FontWeight.BOLD, color="#1D1D1D"),
                    ft.Text(
                        "시작 버튼을 눌러야 Playwright, QR Scanner, 주문창이 실행됩니다.",
                        size=14,
                        color="#5A5A5A",
                    ),
                    ft.Row(controls=[state_badge, last_event_text], spacing=14, wrap=True),
                    runtime_hint_text,
                    ft.Row(controls=[btn_start_stop, btn_relogin], spacing=10),
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
                                        controls=[search_result_table],
                                        scroll=ft.ScrollMode.AUTO,
                                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                                    ),
                                    bgcolor="#FFFFFF",
                                    border=ft.border.all(1, "#D2D2D2"),
                                    border_radius=8,
                                    padding=ft.padding.all(10),
                                    expand=True,
                                ),
                            ],
                            spacing=8,
                            expand=True,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        margin=ft.margin.only(top=14),
                        expand=True,
                    ),
                ],
                spacing=8,
                expand=True,
            ),
            padding=ft.padding.all(8),
            expand=True,
        )

        receipt_settings_panel = build_receipt_settings_panel(page)

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
                    ft.Text("Settings", color="#666666", size=13),
                    ft.Text("v1 Control Center", color="#888888", size=12),
                ],
                spacing=8,
            ),
        )

        apply_button_state("IDLE")
        apply_sidebar_styles()
        set_tab("ticket")

        self._runtime_manager.subscribe(on_runtime_event)

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

        def on_window_event(event: ft.WindowEvent) -> None:
            if event.data == "close":
                self._runtime_manager.stop(timeout_sec=4.0)
                page.window.destroy()

        page.on_window_event = on_window_event


def run_dashboard_app() -> None:
    DashboardFletView().run()


if __name__ == "__main__":
    run_dashboard_app()
