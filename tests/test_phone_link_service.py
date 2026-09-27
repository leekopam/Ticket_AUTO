"""PhoneLinkService 통합 테스트 — 실제 TLS 서버 기동 + 페어링 왕복."""
from __future__ import annotations

import socket
import ssl
import urllib.request
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from services.phone_link_service import PhoneLinkService


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_orders_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "주문목록"
    ws.append(["주문번호", "주문자명", "주문자연락처", "좌석번호", "주문상태", "[상품1]티켓"])
    ws.append(["AAAA1111_BBBB2222", "홍길동", "010-1234-5678", "A-1", "결제완료", 1])
    wb.save(path)


@pytest.fixture
def service(tmp_path: Path):
    data = tmp_path / "data.xlsx"
    _make_orders_xlsx(data)
    svc = PhoneLinkService(port=_free_port(), data_path=data)
    yield svc
    svc.stop()


def _https_post(addr: str, path: str, body: dict) -> dict:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # 테스트: 자체서명 인증서, 폰은 지문핀으로 검증
    req = urllib.request.Request(
        addr + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, context=ctx, timeout=5).read())


def test_start_returns_pairing_payload(service):
    payload = service.start()
    assert service.running
    assert payload["v"] == 1
    assert payload["addr"].startswith("https://")
    assert payload["join_code"]
    assert payload["cert_sha256"]
    assert payload["server_id"] == "ticket-auto-pc"
    # 재호출은 같은 페이로드를 재사용한다
    assert service.start() == payload


def test_real_tls_pairing_roundtrip(service):
    payload = service.start()
    addr = f"https://127.0.0.1:{payload['addr'].rsplit(':', 1)[1]}"

    pending = _https_post(addr, "/v1/pair", {"join_code": payload["join_code"], "device_name": "테스트폰"})
    assert pending["state"] == "pending_approval"

    approvals = service.pending_approvals()
    assert [a.device_name for a in approvals] == ["테스트폰"]

    service.approve(approvals[0].pair_ticket)
    approved = _https_post(addr, "/v1/pair", {"pair_ticket": approvals[0].pair_ticket})
    assert approved["state"] == "approved"
    assert approved["device_token"]


def test_reissue_join_code_changes_code(service):
    payload = service.start()
    new_payload = service.reissue_join_code()
    assert new_payload["join_code"] != payload["join_code"]
    assert new_payload["addr"] == payload["addr"]


def test_stop_clears_state(service):
    service.start()
    service.stop()
    assert not service.running
    assert service.payload is None
