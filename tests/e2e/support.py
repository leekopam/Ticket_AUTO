"""오프라인 E2E에서 외부 입출력을 대체하는 테스트 대역."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from PIL import Image

from models.receipt_settings_model import ReceiptSettings
from services.browser_service import ReceiptClickResult


@dataclass(frozen=True)
class CapturedPrintJob:
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
        if self.failure is not None:
            raise self.failure


class FakeBrowserService:
    """오프라인 E2E에서 실제 윗치폼 접근 여부를 기록한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def open_page(self, url: str, *, preserve_current_page: bool = False) -> bool:
        self.calls.append(("open_page", url))
        return True

    def click_receipt_button(self) -> ReceiptClickResult:
        self.calls.append(("click_receipt_button", ""))
        return ReceiptClickResult(success=True)


class FakeScannerView:
    """화면 없이 애플리케이션 상태 전이만 기록한다."""

    def __init__(self) -> None:
        self.auth_ready = True
        self.scanning_enabled = True
        self.status_message = ""

    def set_auth_ready(self, ready: bool) -> None:
        self.auth_ready = ready

    def set_scanning_enabled(self, enabled: bool) -> None:
        self.scanning_enabled = enabled

    def set_status_message(self, message: str) -> None:
        self.status_message = message


class MemoryReceiptSettingsStore:
    """파일을 읽지 않고 고정된 영수증 설정을 반환한다."""

    def __init__(self, settings: ReceiptSettings) -> None:
        self.settings = settings

    def load(self) -> ReceiptSettings:
        return self.settings


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
    """네트워크를 사용하지 않는 QR 처리 모드를 고정한다."""

    def load_settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            offline_scan_mode=True,
            count_scan_success_as_processed=False,
            play_sound_for_duplicate_received_qr=False,
        )

    @staticmethod
    def should_count_scan_success_as_processed(settings: object | None = None) -> bool:
        return False

    @staticmethod
    def should_play_sound_for_duplicate_received_qr(settings: object | None = None) -> bool:
        return False
