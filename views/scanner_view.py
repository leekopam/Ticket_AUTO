"""Flet 대시보드를 위한 레거시 스타일(Legacy-style) QR 스캐너 뷰(Scanner view)."""
from __future__ import annotations

import base64
import logging
import queue
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pyzbar.pyzbar import decode


logger = logging.getLogger(__name__)

_STATUS_BAR_HEIGHT = 52
_DEFAULT_FONT_SIZE = 26
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
_CAMERA_READ_LOG_INTERVAL_SEC = 5.0
_CAMERA_READ_FAILURE_REOPEN_THRESHOLD = 6
_CAMERA_READ_WARMUP_SEC = 2.0
_CAMERA_READ_STALL_REOPEN_SEC = 2.0
_CAMERA_REOPEN_RELEASE_SETTLE_SEC = 0.15
_STATUS_MAX_LINES = 3
_STATUS_TEXT_PADDING_X = 12
_STATUS_TEXT_PADDING_Y = 10
RecoveryAction = Literal["none", "focus_pulse", "manual_focus_step"]
CameraStatusListener = Callable[[str | None], None]
FocusMode = Literal["auto", "manual"]


@dataclass(frozen=True)
class FocusCapability:
    autofocus_supported: bool
    manual_focus_supported: bool
    focus_min: float | None = None
    focus_max: float | None = None
    focus_step: float | None = None


def _coerce_capture_set_result(result: object) -> bool:
    return True if result is None else bool(result)


def _probe_capture_property_support(
    cap: cv2.VideoCapture | object,
    *,
    prop: int | None,
    value: float,
) -> bool:
    if prop is None or cap is None or not hasattr(cap, "set"):
        return False
    try:
        return _coerce_capture_set_result(cap.set(prop, float(value)))
    except Exception:
        return False


def detect_focus_capability(cap: cv2.VideoCapture | object) -> FocusCapability:
    autofocus_supported = getattr(cv2, "CAP_PROP_AUTOFOCUS", None) is not None and hasattr(cap, "set")
    manual_focus_supported = getattr(cv2, "CAP_PROP_FOCUS", None) is not None and hasattr(cap, "set")
    return FocusCapability(
        autofocus_supported=autofocus_supported,
        manual_focus_supported=manual_focus_supported,
    )


def apply_focus_mode(
    cap: cv2.VideoCapture | object,
    capability: FocusCapability,
    *,
    mode: FocusMode,
    manual_focus_value: float | None = None,
) -> bool:
    if cap is None or not hasattr(cap, "set"):
        return False

    autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
    focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)

    if mode == "auto":
        if not capability.autofocus_supported or autofocus_prop is None:
            return False
        try:
            return _coerce_capture_set_result(cap.set(autofocus_prop, 1.0))
        except Exception:
            return False

    if not capability.manual_focus_supported or manual_focus_value is None or focus_prop is None:
        if capability.autofocus_supported and autofocus_prop is not None:
            try:
                _coerce_capture_set_result(cap.set(autofocus_prop, 1.0))
            except Exception:
                pass
        return False

    if capability.autofocus_supported and autofocus_prop is not None:
        try:
            cap.set(autofocus_prop, 0.0)
        except Exception:
            pass

    try:
        applied = _coerce_capture_set_result(cap.set(focus_prop, float(manual_focus_value)))
    except Exception:
        applied = False
    if applied:
        return True
    if capability.autofocus_supported and autofocus_prop is not None:
        try:
            _coerce_capture_set_result(cap.set(autofocus_prop, 1.0))
        except Exception:
            pass
    return False


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


def _measure_status_text_width(
    text: str,
    *,
    status_font: ImageFont.FreeTypeFont | None,
) -> int:
    normalized = (text or "").strip()
    if not normalized:
        return 0
    if status_font is None:
        weighted_units = 0.0
        for char in normalized:
            east_asian_width = unicodedata.east_asian_width(char)
            if char.isspace():
                weighted_units += 0.45
            elif east_asian_width in {"W", "F"}:
                weighted_units += 1.9
            elif east_asian_width == "A":
                weighted_units += 1.4
            else:
                weighted_units += 1.0
        return int(weighted_units * (_DEFAULT_FONT_SIZE * 0.54))
    try:
        bbox = status_font.getbbox(normalized)
        return max(0, int(bbox[2] - bbox[0]))
    except Exception:
        return max(0, int(len(normalized) * (_DEFAULT_FONT_SIZE * 0.6)))


