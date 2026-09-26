"""오프라인 E2E 테스트에서 공용으로 사용하는 스텁/대역과 계측 유틸."""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from openpyxl import Workbook
from PIL import Image

import main as app_main
from models.receipt_settings_model import ReceiptSettings
from models.ticket_debug_settings_model import TicketDebugSettings
from services.api_service import ApiService
from services.browser_service import (
    BrowserResolveResult,
    PageOrderDiscoveryResult,
    ReceiptClickResult,
)
from services.excel_service import ExcelService
from services.qr_generator_service import generate_qr_image
from viewmodels.order_viewmodel import OrderViewModel
from views.scanner_view import ScannerView


TEST_ORDER_NUMBER = "WFLM7QSDTC_69D53CU23685"
TEST_QR_URL = (
    "https://witchform.com/qrcode_link.php"
    f"?test_order={TEST_ORDER_NUMBER}"
)


# Boogle 실행 계약: 테스트 실행 중 BOOGLE_ARTIFACTS_DIR에 쓴 파일은
# 세션 종료 시 tests/conftest.py의 훅이 manifest와 함께 제출한다.
def _boogle_artifacts_dir() -> Path | None:
    raw = os.environ.get("BOOGLE_ARTIFACTS_DIR")
    return Path(raw) if raw else None


def _sanitize_artifact_name(name: str) -> str:
    """artifact 파일명을 Boogle 확장자/이름 규칙에 맞게 정규화한다."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem) or "artifact"
    ext = Path(name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        ext = ".bin"
    return f"{stem}{ext}"


def save_artifact_bytes(name: str, data: bytes) -> Path | None:
    """BOOGLE_ARTIFACTS_DIR가 있으면 파일을 기록하고 경로를 반환한다."""
    target_dir = _boogle_artifacts_dir()
    if target_dir is None:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _sanitize_artifact_name(name)
    target.write_bytes(data)
    return target


def save_artifact_image(name: str, image: Image.Image) -> Path | None:
    """PIL 이미지를 PNG artifact로 제출한다."""
    target_dir = _boogle_artifacts_dir()
    if target_dir is None:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _sanitize_artifact_name(name)
    image.save(target, format="PNG")
    return target


# 세션 전체에서 누적되는 계측 카운터. conftest의 Boogle 훅이 metric으로 변환한다.
_METRIC_COUNTERS: Counter[str] = Counter()


def record_metric(name: str, amount: int = 1) -> None:
    """E2E 계측 카운터를 누적한다."""
    _METRIC_COUNTERS[name] += amount


def metric_snapshot() -> dict[str, int]:
    """현재까지 누적된 계측 카운터 사본을 반환한다."""
    return dict(_METRIC_COUNTERS)


def reset_metrics() -> None:
    """계측 카운터를 초기화한다(세션 시작 시 1회)."""
    _METRIC_COUNTERS.clear()


def create_test_workbook(path: Path) -> None:
    """테스트 주문 1건이 들어간 최소 data 워크북을 생성한다."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        [
            "주문번호",
            "주문자명",
            "주문자연락처",
            "좌석번호",
            "수령확인",
            "주문상태",
            "처리시간",
            "[상품1] 테스트 상품",
        ]
    )
    worksheet.append(
        [TEST_ORDER_NUMBER, "테스트 사용자", "010-0000-0000", "A-001", "", "거래중", "", 1]
    )
    workbook.save(path)
    workbook.close()


def decode_generated_qr(payload: str) -> str | None:
    """실제 QR 이미지 생성→OpenCV 디코딩 왕복으로 payload를 검증한다."""
    qr_image = generate_qr_image(payload, output_px=600)
    rgb_frame = np.asarray(qr_image.convert("RGB"))
    bgr_frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
    return ScannerView._decode_qr(bgr_frame)


