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
from services.cert_service import detect_lan_ips, remove_server_cert
from services.excel_service import ExcelService
from services.pairing_service import DeviceInfo, PairingService, PendingApproval

DEFAULT_PORT = 18765  # 8765는 일부 환경에서 타 앱이 점유한다


class PhoneLinkService:
    """LAN API 서버 수명주기와 페어링 승인을 담당한다."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        data_path: Path | None = None,
        scan_handler: Callable[[str], dict[str, str]] | None = None,
        token_store_path: str | None = None,
        cert_dir: str | None = None,
    ):
        self._port = port
        self._data_path = data_path
        self._scan_handler = scan_handler
        self._cert_dir = cert_dir
        self._lock = threading.RLock()
        self._server: LanApiServer | None = None
        # 기기 레지스트리는 서버 수명과 무관하게 상시 유지한다 (네트워크 관리 탭이 조회)
        self._pairing = PairingService(token_store_path)
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
            server, _, fingerprint = create_server(
                excel=excel,
                port=self._port,
                pairing=self._pairing,
                scan_handler=self._scan_handler,
                cert_dir=self._cert_dir,
            )

            server.start()
            if not server.wait_started(timeout=5.0):
                server.stop()
                raise RuntimeError(
                    f"포트 {self._port}를 열 수 없습니다. "
                    "다른 프로그램이 이 포트를 사용 중이거나 방화벽이 차단했을 수 있습니다."
                )
            ips = detect_lan_ips()
            if not ips:
                server.stop()
                raise RuntimeError("LAN 주소를 찾지 못했습니다. 네트워크 연결 상태를 확인해주세요.")

            join_code = self._pairing.issue_join_code()
            generation, _ = DatasetTracker(excel).current()
            addr = f"https://{ips[0]}:{self._port}"
            self._payload = build_pairing_qr_payload(addr, fingerprint, join_code, generation)
            self._server = server
            return self._payload

    def reissue_join_code(self) -> dict:
        """연결 QR을 새 참가 코드로 재발급한다 (QR 유출/코드 만료 대응)."""
        with self._lock:
            if self._server is None or self._payload is None:
                return self.start()
            self._payload = {**self._payload, "join_code": self._pairing.issue_join_code()}
            return self._payload

    def pending_approvals(self) -> list[PendingApproval]:
        with self._lock:
            return self._pairing.pending_approvals()

    def approve(self, pair_ticket: str) -> bool:
        with self._lock:
            return self._pairing.approve(pair_ticket)

    def reject(self, pair_ticket: str) -> None:
        with self._lock:
            self._pairing.reject(pair_ticket)

    # ------------------------------------------------------------------
    # 기기 레지스트리 조회/관리 (서버 상태와 무관하게 동작)
    # ------------------------------------------------------------------

    @property
    def pairing(self) -> PairingService:
        return self._pairing

    def list_devices(self) -> list[DeviceInfo]:
        return self._pairing.list_devices()

    def rename_device(self, record_id: str, alias: str) -> bool:
        return self._pairing.rename_device(record_id, alias)

    def revoke_device(self, record_id: str) -> bool:
        return self._pairing.revoke_device(record_id)

    def revoke_all(self) -> int:
        """모든 기기 토큰을 폐기한다 — 행사 종료 정리용. 레코드는 보존한다."""
        return self._pairing.revoke_all()

    def regenerate_cert(self) -> dict | None:
        """인증서·개인키를 재생성한다.

        지문이 바뀌므로 모든 폰의 재페어링이 필요하다.
        서버 실행 중이면 새 인증서로 재기동하고 새 페이로드를 반환한다.
        꺼져 있으면 파일만 지우고 None을 반환한다 (다음 start()에서 생성).
        """
        with self._lock:
            running = self._server is not None
            if running:
                self._server.stop()
                self._server = None
                self._payload = None
            remove_server_cert(self._cert_dir)
            if not running:
                return None
            return self.start()

    def forget_device(self, record_id: str) -> bool:
        return self._pairing.forget_device(record_id)

    def device_for_device_id(self, device_id: str) -> DeviceInfo | None:
        """작업 이력의 device_id(과거 해시 포함)로 기기 레코드를 찾는다."""
        return self._pairing.record_for_device_id(device_id)

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.stop()
            # 레지스트리는 유지하고 미반영 활동 시각만 남긴다
            self._pairing.flush()
            self._server = None
            self._payload = None
