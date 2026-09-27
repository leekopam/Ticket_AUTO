"""휴대폰 연결 서비스 — LAN API v1 서버 + 페어링을 Flet 앱에서 관리한다.

- 운영 data.xlsx를 읽는 전용 ExcelService 인스턴스를 사용한다
  (대시보드/런타임과 동일하게 인스턴스별로 워크북을 새로 여는 기존 패턴).
- 스캔 런타임 시작 전에도 페어링/조회 API를 제공할 수 있다.
"""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Callable

from project_paths import ensure_managed_data_file
from services.api_v1_server import (
    DatasetTracker,
    LanApiServer,
    build_pairing_qr_payload,
    create_api_v1_app,
    create_server,
)
from services.cert_service import detect_lan_ips
from services.excel_service import ExcelService
from services.pairing_service import PairingService, PendingApproval

DEFAULT_PORT = 18765  # 8765는 일부 환경에서 타 앱이 점유한다


class PhoneLinkService:
    """LAN API 서버 수명주기와 페어링 승인을 담당한다."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        data_path: Path | None = None,
        scan_handler: Callable[[str], dict[str, str]] | None = None,
    ):
        self._port = port
        self._data_path = data_path
        self._scan_handler = scan_handler
        self._lock = threading.RLock()
        self._server: LanApiServer | None = None
        self._pairing: PairingService | None = None
        self._payload: dict | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def payload(self) -> dict | None:
        return self._payload

    def start(self) -> dict:
        """서버를 기동하고 연결 QR 페이로드를 반환한다. 실행 중이면 기존 페이로드 재사용."""
        with self._lock:
            if self._server is not None and self._payload is not None:
                return self._payload

            data_path = self._data_path if self._data_path is not None else ensure_managed_data_file()
            excel = ExcelService(str(data_path))
            server, pairing, fingerprint = create_server(
                excel=excel, port=self._port, scan_handler=self._scan_handler
            )
            join_code = pairing.issue_join_code()
            generation, _ = DatasetTracker(excel).current()

            addr = f"https://{detect_lan_ips()[0]}:{self._port}"
            self._payload = build_pairing_qr_payload(addr, fingerprint, join_code, generation)
            server.start()
            self._server = server
            self._pairing = pairing
            return self._payload

    def reissue_join_code(self) -> dict:
        """연결 QR을 새 참가 코드로 재발급한다 (QR 유출/코드 만료 대응)."""
        with self._lock:
            if self._server is None or self._payload is None:
                return self.start()
            assert self._pairing is not None
            self._payload = {**self._payload, "join_code": self._pairing.issue_join_code()}
            return self._payload

    def pending_approvals(self) -> list[PendingApproval]:
        with self._lock:
            if self._pairing is None:
                return []
            return self._pairing.pending_approvals()

    def approve(self, pair_ticket: str) -> bool:
        with self._lock:
            return bool(self._pairing and self._pairing.approve(pair_ticket))

    def reject(self, pair_ticket: str) -> None:
        with self._lock:
            if self._pairing is not None:
                self._pairing.reject(pair_ticket)

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.stop()
            self._server = None
            self._pairing = None
            self._payload = None