def build_offline_app(
    data_path: Path,
    *,
    offline_scan_mode: bool = True,
    browser: "FakeBrowserService | None" = None,
    scanner: "FakeScannerView | None" = None,
):
    """실제 네트워크/장비 없이 Application을 조립한다."""
    excel_service = ExcelService(str(data_path))
    browser_service = browser or FakeBrowserService()
    settings = ReceiptSettings(show_qr=False, qr_scan_auto_print_enabled=True)
    sound_service = MemoryScanSuccessSoundService()
    scanner_view = scanner or FakeScannerView()

    app = app_main.Application.__new__(app_main.Application)
    app._state = app_main.AppState.READY
    app._excel_service = excel_service
    app._browser_service = browser_service
    app._api_service = ApiService()
    app._order_viewmodel = OrderViewModel(excel_service, browser_service)
    app._receipt_settings = settings
    app._settings_store = MemoryReceiptSettingsStore(settings)
    app._scan_success_sound_service = sound_service
    app._audio_service = None
    app._ticket_debug_tools_service = OfflineDebugToolsService(
        offline_scan_mode=offline_scan_mode
    )
    app._scanner_view = scanner_view
    app._order_view = None
    app._order_listener = None
    app._status_listener = None
    app._stop_requested = False
    app._relogin_requested = False
    return app, browser_service, scanner_view, sound_service


@dataclass(frozen=True)
class CapturedPrintJob:
    """프린터 대역에 기록된 인쇄 작업."""

    image: Image.Image
    printer_name: str | None
    job_name: str


class FakePrinterBackend:
    """영수증 이미지를 메모리에 보관하고 선택적으로 실패를 재현한다."""

    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.jobs: list[CapturedPrintJob] = []

    def print_image(
        self,
        image: Image.Image,
        printer_name: str | None,
        job_name: str,
    ) -> None:
        self.jobs.append(
            CapturedPrintJob(
                image=image.copy(),
                printer_name=printer_name,
                job_name=job_name,
            )
        )
        record_metric("print_jobs")
        # 영수증 렌더 결과를 실행 evidence로 제출한다(민감정보 없는 테스트 주문만 사용).
        save_artifact_image(f"receipt-{job_name}.png", image)
        if self.failure is not None:
            record_metric("print_failures")
            raise self.failure


class FakePrinterQueue:
    """L3 스텁용 프린터 큐: 작업 적재/성공/실패 상태를 검증한다."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.pending: list[CapturedPrintJob] = []
        self.completed: list[CapturedPrintJob] = []
        self.failed: list[CapturedPrintJob] = []
        self.failure = failure

    def submit(self, image: Image.Image, job_name: str, printer_name: str | None = None) -> None:
        self.pending.append(
            CapturedPrintJob(image=image, printer_name=printer_name, job_name=job_name)
        )

    def run_next(self) -> bool:
        """대기 중인 첫 작업을 실행한다. 실패 시 작업을 failed로 이동한다."""
        if not self.pending:
            return False
        job = self.pending.pop(0)
        record_metric("print_jobs")
        if self.failure is not None:
            self.failed.append(job)
            record_metric("print_failures")
            return False
        self.completed.append(job)
        save_artifact_image(f"receipt-{job.job_name}.png", job.image)
        return True

    def run_all(self) -> int:
        """모든 대기 작업을 실행하고 성공 건수를 반환한다."""
        succeeded = 0
        while self.pending:
            if self.run_next():
                succeeded += 1
        return succeeded


class StillCameraStub:
    """L3 스텁용 스틸 카메라: 고정 QR 프레임을 반환한다."""

    def __init__(self, payload: str = TEST_QR_URL) -> None:
        qr_image = generate_qr_image(payload, output_px=600)
        rgb_frame = np.asarray(qr_image.convert("RGB"))
        self._frame = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
        self.frames_read = 0

    def read(self) -> np.ndarray:
        """저장된 스틸 프레임 사본을 반환한다."""
        self.frames_read += 1
        return self._frame.copy()

    def decode_next(self) -> str | None:
        """프레임을 읽어 실제 OpenCV 디코더로 QR payload를 추출한다."""
        return ScannerView._decode_qr(self.read())


class FakeBrowserService:
    """오프라인 E2E에서 윗치폼 접근 결과를 스크립트로 재현한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.resolve_results: list[BrowserResolveResult] = []
        self.click_results: list[ReceiptClickResult] = []
        self.discovery = PageOrderDiscoveryResult()
        self.auth_results: list[bool] = []
        self.login_url = "https://witchform.com/w/login"
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def open_page(self, url: str, *, preserve_current_page: bool = False) -> bool:
        self.calls.append(("open_page", url))
        return True

    def resolve_qr_redirect(self, url: str) -> BrowserResolveResult:
        self.calls.append(("resolve_qr_redirect", url))
        record_metric("resolve_redirects")
        if self.resolve_results:
            return self.resolve_results.pop(0)
        return BrowserResolveResult(ok=False, error_code="UNEXPECTED", error_message="미지정 결과")

    def discover_order_context_from_page(self, url: str) -> PageOrderDiscoveryResult:
        self.calls.append(("discover_order_context_from_page", url))
        return self.discovery

    def click_receipt_button(self) -> ReceiptClickResult:
        self.calls.append(("click_receipt_button", ""))
        if self.click_results:
            return self.click_results.pop(0)
        return ReceiptClickResult(success=True)

    def wait_until_authenticated(self, timeout_sec: int = 1) -> bool:
        self.calls.append(("wait_until_authenticated", ""))
        record_metric("recovery_attempts")
        if self.auth_results:
            return self.auth_results.pop(0)
        return False

    def request_relogin(self) -> bool:
        self.calls.append(("request_relogin", ""))
        record_metric("recovery_attempts")
        return True


