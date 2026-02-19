"""QR 스캐너 뷰.

스캐너 창은 앱 수명 동안 유지하고,
디코딩 가능 여부는 상태 플래그로만 제어한다.
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

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


class ScannerView:
    """상시 표시형 QR 스캐너 UI."""

    def __init__(
        self,
        camera_index: int = 0,
        status_font_path: str | None = None,
        status_font_size: int = _DEFAULT_FONT_SIZE,
    ):
        self._camera_index = camera_index
        self._cap: cv2.VideoCapture | None = None

        self._is_running = False
        self._is_scanning_enabled = True
        self._is_auth_ready = False
        self._status_message = "준비됨"

        self._lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None
        self._qr_queue: queue.Queue[str] = queue.Queue(maxsize=10)

        # 동일 QR 재인식은 카메라 화면에서 충분히 사라진 뒤에만 허용한다.
        self._last_emitted_qr = ""
        self._missing_qr_frames = 0
        self._rearm_missing_frames = 8
        self._same_qr_rearmed = True

        self._status_font_path = status_font_path
        self._status_font_size = max(10, int(status_font_size))
        self._status_font = self._load_status_font(self._status_font_path, self._status_font_size)

    def start(self) -> None:
        """백그라운드 스레드에서 캡처/렌더링 루프를 시작한다."""
        if self._is_running:
            return

        self._cap = cv2.VideoCapture(self._camera_index)
        if not self._cap or not self._cap.isOpened():
            print("카메라 열기 실패")
            self._is_running = False
            return

        self._is_running = True
        self._worker_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._worker_thread.start()

    def is_running(self) -> bool:
        return self._is_running

    def set_auth_ready(self, ready: bool) -> None:
        with self._lock:
            self._is_auth_ready = ready

    def is_auth_ready(self) -> bool:
        with self._lock:
            return self._is_auth_ready

    def set_status_font(self, font_path: str | None, font_size: int | None = None) -> None:
        """상태바 렌더링 폰트를 런타임에 변경한다."""
        with self._lock:
            if font_size is not None:
                self._status_font_size = max(10, int(font_size))
            self._status_font_path = font_path
            self._status_font = self._load_status_font(self._status_font_path, self._status_font_size)

    def set_scanning_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._is_scanning_enabled = enabled
            if enabled and self._status_message == "처리 중...":
                self._status_message = "준비됨"
            elif not enabled and self._status_message == "준비됨":
                self._status_message = "처리 중..."

    def set_status_message(self, message: str) -> None:
        with self._lock:
            self._status_message = (message or "").strip() or "준비됨"

    def get_next_qr(self, timeout_sec: float = 0.1) -> str | None:
        try:
            return self._qr_queue.get(timeout=max(0.01, timeout_sec))
        except queue.Empty:
            return None

    def scan_qr(self) -> str | None:
        """하위 호환용 블로킹 래퍼."""
        if not self._is_running:
            self.start()

        while self._is_running:
            code = self.get_next_qr(timeout_sec=0.1)
            if code:
                return code

        return None

    def _capture_loop(self) -> None:
        while self._is_running:
            if not self._cap:
                break

            ret, frame = self._cap.read()
            if not ret:
                self.set_status_message("카메라 프레임 읽기 실패")
                time.sleep(0.05)
                continue

            with self._lock:
                scanning_enabled = self._is_scanning_enabled
                auth_ready = self._is_auth_ready
                status_message = self._status_message
                status_font = self._status_font

            if scanning_enabled and auth_ready:
                qr_codes = decode(frame)
                if qr_codes:
                    qr_url = qr_codes[0].data.decode("utf-8")
                    if self._can_emit_qr(qr_url):
                        if not self._qr_queue.full():
                            self._qr_queue.put(qr_url)
                        self.set_scanning_enabled(False)
                        self.set_status_message("처리 중...")
                        status_message = "처리 중..."
                else:
                    self._record_missing_qr_frame()

            self._draw_status(frame, status_message, scanning_enabled, auth_ready, status_font)
            cv2.imshow("QR Scanner", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                self._is_running = False
                break

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
        frame,
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

        if (
            "FAIL" in message.upper()
            or "ERROR" in message.upper()
            or "실패" in message
            or "오류" in message
        ):
            color = (0, 0, 255)

        bar_height = min(_STATUS_BAR_HEIGHT, frame.shape[0])
        cv2.rectangle(frame, (0, 0), (frame.shape[1], bar_height), (0, 0, 0), -1)

        if status_font is None:
            # OpenCV 기본 폰트는 한글을 지원하지 않으므로 폰트 로드 실패 시 ASCII로 폴백한다.
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
                print(f"SCANNER_FONT_WARN 폰트 로드 실패 path={candidate}: {exc}")

        print("SCANNER_FONT_WARN 사용 가능한 한글 폰트를 찾지 못해 ASCII 폴백을 사용합니다.")
        return None

    def release(self) -> None:
        """카메라 자원을 해제하고 스캐너 창을 닫는다."""
        self._is_running = False

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3)

        if self._cap:
            self._cap.release()
            self._cap = None

        cv2.destroyAllWindows()
