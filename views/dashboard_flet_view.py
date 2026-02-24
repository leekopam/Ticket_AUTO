"""Integrated Flet control dashboard for Ticket_AUTO."""
from __future__ import annotations

import sys
from pathlib import Path

# 직접 실행 시 프로젝트 루트를 sys.path에 추가
sys.path.append(str(Path(__file__).parent.parent))

import flet as ft

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
    return ticket_panel if tab_key == "ticket" else receipt_panel


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
        page.padding = 0
        page.bgcolor = "#EDEDED"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = ft.Theme(font_family="Segoe UI")

        current_tab = {"value": "ticket"}
        current_state = {"value": "IDLE"}
        logs: list[str] = []

        state_text = ft.Text("IDLE", size=18, weight=ft.FontWeight.BOLD, color="#1F1F1F")
        state_badge = ft.Container(
            content=state_text,
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
            bgcolor="#DDE8FF",
            border_radius=10,
        )
        last_event_text = ft.Text("마지막 이벤트: -", color="#505050")
        runtime_hint_text = ft.Text("런타임 대기 중", color="#606060", size=13)
        log_list = ft.ListView(expand=True, spacing=4, auto_scroll=False)

        btn_start = ft.ElevatedButton(
            "티켓 확인 시작",
            icon=ICONS.PLAY_ARROW_ROUNDED,
            style=ft.ButtonStyle(
                bgcolor="#2A7FFF",
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        btn_stop = ft.ElevatedButton(
            "중지",
            icon=ICONS.STOP_CIRCLE_ROUNDED,
            style=ft.ButtonStyle(
                bgcolor="#DD4C4C",
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

        def append_log(message: str) -> None:
            logs.append(message)
            if len(logs) > 50:
                del logs[:-50]
            log_list.controls = [
                ft.Text(item, size=13, color="#2E2E2E", selectable=True) for item in logs
            ]

        def set_tab(tab_key: str) -> None:
            current_tab["value"] = tab_key
            content_host.content = resolve_tab_content(tab_key, ticket_panel, receipt_settings_panel)
            apply_sidebar_styles()
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
            btn_start.disabled = not start_enabled
            btn_stop.disabled = not stop_enabled
            btn_relogin.disabled = not relogin_enabled

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
            append_log(f"[{timestamp}] [{state}] {message}")
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
            started = self._runtime_manager.start()
            if not started:
                append_log("[로컬] 이미 실행 중입니다.")
                page.update()

        def on_stop(_: ft.ControlEvent) -> None:
            self._runtime_manager.stop()

        def on_relogin(_: ft.ControlEvent) -> None:
            ok = self._runtime_manager.relogin()
            if not ok:
                append_log("[로컬] 실행 중이 아니라 재로그인할 수 없습니다.")
                page.update()

        btn_start.on_click = on_start
        btn_stop.on_click = on_stop
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
                    ft.Row(controls=[btn_start, btn_stop, btn_relogin], spacing=10),
                    ft.Container(
                        content=ft.Column(
                            controls=[
                                ft.Text("최근 로그 (최대 50개)", weight=ft.FontWeight.BOLD, size=16),
                                ft.Container(
                                    content=log_list,
                                    bgcolor="#FFFFFF",
                                    border=ft.border.all(1, "#D2D2D2"),
                                    border_radius=8,
                                    padding=ft.padding.all(10),
                                    height=520,
                                ),
                            ],
                            spacing=8,
                        ),
                        margin=ft.margin.only(top=14),
                    ),
                ],
                spacing=8,
            ),
            padding=ft.padding.all(8),
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
