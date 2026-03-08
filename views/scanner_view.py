"""Flet 대시보드를 위한 레거시 스타일(Legacy-style) QR 스캐너 뷰(Scanner view)."""
from __future__ import annotations

import base64
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pyzbar.pyzbar import decode


_STATUS_BAR_HEIGHT = 38
_DEFAULT_FONT_SIZE = 20
_DEFAULT_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\malgunbd.ttf",
    r"C:\Windows\Fonts\gulim.ttc",
)

_STATUS_READY = "준비됨"
_STATUS_PROCESSING = "처리 중..."
_STATUS_CAMERA_READ_FAIL = "카메라 프레임 읽기 실패"
_STATUS_CAMERA_RECONNECTING = "카메라 재연결 중"
_CAMERA_REOPEN_COOLDOWN_SEC = 1.0
_CAMERA_READ_RETRY_SEC = 0.05
RecoveryAction = Literal["none", "focus_pulse", "manual_focus_step"]
CameraStatusListener = Callable[[str | None], None]


def advance_phone_screen_recovery_state(
    *,
    active: bool,
    bright_frame: bool,
    bright_streak: int,
    normal_streak: int,
) -> tuple[bool, int, int, bool, bool]:
    """이전 버전의 임포트(imports)를 위해 유지된 호환성 도우미 파이썬 함수입니다."""
    detected = False
    recovered = False
    if bright_frame:
        bright_streak += 1
        normal_streak = 0
    else:
        normal_streak += 1
        bright_streak = 0
    if active and not bright_frame:
        recovered = True
    if not active and bright_frame:
        detected = True
    return active, bright_streak, normal_streak, detected, recovered


def decide_focus_recovery_action(
    *,
    now: float,
    scanning_enabled: bool,
    auth_ready: bool,
    scan_enabled_since: float,
    last_decode_success_at: float,
    last_focus_pulse_at: float,
    last_manual_focus_step_at: float,
    focus_blur_streak: int,
) -> RecoveryAction:
    """레거시 스캐너는 스캔 중에 초점 변경을 강제하지 않습니다."""
    return "none"


def should_enable_roi_recovery(
    *,
    now: float,
    scanning_enabled: bool,
    auth_ready: bool,
    scan_enabled_since: float,
    last_decode_success_at: float,
    exposure_mode: str,
) -> bool:
    """레거시 스캐너는 원본 프레임(Raw frame)만 읽습니다."""
    return False


def compute_focus_metric(frame: np.ndarray | None) -> float:
    """테스트 및 진단을 위해 유지된 호환성 도우미 함수입니다."""
    if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
        return 0.0
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def build_preview_frame(frame: np.ndarray) -> np.ndarray:
    """추가 복구 필터(Recovery filters) 없이 원본 미리보기 프레임(Raw preview frame)을 반환합니다."""
    return frame.copy()


def build_qr_decode_candidates(
    frame: np.ndarray | None,
    *,
    enable_roi_recovery: bool,
    phone_screen_mode: bool = False,
) -> list[np.ndarray]:
    """레거시 pyzbar 디코딩을 위해 원본 프레임만 반환합니다."""
    if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
        return []
    return [frame]