class FakeScannerView:
    """화면과 카메라 없이 상태 전이와 QR 입력만 재현한다."""

    def __init__(self) -> None:
        self.auth_ready = True
        self.scanning_enabled = True
        self.status_message = ""
        self.status_history: list[str] = []
        self.qr_queue: list[str] = []
        self._running = False
        self._camera_ready = True

    def push_qr(self, url: str) -> None:
        self.qr_queue.append(url)

    def start(self) -> None:
        self._running = True

    def release(self) -> None:
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def is_camera_ready(self) -> bool:
        return self._camera_ready

    def get_next_qr(self, timeout_sec: float = 0.1) -> str:
        if self.qr_queue:
            return self.qr_queue.pop(0)
        # 큐 소진 시 자동으로 런타임 종료를 재현한다
        self._running = False
        return ""

    def set_auth_ready(self, ready: bool) -> None:
        self.auth_ready = ready

    def set_scanning_enabled(self, enabled: bool) -> None:
        self.scanning_enabled = enabled

    def set_status_message(self, message: str) -> None:
        self.status_message = message
        self.status_history.append(message)
        record_metric("status_transitions")


class MemoryReceiptSettingsStore:
    """파일 없이 메모리에 영수증 설정을 보관한다."""

    def __init__(self, settings: ReceiptSettings) -> None:
        self._settings = settings
        self.saved: list[ReceiptSettings] = []

    def load(self) -> ReceiptSettings:
        return self._settings

    def save(self, settings: ReceiptSettings) -> None:
        self._settings = settings
        self.saved.append(settings)


class MemoryScanSuccessSoundService:
    """실제 소리를 재생하지 않고 성공 카운트만 보관한다."""

    def __init__(self) -> None:
        self.success_count = 0
        self.play_calls: list[tuple[str, bool, bool]] = []

    def play_for_scan_success(
        self,
        settings: ReceiptSettings,
        *,
        order_number: str,
        increment_count: bool,
        persist_count: bool,
    ) -> None:
        self.play_calls.append((order_number, increment_count, persist_count))
        if increment_count and persist_count:
            self.success_count += 1

    def load_success_count(self) -> int:
        return self.success_count

    def save_success_count(self, count: int) -> None:
        self.success_count = int(count)


class OfflineDebugToolsService:
    """네트워크 없이 디버그 모드 플래그를 재현한다."""

    def __init__(
        self,
        *,
        offline_scan_mode: bool = True,
        count_scan_success_as_processed: bool = False,
        play_sound_for_duplicate_received_qr: bool = False,
    ) -> None:
        self.settings = TicketDebugSettings(
            offline_scan_mode=offline_scan_mode,
            count_scan_success_as_processed=count_scan_success_as_processed,
            play_sound_for_duplicate_received_qr=play_sound_for_duplicate_received_qr,
        )

    def load_settings(self) -> TicketDebugSettings:
        return self.settings

    def save_settings(self, settings: TicketDebugSettings) -> None:
        self.settings = settings

    def should_count_scan_success_as_processed(self, settings: object | None = None) -> bool:
        # 실제 서비스와 동일하게 settings=None이면 load_settings()로 폴백한다.
        target = settings if settings is not None else self.settings
        return bool(getattr(target, "count_scan_success_as_processed", False))

    def should_play_sound_for_duplicate_received_qr(self, settings: object | None = None) -> bool:
        target = settings if settings is not None else self.settings
        return bool(getattr(target, "play_sound_for_duplicate_received_qr", False))
