"""휴대폰 연결 다이얼로그 — 연결 QR 표시 + 페어링 승인/거절.

대시보드의 UI 헬퍼(safe_page_update/call_page_from_thread)를 주입받아
dashboard_flet_view와의 순환 import를 피한다.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time
from typing import Callable

import flet as ft
import qrcode

from services.phone_link_service import PhoneLinkService

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 1.5


def _qr_png_base64(payload: dict) -> str:
    """페어링 페이로드를 compact JSON QR PNG(base64)로 인코딩한다."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def open_phone_link_dialog(
    *,
    page: ft.Page,
    service: PhoneLinkService,
    safe_update: Callable[[ft.Control], bool],
    call_from_thread: Callable[[Callable[[], None]], None],
) -> None:
    """연결 QR 다이얼로그를 연다. 서버가 꺼져 있으면 먼저 기동한다."""
    payload = service.start()

    qr_image = ft.Image(src_base64=_qr_png_base64(payload), width=280, height=280, fit=ft.ImageFit.CONTAIN)
    addr_text = ft.Text(f"서버 주소: {payload['addr']}", selectable=True, size=12)
    status_text = ft.Text("휴대폰 연결 서버 실행 중", size=12, color=ft.Colors.GREEN_700)
    pending_column = ft.Column(spacing=6)
    empty_pending = ft.Text("승인 대기 중인 폰이 없습니다", size=12, color=ft.Colors.GREY_600)

    closing = threading.Event()
    dialog: ft.AlertDialog | None = None

    def rebuild_pending() -> None:
        pending = service.pending_approvals()
        if not pending:
            pending_column.controls = [empty_pending]
            return
        rows: list[ft.Control] = []
        for item in pending:
            rows.append(
                ft.Row(
                    controls=[
                        ft.Text(item.device_name, expand=True, size=13),
                        ft.FilledButton("승인", on_click=lambda e, t=item.pair_ticket: _approve(t)),
                        ft.OutlinedButton("거절", on_click=lambda e, t=item.pair_ticket: _reject(t)),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            )
        pending_column.controls = rows

    def _approve(ticket: str) -> None:
        service.approve(ticket)
        rebuild_pending()
        safe_update(pending_column)

    def _reject(ticket: str) -> None:
        service.reject(ticket)
        rebuild_pending()
        safe_update(pending_column)

    def _reissue(e: ft.ControlEvent) -> None:
        new_payload = service.reissue_join_code()
        qr_image.src_base64 = _qr_png_base64(new_payload)
        addr_text.value = f"서버 주소: {new_payload['addr']}"
        safe_update(qr_image)
        safe_update(addr_text)

    def _close(e: ft.ControlEvent | None = None) -> None:
        closing.set()
        if dialog is not None:
            dialog.open = False
            safe_update(page)

    def _stop_server(e: ft.ControlEvent) -> None:
        _close()
        service.stop()

    def _poll() -> None:
        while not closing.is_set():
            time.sleep(POLL_INTERVAL_SEC)
            if closing.is_set() or not service.running:
                break

            def refresh() -> None:
                rebuild_pending()

            try:
                call_from_thread(refresh)
                safe_update(pending_column)
            except Exception:
                logger.warning("폰 연결 승인 목록 갱신 실패", exc_info=True)

    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("휴대폰 연결"),
        content=ft.Container(
            width=340,
            content=ft.Column(
                tight=True,
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    qr_image,
                    addr_text,
                    status_text,
                    ft.Divider(),
                    ft.Text("승인 대기", size=13, weight=ft.FontWeight.BOLD),
                    pending_column,
                ],
            ),
        ),
        actions=[
            ft.TextButton("QR 재발급", on_click=_reissue),
            ft.TextButton("서버 중지", on_click=_stop_server),
            ft.FilledButton("닫기", on_click=_close),
        ],
        on_dismiss=lambda e: closing.set(),
    )

    rebuild_pending()
    page.open(dialog)
    threading.Thread(target=_poll, name="phone-link-poll", daemon=True).start()