def split_capture_frame(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """캡처된 프레임에서 독립적인 미리보기/디코드 복사본을 반환합니다."""
    return frame.copy(), frame.copy()


class ScannerView:
    """Flet 미리보기 포워딩 기능(Preview forwarding)이 있는 간단한 원본 프레임(Raw-frame) QR 스캔 루프를 실행합니다."""

    _JPEG_ENCODE_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 70]

    def __init__(
        self,
        camera_index: int = 0,
        status_font_path: str | None = None,
        status_font_size: int = _DEFAULT_FONT_SIZE,
        on_frame_ready: Callable[[str], None] | None = None,
    ):
        self._camera_index = camera_index
        self._cap: cv2.VideoCapture | None = None
        self._camera_backend_name: str | None = None

        self._is_running = False
        self._is_scanning_enabled = True
        self._is_auth_ready = False
        self._runtime_status_message = _STATUS_READY

        self._lock = threading.Lock()
        self._start_complete = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._qr_queue: queue.Queue[str] = queue.Queue(maxsize=10)

        self._last_emitted_qr = ""
        self._missing_qr_frames = 0
        self._rearm_missing_frames = 8
        self._same_qr_rearmed = True

        self._camera_status_message: str | None = None
        self._persistent_camera_status_message: str | None = None
        self._camera_status_listener: CameraStatusListener | None = None
        self._last_notified_camera_status: str | None = None
        self._last_camera_reopen_at = 0.0
        self._active_exposure_mode = "default"
        self._applied_exposure_mode = "default"

        self._status_font_path = status_font_path
        self._status_font_size = max(10, int(status_font_size))
        self._status_font = self._load_status_font(self._status_font_path, self._status_font_size)
        self.on_frame_ready = on_frame_ready

    def start(self) -> None:
        """카메라 캡처 루프를 즉시 시작한다. 카메라 연결은 백그라운드에서 비동기로 수행."""
        if self._is_running:
            return

        self._start_complete.clear()
        self._cap = None
        self._camera_backend_name = None
        self._set_camera_status(_STATUS_CAMERA_RECONNECTING)
        self._is_running = True
        self._worker_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._worker_thread.start()
        self._start_complete.set()

    def wait_until_started(self, timeout: float = 30) -> bool:
        return self._start_complete.wait(timeout=timeout)

    def is_camera_ready(self) -> bool:
        """카메라가 성공적으로 열렸는지 반환한다."""
        return self._cap is not None and self._is_running

    def is_running(self) -> bool:
        return self._is_running

    def set_auth_ready(self, ready: bool) -> None:
        with self._lock:
            self._is_auth_ready = ready
            cap = self._cap if ready else None
        if cap is not None:
            self._enable_autofocus(cap)

    def is_auth_ready(self) -> bool:
        with self._lock:
            return self._is_auth_ready

    def set_status_font(self, font_path: str | None, font_size: int | None = None) -> None:
        with self._lock:
            if font_size is not None:
                self._status_font_size = max(10, int(font_size))
            self._status_font_path = font_path
            self._status_font = self._load_status_font(self._status_font_path, self._status_font_size)

    def set_camera_status_listener(self, listener: CameraStatusListener | None) -> None:
        with self._lock:
            self._camera_status_listener = listener
        self._notify_camera_status_listener_if_changed(force=True)

    def set_scanning_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._is_scanning_enabled = enabled
            if enabled and self._runtime_status_message == _STATUS_PROCESSING:
                self._runtime_status_message = _STATUS_READY
            elif not enabled and self._runtime_status_message == _STATUS_READY:
                self._runtime_status_message = _STATUS_PROCESSING
            cap = self._cap if enabled else None
        if cap is not None:
            self._enable_autofocus(cap)

    def set_status_message(self, message: str) -> None:
        with self._lock:
            self._runtime_status_message = (message or "").strip() or _STATUS_READY

    def get_next_qr(self, timeout_sec: float = 0.1) -> str | None:
        try:
            return self._qr_queue.get(timeout=max(0.01, timeout_sec))
        except queue.Empty:
            return None

    def scan_qr(self) -> str | None:
        """하위 호환성을 유지하기 위한 블로킹 래퍼(Blocking wrapper)입니다."""
        if not self._is_running:
            self.start()

        while self._is_running:
            code = self.get_next_qr(timeout_sec=0.1)
            if code:
                return code

        return None

    def _capture_loop(self) -> None:
        while self._is_running:
            cap = self._cap
            if cap is None:
                if not self._attempt_camera_reopen(time.monotonic(), reason="missing_cap"):
                    time.sleep(_CAMERA_READ_RETRY_SEC)
                continue

            try:
                ret, frame = cap.read()
            except cv2.error as exc:
                print(f"CAMERA_READ_EXCEPTION error={exc}")
                ret, frame = False, None
            except Exception as exc:
                print(f"CAMERA_READ_EXCEPTION error={exc}")
                ret, frame = False, None

            if not ret or frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
                self._set_camera_status(_STATUS_CAMERA_READ_FAIL)
                if not self._attempt_camera_reopen(time.monotonic(), reason="read_failure"):
                    time.sleep(_CAMERA_READ_RETRY_SEC)
                continue

            self._clear_camera_status()
            preview_frame, decode_frame = split_capture_frame(frame)
            preview_frame = build_preview_frame(preview_frame)

            with self._lock:
                scanning_enabled = self._is_scanning_enabled
                auth_ready = self._is_auth_ready
                status_font = self._status_font

            if scanning_enabled and auth_ready:
                qr_url = self._decode_qr(decode_frame)
                if qr_url:
                    if self._can_emit_qr(qr_url):
                        if not self._qr_queue.full():
                            self._qr_queue.put(qr_url)
                        self.set_scanning_enabled(False)
                        self.set_status_message(_STATUS_PROCESSING)
                else:
                    self._record_missing_qr_frame()

            with self._lock:
                status_message = self._current_status_message_locked()
                scanning_enabled = self._is_scanning_enabled
                auth_ready = self._is_auth_ready
                status_font = self._status_font

            self._draw_status(preview_frame, status_message, scanning_enabled, auth_ready, status_font)
            self._emit_preview_frame(preview_frame)

    def _emit_preview_frame(self, frame: np.ndarray) -> None:
        if not self.on_frame_ready:
            return

        try:
            ok, encoded = cv2.imencode(".jpg", frame, self._JPEG_ENCODE_PARAMS)
        except Exception:
            return
        if not ok:
            return

        try:
            b64_str = base64.b64encode(encoded.tobytes()).decode("ascii")
            self.on_frame_ready(b64_str)
        except Exception:
            return

    def _attempt_camera_reopen(self, now: float, *, reason: str) -> bool:
        with self._lock:
            if (now - self._last_camera_reopen_at) < _CAMERA_REOPEN_COOLDOWN_SEC:
                return False
            self._last_camera_reopen_at = now
            old_cap = self._cap

        self._set_camera_status(_STATUS_CAMERA_RECONNECTING)
        new_cap, backend_name = self._open_camera_with_fallback(self._camera_index)
        if new_cap is None:
            print(f"CAMERA_REOPEN_FAIL reason={reason}")
            return False

        with self._lock:
            self._cap = new_cap
            self._camera_backend_name = backend_name

        if old_cap is not None and old_cap is not new_cap:
            try:
                old_cap.release()
            except Exception:
                pass

        self._clear_camera_status()
        print(f"CAMERA_REOPEN_OK reason={reason}")
        return True

    @classmethod
    def _open_camera_with_fallback(
        cls,
        camera_index: int,
    ) -> tuple[cv2.VideoCapture | None, str | None]:
        cap = cls._open_camera(camera_index)
        if cap is None:
            return None, None
        cls._enable_autofocus(cap)
        print("CAMERA_BACKEND_SELECTED backend=DEFAULT")
        return cap, "DEFAULT"

    @staticmethod
    def _open_camera(camera_index: int) -> cv2.VideoCapture | None:
        # DirectShow 백엔드를 우선 사용하여 초기화 속도를 단축한다.
        for backend in (cv2.CAP_DSHOW, None):
            try:
                cap = (
                    cv2.VideoCapture(camera_index, backend)
                    if backend is not None
                    else cv2.VideoCapture(camera_index)
                )
            except Exception:
                continue
            if cap and cap.isOpened():
                if backend is not None:
                    print(f"CAMERA_OPEN_OK backend=DSHOW")
                else:
                    print(f"CAMERA_OPEN_OK backend=DEFAULT_FALLBACK")
                return cap
            try:
                cap.release()
            except Exception:
                pass
        return None

    @classmethod
    def _decode_qr(cls, frame: np.ndarray) -> str | None:
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return None

        try:
            results = decode(frame)
        except Exception:
            return None

        if not results:
            return None

        data = getattr(results[0], "data", b"")
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="ignore").strip()
        else:
            text = str(data).strip()
        if text:
            return text
        return None

    def _reset_focus_recovery_timers(self, now: float | None = None) -> None:
        """레거시 테스트/임포트를 위해 유지된 호환성 No-op(아무 작업도 하지 않음)입니다."""
        return

    def _can_emit_qr(self, qr_url: str) -> bool:
        with self._lock:
            if not qr_url:
                return False

            if qr_url != self._last_emitted_qr:
                self._last_emitted_qr = qr_url
                self._same_qr_rearmed = False
                self._missing_qr_frames = 0
                return True

            if self._same_qr_rearmed:
                self._same_qr_rearmed = False
                self._missing_qr_frames = 0
                return True

            return False

    def _record_missing_qr_frame(self) -> None:
        with self._lock:
            self._missing_qr_frames += 1
            if self._missing_qr_frames >= self._rearm_missing_frames:
                self._same_qr_rearmed = True

    def _draw_status(
        self,
        frame: np.ndarray,
        message: str,
        scanning_enabled: bool,
        auth_ready: bool,
        status_font: ImageFont.FreeTypeFont | None,
    ) -> None:
        if not auth_ready:
            color = (0, 165, 255)
        elif scanning_enabled:
            color = (0, 200, 0)
        else:
            color = (0, 215, 255)

        if "FAIL" in message.upper() or "ERROR" in message.upper() or "실패" in message or "오류" in message:
            color = (0, 0, 255)

        bar_height = min(_STATUS_BAR_HEIGHT, frame.shape[0])
        cv2.rectangle(frame, (0, 0), (frame.shape[1], bar_height), (0, 0, 0), -1)

        if status_font is None:
            ascii_message = message.encode("ascii", "replace").decode("ascii")
            cv2.putText(
                frame,
                ascii_message,
                (10, 26),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                color,
                2,
                cv2.LINE_AA,
            )
            return

        roi_bgr = frame[:bar_height, :, :]
        roi_rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(roi_rgb)
        draw = ImageDraw.Draw(pil_image)
        draw.text((10, 6), message, font=status_font, fill=self._bgr_to_rgb(color))
        rendered_bgr = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        frame[:bar_height, :, :] = rendered_bgr

    def _current_status_message_locked(self) -> str:
        return self._camera_status_message or self._runtime_status_message

    @staticmethod
    def _autofocus_prop() -> int | None:
        return getattr(cv2, "CAP_PROP_AUTOFOCUS", None)

    @classmethod
    def _enable_autofocus(cls, cap: cv2.VideoCapture) -> bool:
        autofocus_prop = cls._autofocus_prop()
        if autofocus_prop is None or not hasattr(cap, "set"):
            return False
        try:
            result = cap.set(autofocus_prop, 1.0)
        except Exception:
            return False
        return True if result is None else bool(result)

    def _update_phone_screen_recovery_state(self, frame: np.ndarray) -> str:
        """제거된 폰 화면 복구 모드에 대한 호환성 No-op입니다."""
        self._active_exposure_mode = "default"
        self._persistent_camera_status_message = None
        return "default"

    def _apply_exposure_mode(self, cap: cv2.VideoCapture, target_mode: str) -> bool:
        """제거된 노출 재정의 경로(Exposure override path)에 대한 호환성 No-op입니다."""
        self._applied_exposure_mode = "default"
        return False

    def _sync_exposure_mode(self) -> None:
        """제거된 노출 동기화 경로(Exposure sync path)에 대한 호환성 No-op입니다."""
        self._applied_exposure_mode = self._active_exposure_mode
        if self._active_exposure_mode != "default":
            print("CAMERA_EXPOSURE_PROFILE_UNSUPPORTED mode=default")

    def _set_camera_status(self, message: str | None) -> None:
        with self._lock:
            self._camera_status_message = message
        self._notify_camera_status_listener_if_changed()

    def _clear_camera_status(self) -> None:
        self._set_camera_status(None)

    def _notify_camera_status_listener_if_changed(self, *, force: bool = False) -> None:
        with self._lock:
            listener = self._camera_status_listener
            message = self._camera_status_message
            if not force and message == self._last_notified_camera_status:
                return
            self._last_notified_camera_status = message

        if listener is None:
            return
        try:
            listener(message)
        except Exception:
            return

    @staticmethod
    def _bgr_to_rgb(color: tuple[int, int, int]) -> tuple[int, int, int]:
        return (color[2], color[1], color[0])

    @staticmethod
    def _load_status_font(font_path: str | None, font_size: int) -> ImageFont.FreeTypeFont | None:
        candidates: list[str] = []
        if font_path:
            candidates.append(font_path)

        for candidate in _DEFAULT_FONT_CANDIDATES:
            if candidate not in candidates:
                candidates.append(candidate)

        for candidate in candidates:
            if not Path(candidate).exists():
                continue
            try:
                return ImageFont.truetype(candidate, font_size)
            except Exception as exc:
                print(f"SCANNER_FONT_WARN font load failed path={candidate}: {exc}")

        print("SCANNER_FONT_WARN 사용할 수 있는 한글 폰트를 찾지 못했습니다. ASCII 상태 텍스트로 대체합니다.")
        return None

    def release(self) -> None:
        self._is_running = False

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3)

        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._camera_backend_name = None
        self._clear_camera_status()