def _truncate_status_line(
    text: str,
    *,
    max_width: int,
    status_font: ImageFont.FreeTypeFont | None,
) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return ""
    if _measure_status_text_width(normalized, status_font=status_font) <= max_width:
        return normalized

    ellipsis = "..."
    truncated = normalized
    while truncated:
        candidate = f"{truncated}{ellipsis}"
        if _measure_status_text_width(candidate, status_font=status_font) <= max_width:
            return candidate
        truncated = truncated[:-1].rstrip()
    return ellipsis


def _wrap_status_message(
    message: str,
    *,
    max_width: int,
    status_font: ImageFont.FreeTypeFont | None,
    max_lines: int = _STATUS_MAX_LINES,
) -> list[str]:
    normalized = " ".join(str(message or "").split())
    if not normalized:
        return []

    if _measure_status_text_width(normalized, status_font=status_font) <= max_width:
        return [normalized]

    word_chunks = [chunk for chunk in normalized.split(" ") if chunk]
    if len(word_chunks) > 1:
        lines: list[str] = []
        current = ""
        for chunk in word_chunks:
            candidate = chunk if not current else f"{current} {chunk}"
            if current and _measure_status_text_width(candidate, status_font=status_font) > max_width:
                lines.append(current.rstrip())
                current = chunk
                if len(lines) >= max_lines:
                    break
                continue
            current = candidate
        if current and len(lines) < max_lines:
            lines.append(current.rstrip())
        if lines:
            remaining_words = word_chunks[len(" ".join(lines).split(" ")) :]
            if remaining_words:
                merged = f"{lines[-1]} {' '.join(remaining_words)}".strip()
                lines[-1] = _truncate_status_line(
                    merged,
                    max_width=max_width,
                    status_font=status_font,
                )
            else:
                lines[-1] = _truncate_status_line(
                    lines[-1],
                    max_width=max_width,
                    status_font=status_font,
                )
            return [line for line in lines[:max_lines] if line]

    lines: list[str] = []
    current = ""
    index = 0

    while index < len(normalized):
        char = normalized[index]
        if not current and char == " ":
            index += 1
            continue

        candidate = f"{current}{char}"
        if current and _measure_status_text_width(candidate, status_font=status_font) > max_width:
            lines.append(current.rstrip())
            current = ""
            if len(lines) >= max_lines:
                break
            continue

        current = candidate
        index += 1

    if current and len(lines) < max_lines:
        lines.append(current.rstrip())

    if not lines:
        return []

    remaining = normalized[index:].strip()
    if remaining:
        merged = f"{lines[-1]} {remaining}".strip()
        lines[-1] = _truncate_status_line(
            merged,
            max_width=max_width,
            status_font=status_font,
        )
    else:
        lines[-1] = _truncate_status_line(
            lines[-1],
            max_width=max_width,
            status_font=status_font,
        )
    return [line for line in lines[:max_lines] if line]


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
    _last_successful_camera_backend_by_index: dict[int, int | None] = {}

    def __init__(
        self,
        camera_index: int = 0,
        status_font_path: str | None = None,
        status_font_size: int = _DEFAULT_FONT_SIZE,
        focus_mode: FocusMode = "auto",
        manual_focus_value: float | None = None,
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
        self._capture_io_lock = threading.Lock()
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
        self._last_camera_reopen_log_at = 0.0
        self._consecutive_read_failures = 0
        self._camera_warmup_until = 0.0
        self._last_successful_frame_at = 0.0
        self._camera_cap_ready_at = 0.0
        self._camera_first_frame_timing_logged = True
        self._active_exposure_mode = "default"
        self._applied_exposure_mode = "default"
        self._focus_mode: FocusMode = "manual" if focus_mode == "manual" and manual_focus_value is not None else "auto"
        self._manual_focus_value: float | None = None if manual_focus_value is None else float(manual_focus_value)
        self._focus_capability: FocusCapability | None = None

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
        self._camera_cap_ready_at = 0.0
        self._camera_first_frame_timing_logged = True
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
            self._configure_focus_for_capture(cap, reprobe=False)

    def is_auth_ready(self) -> bool:
        with self._lock:
            return self._is_auth_ready

    def get_focus_capability(self) -> FocusCapability:
        with self._lock:
            cap = self._cap
            cached = self._focus_capability
        if cap is None:
            return cached or FocusCapability(autofocus_supported=False, manual_focus_supported=False)
        capability = detect_focus_capability(cap)
        with self._lock:
            self._focus_capability = capability
        return capability

    def _configure_focus_for_capture(
        self,
        cap: cv2.VideoCapture | object,
        *,
        reprobe: bool = True,
    ) -> bool:
        if reprobe:
            capability = detect_focus_capability(cap)
        else:
            with self._lock:
                capability = self._focus_capability
            if capability is None:
                capability = detect_focus_capability(cap)
        with self._lock:
            self._focus_capability = capability
            focus_mode = self._focus_mode
            manual_focus_value = self._manual_focus_value

        with self._capture_io_lock:
            applied = apply_focus_mode(
                cap,
                capability,
                mode=focus_mode,
                manual_focus_value=manual_focus_value,
            )
        return applied

    def set_focus_mode(self, mode: FocusMode) -> bool:
        normalized_mode: FocusMode = "manual" if mode == "manual" else "auto"
        with self._lock:
            self._focus_mode = normalized_mode
            cap = self._cap
            capability = self._focus_capability
            manual_focus_value = self._manual_focus_value

        if cap is None:
            return normalized_mode == "auto" or manual_focus_value is not None

        if capability is None:
            capability = detect_focus_capability(cap)
            with self._lock:
                self._focus_capability = capability

        with self._capture_io_lock:
            applied = apply_focus_mode(
                cap,
                capability,
                mode=normalized_mode,
                manual_focus_value=manual_focus_value,
            )
        logger.info("초점 모드 변경: mode=%s, value=%s, applied=%s", normalized_mode, manual_focus_value, applied)
        return applied

    def set_manual_focus_value(self, value: float | None) -> bool:
        normalized_value = None if value is None else float(value)
        with self._lock:
            self._manual_focus_value = normalized_value
            cap = self._cap
            focus_mode = self._focus_mode
            capability = self._focus_capability

        if cap is None or focus_mode != "manual":
            return True

        if capability is None:
            capability = detect_focus_capability(cap)
            with self._lock:
                self._focus_capability = capability

        with self._capture_io_lock:
            applied = apply_focus_mode(
                cap,
                capability,
                mode="manual",
                manual_focus_value=normalized_value,
            )
        logger.info("수동 초점 값 변경: value=%s, applied=%s", normalized_value, applied)
        return applied

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
            self._configure_focus_for_capture(cap, reprobe=False)

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
                with self._capture_io_lock:
                    ret, frame = cap.read()
            except cv2.error as exc:
                print(f"CAMERA_READ_EXCEPTION error={exc}")
                ret, frame = False, None
            except Exception as exc:
                print(f"CAMERA_READ_EXCEPTION error={exc}")
                ret, frame = False, None

            if not ret or frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
                now = time.monotonic()
                with self._lock:
                    warmup_active = now < self._camera_warmup_until
                    last_successful_frame_at = self._last_successful_frame_at
                    if warmup_active:
                        self._consecutive_read_failures = 0
                        consecutive_read_failures = 0
                    else:
                        self._consecutive_read_failures += 1
                        consecutive_read_failures = self._consecutive_read_failures
                    sustained_stall = (
                        last_successful_frame_at <= 0.0
                        or (now - last_successful_frame_at) >= _CAMERA_READ_STALL_REOPEN_SEC
                    )
                self._set_camera_status(
                    _STATUS_CAMERA_RECONNECTING if warmup_active else _STATUS_CAMERA_READ_FAIL
                )
                if (
                    consecutive_read_failures >= _CAMERA_READ_FAILURE_REOPEN_THRESHOLD
                    and sustained_stall
                ):
                    if not self._attempt_camera_reopen(now, reason="read_failure"):
                        time.sleep(_CAMERA_READ_RETRY_SEC)
                else:
                    time.sleep(_CAMERA_READ_RETRY_SEC)
                continue

            frame_success_at = time.monotonic()
            with self._lock:
                self._consecutive_read_failures = 0
                self._camera_warmup_until = 0.0
                self._last_successful_frame_at = frame_success_at
                cap_ready_at = self._camera_cap_ready_at
                backend_name = self._camera_backend_name or "UNKNOWN"
                should_log_first_frame = (
                    cap_ready_at > 0.0 and not self._camera_first_frame_timing_logged
                )
                if should_log_first_frame:
                    self._camera_first_frame_timing_logged = True
            if should_log_first_frame:
                first_frame_ms = max(0.0, (frame_success_at - cap_ready_at) * 1000.0)
                print(
                    "CAMERA_FIRST_FRAME_TIMING "
                    f"camera_index={self._camera_index} "
                    f"backend={backend_name} "
                    f"first_frame_ms={first_frame_ms:.1f}"
                )
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
        reopen_start = time.perf_counter()
        release_ms = 0.0
        with self._lock:
            if (now - self._last_camera_reopen_at) < _CAMERA_REOPEN_COOLDOWN_SEC:
                return False
            self._last_camera_reopen_at = now
            old_cap = self._cap
            self._cap = None
            self._camera_backend_name = None
            self._camera_cap_ready_at = 0.0
            self._camera_first_frame_timing_logged = True

        self._set_camera_status(_STATUS_CAMERA_RECONNECTING)
        if old_cap is not None:
            release_start = time.perf_counter()
            try:
                with self._capture_io_lock:
                    old_cap.release()
            except Exception:
                pass
            time.sleep(_CAMERA_REOPEN_RELEASE_SETTLE_SEC)
            release_ms = (time.perf_counter() - release_start) * 1000.0

        verbose_open = reason != "read_failure"
        open_start = time.perf_counter()
        try:
            new_cap, backend_name = self._open_camera_with_fallback(self._camera_index, verbose=verbose_open)
        except TypeError as exc:
            if "verbose" not in str(exc):
                raise
            new_cap, backend_name = self._open_camera_with_fallback(self._camera_index)
        open_ms = (time.perf_counter() - open_start) * 1000.0
        if new_cap is None:
            total_ms = (time.perf_counter() - reopen_start) * 1000.0
            print(
                "CAMERA_REOPEN_TIMING "
                f"reason={reason} outcome=fail camera_index={self._camera_index} "
                f"backend=NONE release_ms={release_ms:.1f} open_ms={open_ms:.1f} "
                f"cap_ready_ms=0.0 focus_ms=0.0 total_ms={total_ms:.1f}"
            )
            self._log_camera_reopen_event(now, f"CAMERA_REOPEN_FAIL reason={reason}")
            return False

        cap_ready_perf = time.perf_counter()
        cap_ready_at = time.monotonic()
        cap_ready_ms = (cap_ready_perf - reopen_start) * 1000.0
        with self._lock:
            self._cap = new_cap
            self._camera_backend_name = backend_name
            self._consecutive_read_failures = 0
            self._camera_warmup_until = cap_ready_at + _CAMERA_READ_WARMUP_SEC
            self._last_successful_frame_at = 0.0
            self._camera_cap_ready_at = cap_ready_at
            self._camera_first_frame_timing_logged = False

        self._clear_camera_status()
        focus_start = time.perf_counter()
        focus_applied = self._configure_focus_for_capture(new_cap)
        focus_ms = (time.perf_counter() - focus_start) * 1000.0
        total_ms = (time.perf_counter() - reopen_start) * 1000.0
        selected_backend = backend_name or "UNKNOWN"
        print(
            "CAMERA_REOPEN_TIMING "
            f"reason={reason} outcome=ok camera_index={self._camera_index} "
            f"backend={selected_backend} release_ms={release_ms:.1f} "
            f"open_ms={open_ms:.1f} cap_ready_ms={cap_ready_ms:.1f} "
            f"focus_ms={focus_ms:.1f} total_ms={total_ms:.1f} "
            f"focus_applied={bool(focus_applied)}"
        )
        self._log_camera_reopen_event(now, f"CAMERA_REOPEN_OK reason={reason} backend={selected_backend}")
        return True

    @classmethod
    def _open_camera_with_fallback(
        cls,
        camera_index: int,
        *,
        verbose: bool = True,
    ) -> tuple[cv2.VideoCapture | None, str | None]:
        cap, backend_name = cls._open_camera(camera_index, verbose=verbose)
        if cap is None:
            return None, None
        if verbose:
            print(f"CAMERA_BACKEND_SELECTED camera_index={camera_index} backend={backend_name}")
        return cap, backend_name

    @staticmethod
    def _camera_backend_name(backend: int | None) -> str:
        return "DSHOW" if backend is not None else "DEFAULT_FALLBACK"

    @classmethod
    def _camera_backend_attempt_order(cls, camera_index: int) -> tuple[int | None, ...]:
        default_order: tuple[int | None, ...] = (cv2.CAP_DSHOW, None)
        if camera_index not in cls._last_successful_camera_backend_by_index:
            return default_order
        cached_backend = cls._last_successful_camera_backend_by_index[camera_index]
        return (cached_backend, *tuple(backend for backend in default_order if backend != cached_backend))

    @classmethod
    def _open_camera(
        cls,
        camera_index: int,
        *,
        verbose: bool = True,
    ) -> tuple[cv2.VideoCapture | None, str | None]:
        # DirectShow 백엔드를 우선 사용하되, 같은 카메라에서 직전 성공 백엔드가 있으면 먼저 재시도한다.
        for backend in cls._camera_backend_attempt_order(camera_index):
            backend_name = cls._camera_backend_name(backend)
            constructor_error = False
            is_opened_error = False
            buffer_error = False
            cap = None

            constructor_start = time.perf_counter()
            try:
                cap = (
                    cv2.VideoCapture(camera_index, backend)
                    if backend is not None
                    else cv2.VideoCapture(camera_index)
                )
            except Exception:
                constructor_error = True
            constructor_ms = (time.perf_counter() - constructor_start) * 1000.0

            opened = False
            is_opened_ms = 0.0
            if cap is not None:
                is_opened_start = time.perf_counter()
                try:
                    opened = bool(cap.isOpened())
                except Exception:
                    opened = False
                    is_opened_error = True
                is_opened_ms = (time.perf_counter() - is_opened_start) * 1000.0

            buffer_ms = 0.0
            release_ms = 0.0
            if cap is not None and opened:
                buffer_start = time.perf_counter()
                try:
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    buffer_error = True
                buffer_ms = (time.perf_counter() - buffer_start) * 1000.0
                cls._last_successful_camera_backend_by_index[camera_index] = backend
                if verbose:
                    print(
                        "CAMERA_OPEN_TIMING "
                        f"camera_index={camera_index} backend={backend_name} opened=true "
                        f"constructor_ms={constructor_ms:.1f} is_opened_ms={is_opened_ms:.1f} "
                        f"buffer_ms={buffer_ms:.1f} release_ms=0.0 "
                        f"constructor_error={constructor_error} is_opened_error={is_opened_error} "
                        f"buffer_error={buffer_error}"
                    )
                    print(f"CAMERA_OPEN_OK camera_index={camera_index} backend={backend_name}")
                return cap, backend_name

            if cap is not None:
                release_start = time.perf_counter()
                try:
                    cap.release()
                except Exception:
                    pass
                release_ms = (time.perf_counter() - release_start) * 1000.0

            if verbose:
                print(
                    "CAMERA_OPEN_TIMING "
                    f"camera_index={camera_index} backend={backend_name} opened=false "
                    f"constructor_ms={constructor_ms:.1f} is_opened_ms={is_opened_ms:.1f} "
                    f"buffer_ms={buffer_ms:.1f} release_ms={release_ms:.1f} "
                    f"constructor_error={constructor_error} is_opened_error={is_opened_error} "
                    f"buffer_error={buffer_error}"
                )
        return None, None

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

    def _log_camera_reopen_event(self, now: float, message: str) -> None:
        with self._lock:
            if (now - self._last_camera_reopen_log_at) < _CAMERA_READ_LOG_INTERVAL_SEC:
                return
            self._last_camera_reopen_log_at = now
        print(message)

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

        max_text_width = max(40, frame.shape[1] - (_STATUS_TEXT_PADDING_X * 2) - 120)
        lines = _wrap_status_message(
            message,
            max_width=max_text_width,
            status_font=status_font,
        )
        if not lines:
            lines = [message]

        if status_font is None:
            line_height = 20
        else:
            try:
                line_height = max(20, int(status_font.getbbox("한글Ag")[3] - status_font.getbbox("한글Ag")[1]) + 4)
            except Exception:
                line_height = max(20, self._status_font_size + 4)

        bar_height = min(
            frame.shape[0],
            max(
                _STATUS_BAR_HEIGHT,
                (_STATUS_TEXT_PADDING_Y * 2) + (len(lines) * line_height),
            ),
        )
        cv2.rectangle(frame, (0, 0), (frame.shape[1], bar_height), (0, 0, 0), -1)

        if status_font is None:
            for index, line in enumerate(lines):
                ascii_message = line.encode("ascii", "replace").decode("ascii")
                baseline_y = _STATUS_TEXT_PADDING_Y + 25 + (index * line_height)
                cv2.putText(
                    frame,
                    ascii_message,
                    (_STATUS_TEXT_PADDING_X, min(bar_height - 8, baseline_y)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    (0, 0, 0),
                    3,
                    cv2.LINE_AA,
                )
                cv2.putText(
                    frame,
                    ascii_message,
                    (_STATUS_TEXT_PADDING_X, min(bar_height - 8, baseline_y)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.9,
                    color,
                    2,
                    cv2.LINE_AA,
                )
            return

        roi_bgr = frame[:bar_height, :, :]
        roi_rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(roi_rgb)
        draw = ImageDraw.Draw(pil_image)
        for index, line in enumerate(lines):
            draw.text(
                (_STATUS_TEXT_PADDING_X, _STATUS_TEXT_PADDING_Y + (index * line_height)),
                line,
                font=status_font,
                fill=self._bgr_to_rgb(color),
                stroke_width=1,
                stroke_fill=(0, 0, 0),
            )
        rendered_bgr = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        frame[:bar_height, :, :] = rendered_bgr

    def _current_status_message_locked(self) -> str:
        return self._camera_status_message or self._runtime_status_message

    @staticmethod
    def _autofocus_prop() -> int | None:
        return getattr(cv2, "CAP_PROP_AUTOFOCUS", None)

    @classmethod
    def _enable_autofocus(cls, cap: cv2.VideoCapture) -> bool:
        return apply_focus_mode(
            cap,
            FocusCapability(
                autofocus_supported=cls._autofocus_prop() is not None,
                manual_focus_supported=False,
            ),
            mode="auto",
        )

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

    def change_camera(self, new_index: int) -> None:
        """런타임 중 카메라 디바이스를 변경한다. capture_loop가 자동으로 새 인덱스로 재연결한다."""
        with self._lock:
            if self._camera_index == new_index:
                return
            self._camera_index = new_index
            old_cap = self._cap
            self._cap = None
            self._camera_backend_name = None
            self._camera_cap_ready_at = 0.0
            self._camera_first_frame_timing_logged = True

        self._set_camera_status(_STATUS_CAMERA_RECONNECTING)
        if old_cap is not None:
            try:
                with self._capture_io_lock:
                    old_cap.release()
            except Exception:
                pass

    def release(self) -> None:
        self._is_running = False

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3)

        if self._cap:
            try:
                with self._capture_io_lock:
                    self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._camera_backend_name = None
        self._camera_cap_ready_at = 0.0
        self._camera_first_frame_timing_logged = True
        self._clear_camera_status()
