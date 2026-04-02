"""주문 정보 창 뷰 (Tk UI 스레드 모델)."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import font
from typing import Callable

from models.order_model import Order
from services.receipt_settings_store import ReceiptSettingsStore


_STOP_SENTINEL = object()

# 수동 영수증 출력 콜백 타입
PrintRequestCallback = Callable[[Order], None]


class OrderView:
    """스레드 안전 주문 정보 UI."""

    def __init__(self):
        self._root: tk.Tk | None = None

        self._name_label: tk.Label | None = None
        self._phone_label: tk.Label | None = None
        self._seat_label: tk.Label | None = None
        self._goods_label: tk.Label | None = None
        self._ticket_label: tk.Label | None = None
        self._print_button: tk.Button | None = None

        self._current_order: Order | None = None
        self._on_print_request: PrintRequestCallback | None = None

        self._settings_store = ReceiptSettingsStore(".runtime/receipt_settings.json")
        self._ui_thread: threading.Thread | None = None
        self._ui_ready = threading.Event()
        self._ui_queue: queue.Queue = queue.Queue()

    def set_print_request_callback(self, callback: PrintRequestCallback | None) -> None:
        """영수증 수동 출력 요청 시 호출할 콜백을 등록한다."""
        self._on_print_request = callback

    def start(self) -> None:
        if self._ui_thread and self._ui_thread.is_alive():
            return

        self._ui_ready.clear()
        self._ui_thread = threading.Thread(target=self._ui_loop, daemon=True)
        self._ui_thread.start()
        self._ui_ready.wait(timeout=5)

    def stop(self) -> None:
        if not self._ui_thread or not self._ui_thread.is_alive():
            return

        self._ui_queue.put(_STOP_SENTINEL)
        self._ui_thread.join(timeout=5)

    def show_or_update(self, order: Order) -> None:
        """UI 스레드에서 렌더링하도록 주문 정보를 큐에 넣는다."""
        self.start()
        self._ui_queue.put(order)

    def _ui_loop(self) -> None:
        self._root = tk.Tk()
        self._root.title("주문 정보")
        self._root.geometry("600x700")

        large_font = font.Font(size=20, weight="bold")
        medium_font = font.Font(size=15, weight="bold")

        self._name_label = tk.Label(self._root, text="주문자명:", font=large_font)
        self._name_label.pack(pady=10)

        self._phone_label = tk.Label(self._root, text="주문자연락처:", font=large_font)
        self._phone_label.pack(pady=10)

        self._seat_label = tk.Label(self._root, text="좌석번호:", font=large_font)
        self._seat_label.pack(pady=10)

        self._goods_label = tk.Label(self._root, text="상품:", font=medium_font, justify=tk.LEFT)
        self._goods_label.pack(pady=10)

        self._ticket_label = tk.Label(self._root, text="", font=medium_font, justify=tk.LEFT, fg="#1C8C84")
        self._ticket_label.pack(pady=10)

        # 영수증 수동 출력 버튼
        self._print_button = tk.Button(
            self._root,
            text="영수증 출력",
            font=medium_font,
            command=self._on_print_click,
            state=tk.DISABLED,
        )
        self._print_button.pack(pady=20)

        # 창 닫기 버튼은 무시하고 창을 유지한다.
        self._root.protocol("WM_DELETE_WINDOW", self._ignore_close)
        self._root.withdraw()

        self._ui_ready.set()
        self._poll_queue()
        self._root.mainloop()

        self._root = None
        self._name_label = None
        self._phone_label = None
        self._seat_label = None
        self._goods_label = None
        self._ticket_label = None
        self._print_button = None

    def _poll_queue(self) -> None:
        if not self._root:
            return

        stop_requested = False
        while True:
            try:
                item = self._ui_queue.get_nowait()
            except queue.Empty:
                break

            if item is _STOP_SENTINEL:
                stop_requested = True
                continue

            if isinstance(item, Order):
                self._apply_order(item)

        if stop_requested:
            self._root.destroy()
            return

        self._root.after(50, self._poll_queue)

    def _split_goods_ticket(self, goods: list[str]) -> tuple[list[str], list[str]]:
        """상품 목록을 일반상품/티켓으로 분리한다."""
        try:
            ticket_names = set(self._settings_store.load().ticket_product_names)
        except Exception:
            ticket_names = set()

        general: list[str] = []
        ticket: list[str] = []
        for item in goods:
            product_name = item.rsplit(" x", 1)[0] if " x" in item else item
            if product_name in ticket_names:
                ticket.append(item)
            else:
                general.append(item)
        return general, ticket

    def _apply_order(self, order: Order) -> None:
        if not self._root:
            return

        self._current_order = order

        if self._name_label:
            self._name_label.config(text=f"주문자명: {order.name}")
        if self._phone_label:
            self._phone_label.config(text=f"주문자연락처: {order.phone}")
        if self._seat_label:
            self._seat_label.config(text=f"좌석번호: {order.seat}")

        general, ticket = self._split_goods_ticket(order.goods)
        if self._goods_label:
            general_text = "\n".join(general) if general else "-"
            self._goods_label.config(text=f"상품:\n{general_text}")
        if self._ticket_label:
            if ticket:
                ticket_text = "\n".join(ticket)
                self._ticket_label.config(text=f"티켓:\n{ticket_text}")
                self._ticket_label.pack(before=self._print_button, pady=10)
            else:
                self._ticket_label.config(text="")
                self._ticket_label.pack_forget()

        if self._print_button:
            self._print_button.config(state=tk.NORMAL)

        # 내용에 따라 창 높이 자동 조절
        self._root.deiconify()
        self._root.lift()
        self._root.update_idletasks()
        req_height = self._root.winfo_reqheight()
        min_height = 400
        self._root.geometry(f"600x{max(min_height, req_height)}")

    def _on_print_click(self) -> None:
        """영수증 수동 출력 버튼 클릭 처리."""
        order = self._current_order
        callback = self._on_print_request
        if not order or not callback:
            return

        # 중복 클릭 방지: 버튼 비활성화 후 콜백을 별도 스레드에서 실행
        if self._print_button:
            self._print_button.config(state=tk.DISABLED)

        def _run_print() -> None:
            try:
                callback(order)
            finally:
                if self._root and self._print_button:
                    self._root.after(0, lambda: self._print_button.config(state=tk.NORMAL))

        threading.Thread(target=_run_print, daemon=True).start()

    def _ignore_close(self) -> None:
        if self._root:
            self._root.deiconify()
            self._root.lift()
