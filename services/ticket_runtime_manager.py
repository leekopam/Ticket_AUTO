"""Runtime wrapper to control Application from Flet UI."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from main import Application
from models.order_model import Order

logger = logging.getLogger(__name__)


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
        self._lock = threading.RLock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def _forward_listener_to_running_app(
        self,
        method_name: str,
        listener: object,
        warning_message: str,
    ) -> None:
        with self._lock:
            app = self._app
        self._apply_listener_safely(
            app,
            method_name,
            listener,
            warning_message,
        )

    def _apply_listener_safely(
        self,
        app: object | None,
        method_name: str,
        listener: object,
        warning_message: str,
    ) -> None:
        if not app:
            return
        setter = getattr(app, method_name, None)
        if not callable(setter):
            return
        try:
            setter(listener)
        except Exception:
            logger.warning(warning_message, exc_info=True)

    def set_camera_frame_listener(self, listener: Callable[[str], None]) -> None:
        with self._lock:
            self._camera_listener = listener
        self._forward_listener_to_running_app(
            "set_camera_frame_listener",
            listener,
            "카메라 프레임 리스너 전달 실패",
        )

    def set_camera_status_listener(self, listener: Callable[[str | None], None] | None) -> None:
        with self._lock:
            self._camera_status_listener = listener
        self._forward_listener_to_running_app(
            "set_camera_status_listener",
            listener,
            "카메라 상태 리스너 전달 실패",
        )

    def set_order_listener(self, listener: OrderCallback | None) -> None:
        """QR 스캔 시 주문 정보를 대시보드로 전달할 리스너를 등록한다."""
        with self._lock:
            self._order_listener = listener
        self._forward_listener_to_running_app(
            "set_order_listener",
            listener,
            "주문 리스너 전달 실패",
        )

    def change_camera(self, new_index: int) -> None:
        """실행 중인 Application의 카메라 디바이스를 변경한다."""
        with self._lock:
            app = self._app
        if app is not None:
            try:
                app.change_camera(new_index)
            except Exception:
                logger.warning("카메라 변경 실패", exc_info=True)

    def apply_scanner_focus_settings(self, focus_mode: str, manual_focus_value: float | None) -> str:
        """실행 중인 런타임이 있으면 스캐너 초점 설정을 즉시 반영한다."""
        normalized_mode = "manual" if focus_mode == "manual" else "auto"
        normalized_value = None if manual_focus_value is None else float(manual_focus_value)

        with self._lock:
            app = self._app
            thread = self._thread

        if not app or not thread or not thread.is_alive():
            return "카메라 초점 설정 저장 완료 (다음 앱 시작 후 적용)"

        apply_method = getattr(app, "apply_scanner_focus_settings", None)
        if not callable(apply_method):
            return "카메라 초점 설정 저장 완료 (다음 앱 시작 후 적용)"

        try:
            return str(apply_method(normalized_mode, normalized_value))
        except Exception:
            logger.warning("카메라 초점 설정 런타임 적용 실패", exc_info=True)
            return "카메라 초점 설정 저장 완료 (현재 런타임 적용 실패, 다음 앱 시작 후 적용)"

    def get_scanner_focus_capability(self):
        """실행 중인 scanner의 초점 capability를 안전하게 조회한다."""
        with self._lock:
            app = self._app
            thread = self._thread

        if not app or not thread or not thread.is_alive():
            return None

        getter = getattr(app, "get_scanner_focus_capability", None)
        if not callable(getter):
            return None

        try:
            return getter()
        except Exception:
            logger.warning("移대찓??珥덉젏 capability 議고쉶 ?ㅽ뙣", exc_info=True)
            return None

    def subscribe(self, callback: RuntimeCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                return
            self._callbacks.append(callback)
            current_state = self._state
        try:
            callback(current_state, "상태 구독됨", self._now())
        except Exception:
            logger.warning("상태 구독 초기 콜백 처리 실패", exc_info=True)

    def unsubscribe(self, callback: RuntimeCallback) -> None:
        with self._lock:
            self._callbacks = [registered for registered in self._callbacks if registered is not callback]

    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._emit_locked("RUNNING", "이미 실행 중입니다.")
                return False

            try:
                app = self._app_factory()
                app.set_status_listener(self._on_app_status)
                self._apply_listener_safely(
                    app,
                    "set_camera_frame_listener",
                    self._camera_listener,
                    "카메라 프레임 리스너 전달 실패",
                )
                self._apply_listener_safely(
                    app,
                    "set_camera_status_listener",
                    self._camera_status_listener,
                    "카메라 상태 리스너 전달 실패",
                )
                self._apply_listener_safely(
                    app,
                    "set_order_listener",
                    self._order_listener,
                    "주문 리스너 전달 실패",
                )
            except Exception as exc:
                logger.exception("런타임 초기화 실패")
                self._app = None
                self._thread = None
                self._emit_locked("ERROR", f"런타임 시작 실패: {exc}")
                return False

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

        stop_error: Exception | None = None
        if app is not None:
            try:
                app.request_stop()
            except Exception as exc:
                stop_error = exc
                logger.exception("런타임 중지 요청 실패")
        thread.join(timeout=max(0.1, timeout_sec))

        alive = thread.is_alive()
        with self._lock:
            if alive:
                if stop_error is not None:
                    self._emit_locked("ERROR", f"런타임 중지 요청 실패: {stop_error}")
                else:
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

        try:
            app.request_relogin()
        except Exception as exc:
            logger.exception("재로그인 요청 실패")
            with self._lock:
                self._emit_locked("ERROR", f"재로그인 요청 실패: {exc}")
            return False
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
            logger.exception("런타임 예외")
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
                logger.warning("상태 콜백 처리 실패", exc_info=True)
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
