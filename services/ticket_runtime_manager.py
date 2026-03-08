"""Runtime wrapper to control Application from Flet UI."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from main import Application
from models.order_model import Order


RuntimeCallback = Callable[[str, str, str], None]
OrderCallback = Callable[[Order], None]


@dataclass
class RuntimeEvent:
    state: str
    message: str
    timestamp: str


class TicketRuntimeManager:
    """Starts/stops ticket runtime safely from non-blocking UI."""

    def __init__(self, app_factory: Callable[[], Application] | None = None):
        self._app_factory = app_factory or Application
        self._app: Application | None = None
        self._thread: threading.Thread | None = None
        self._state = "IDLE"
        self._callbacks: list[RuntimeCallback] = []
        self._camera_listener: Callable[[str], None] | None = None
        self._camera_status_listener: Callable[[str | None], None] | None = None
        self._order_listener: OrderCallback | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def set_camera_frame_listener(self, listener: Callable[[str], None]) -> None:
        with self._lock:
            self._camera_listener = listener
            if self._app:
                self._app.set_camera_frame_listener(listener)

    def set_camera_status_listener(self, listener: Callable[[str | None], None] | None) -> None:
        with self._lock:
            self._camera_status_listener = listener
            if self._app:
                set_listener = getattr(self._app, "set_camera_status_listener", None)
                if callable(set_listener):
                    set_listener(listener)

    def set_order_listener(self, listener: OrderCallback | None) -> None:
        """QR 스캔 시 주문 정보를 대시보드로 전달할 리스너를 등록한다."""
        with self._lock:
            self._order_listener = listener
            if self._app:
                self._app.set_order_listener(listener)

    def subscribe(self, callback: RuntimeCallback) -> None:
        with self._lock:
            self._callbacks.append(callback)
            current_state = self._state
        callback(current_state, "상태 구독됨", self._now())

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._emit_locked("RUNNING", "이미 실행 중입니다.")
                return False

            app = self._app_factory()
            app.set_status_listener(self._on_app_status)
            if self._camera_listener:
                app.set_camera_frame_listener(self._camera_listener)
            if self._camera_status_listener:
                set_listener = getattr(app, "set_camera_status_listener", None)
                if callable(set_listener):
                    set_listener(self._camera_status_listener)
            if self._order_listener:
                app.set_order_listener(self._order_listener)
            self._app = app

            thread = threading.Thread(target=self._run_app, daemon=True)
            self._thread = thread
            self._emit_locked("STARTING", "티켓 확인 런타임 시작 요청")

        thread.start()
        return True

    def stop(self, timeout_sec: float = 25.0) -> bool:
        with self._lock:
            thread = self._thread
            app = self._app
            if not thread or not thread.is_alive():
                self._emit_locked("IDLE", "이미 중지 상태입니다.")
                return True
            self._emit_locked("STOPPING", "런타임 중지 요청")

        if app is not None:
            app.request_stop()
        thread.join(timeout=max(0.1, timeout_sec))

        alive = thread.is_alive()
        with self._lock:
            if alive:
                self._emit_locked("ERROR", "중지 시간 초과: 강제 종료가 필요할 수 있습니다.")
            else:
                self._emit_locked("IDLE", "런타임 중지 완료")
        return not alive

    def relogin(self) -> bool:
        with self._lock:
            app = self._app
            if not app or not (self._thread and self._thread.is_alive()):
                self._emit_locked("IDLE", "런타임이 실행 중이 아닙니다.")
                return False
            self._emit_locked("RECOVERING", "재로그인 요청 전송")

        app.request_relogin()
        return True

    def _run_app(self) -> None:
        app: Application | None
        with self._lock:
            app = self._app

        if app is None:
            with self._lock:
                self._emit_locked("ERROR", "런타임 인스턴스가 없습니다.")
            return

        try:
            app.run()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            with self._lock:
                self._emit_locked("ERROR", f"런타임 예외: {exc}")
        finally:
            with self._lock:
                self._app = None
                self._thread = None
                if self._state not in {"ERROR", "IDLE"}:
                    self._emit_locked("IDLE", "런타임 종료")

    def _on_app_status(self, app_state: str, message: str) -> None:
        mapped = self._map_app_state(app_state)
        with self._lock:
            self._emit_locked(mapped, message)

    def _emit_locked(self, state: str, message: str) -> None:
        self._state = state
        timestamp = self._now()
        callbacks = list(self._callbacks)

        for callback in callbacks:
            try:
                callback(state, message, timestamp)
            except Exception:
                continue

    @staticmethod
    def _map_app_state(app_state: str) -> str:
        if app_state == "READY":
            return "RUNNING"
        if app_state == "PROCESSING":
            return "RUNNING"
        if app_state == "AUTH_WAIT":
            return "RECOVERING"
        if app_state == "RECOVERING":
            return "RECOVERING"
        if app_state == "ERROR":
            return "ERROR"
        if app_state == "STARTING":
            return "STARTING"
        if app_state == "STOPPING":
            return "STOPPING"
        if app_state == "STOPPED":
            return "IDLE"
        return "RUNNING"

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
