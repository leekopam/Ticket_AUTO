"""실제 HTTPS 연결에서 휴대폰 페어링·인증·토큰 폐기를 검증한다."""
from __future__ import annotations

import hashlib
import http.client
import json
import socket
import ssl
import time
from pathlib import Path

import pytest
from openpyxl import Workbook

from services.api_v1_server import LanApiServer, create_api_v1_app
from services.cert_service import ensure_server_cert
from services.excel_service import ExcelService
from services.pairing_service import PairingService


@pytest.fixture
def link(tmp_path: Path):
    data_path = tmp_path / "orders.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "주문목록"
    sheet.append(["주문번호", "주문자명", "주문자연락처", "좌석번호", "주문상태", "[상품1]티켓"])
    sheet.append(["AAAA1111_BBBB2222", "홍길동", "010-1234-5678", "A-1", "결제완료", 1])
    workbook.save(data_path)

    cert = ensure_server_cert(cert_dir=str(tmp_path / "cert"))
    pairing = PairingService(str(tmp_path / "devices.json"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = LanApiServer(
        create_api_v1_app(ExcelService(str(data_path)), pairing),
        "127.0.0.1", port, cert.cert_path, cert.key_path,
    )
    server.start()
    try:
        yield port, cert.sha256_fingerprint, pairing
    finally:
        server.stop()


def _pinned_json(
    port: int,
    fingerprint: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    token: str = "",
) -> tuple[int, dict]:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=3)
    try:
        for attempt in range(30):
            try:
                connection.connect()
                break
            except ConnectionRefusedError:
                if attempt == 29:
                    raise
                time.sleep(0.05)

        # 테스트 클라이언트도 인증서 지문 확인 전에는 비밀값을 전송하지 않는다.
        certificate = connection.sock.getpeercert(binary_form=True)
        actual = hashlib.sha256(certificate).hexdigest().upper()
        if actual != fingerprint.replace(":", "").upper():
            raise ValueError("서버 인증서 지문 불일치")

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(
            method, path,
            body=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_tls_pairing_token_lifecycle_and_masked_order(link):
    port, fingerprint, pairing = link
    code = pairing.issue_join_code()

    with pytest.raises(ValueError, match="지문 불일치"):
        _pinned_json(port, "00" * 32, "POST", "/v1/pair", body={"join_code": code})
    protected = (
        ("GET", "/v1/status", None),
        ("GET", "/v1/orders", None),
        ("GET", "/v1/orders/search?q=staff", None),
        ("GET", "/v1/orders/AAAA1111_BBBB2222", None),
        ("POST", "/v1/scan", {"request_id": "probe", "qr_url": "https://witchform.com/qrcode_link.php"}),
        ("POST", "/v1/actions", {"request_id": "probe", "order_id": "AAAA1111_BBBB2222", "action": "receipt"}),
        ("GET", "/v1/actions/probe", None),
        ("POST", "/v1/actions/probe/result", {"state": "succeeded"}),
    )
    for method, path, body in protected:
        assert _pinned_json(port, fingerprint, method, path, body=body)[0] == 401

    status, pending = _pinned_json(
        port, fingerprint, "POST", "/v1/pair",
        body={"join_code": code, "device_name": "staff-phone"},
    )
    assert status == 200 and pending["state"] == "pending_approval"
    assert _pinned_json(
        port, fingerprint, "POST", "/v1/pair", body={"join_code": code}
    )[1]["error"]["code"] == "EXPIRED_JOIN_CODE"

    ticket = pending["pair_ticket"]
    assert pairing.approve(ticket)
    approved = _pinned_json(
        port, fingerprint, "POST", "/v1/pair", body={"pair_ticket": ticket}
    )[1]
    token = approved["device_token"]
    assert _pinned_json(port, fingerprint, "GET", "/v1/status", token=token)[0] == 200
    order = _pinned_json(
        port, fingerprint, "GET", "/v1/orders/AAAA1111_BBBB2222", token=token
    )[1]["order"]
    assert order["name"] == "홍*동"
    assert order["phone"] == "010-****-5678"

    assert pairing.revoke_token(token)
    assert _pinned_json(port, fingerprint, "GET", "/v1/status", token=token)[0] == 401


def test_device_registry_presence_rename_and_repair(link):
    """device_uid 기반 재페어링·별칭 유지·활동 기록·차단을 실제 TLS로 검증한다."""
    port, fingerprint, pairing = link
    uid = "550e8400-e29b-41d4-a716-446655440000"

    def _pair() -> str:
        code = pairing.issue_join_code()
        _, pending = _pinned_json(
            port, fingerprint, "POST", "/v1/pair",
            body={"join_code": code, "device_name": "staff-phone", "device_uid": uid},
        )
        assert pairing.approve(pending["pair_ticket"])
        return _pinned_json(
            port, fingerprint, "POST", "/v1/pair",
            body={"pair_ticket": pending["pair_ticket"]},
        )[1]["device_token"]

    token = _pair()
    # 인증된 호출이 last_seen을 갱신한다 (폰 하트비트 역할)
    assert _pinned_json(port, fingerprint, "GET", "/v1/status", token=token)[0] == 200
    devices = pairing.list_devices()
    assert len(devices) == 1
    record = devices[0]
    assert record.reported_name == "staff-phone"
    assert record.device_uid == uid
    assert record.last_seen_at  # 활동 기록됨 → 온라인 판정 기준

    # 별칭 설정 후 토큰 폐기 → 같은 device_uid 재페어링이면 별칭 유지
    assert pairing.rename_device(record.record_id, "입구1번")
    assert pairing.revoke_token(token)
    token2 = _pair()
    devices = pairing.list_devices()
    assert len(devices) == 1
    assert devices[0].record_id == record.record_id
    assert devices[0].custom_name == "입구1번"
    assert _pinned_json(port, fingerprint, "GET", "/v1/status", token=token2)[0] == 200

    # 기기 차단은 레코드를 지우지 않고 인증만 거절한다
    assert pairing.revoke_device(record.record_id)
    assert _pinned_json(port, fingerprint, "GET", "/v1/status", token=token2)[0] == 401
    kept = pairing.list_devices()
    assert len(kept) == 1 and kept[0].revoked and kept[0].custom_name == "입구1번"


def test_reject_lockout_and_sanitized_name_over_tls(link):
    """실 TLS로 거절 흐름·무차별 잠금·기기 이름 정제를 검증한다."""
    port, fingerprint, pairing = link

    # 운영자 거절 → 폰 폴링이 rejected → 티켓 소진
    code = pairing.issue_join_code()
    _, pending = _pinned_json(
        port, fingerprint, "POST", "/v1/pair",
        body={"join_code": code, "device_name": "stranger"},
    )
    assert pairing.reject(pending["pair_ticket"])
    assert _pinned_json(
        port, fingerprint, "POST", "/v1/pair", body={"pair_ticket": pending["pair_ticket"]}
    )[1]["state"] == "rejected"
    assert _pinned_json(
        port, fingerprint, "POST", "/v1/pair", body={"pair_ticket": pending["pair_ticket"]}
    )[1]["error"]["code"] == "EXPIRED_JOIN_CODE"

    # 잘못된 코드 연속 제출 → 잠금, 잠금 중에는 올바른 코드도 거절된다
    for _ in range(10):
        _pinned_json(port, fingerprint, "POST", "/v1/pair", body={"join_code": "000000"})
    fresh = pairing.issue_join_code()
    locked = _pinned_json(
        port, fingerprint, "POST", "/v1/pair",
        body={"join_code": fresh, "device_name": "staff"},
    )[1]
    assert locked["error"]["code"] == "PAIRING_LOCKED"

    # 제어문자가 든 기기 이름은 정제되어 레지스트리에 저장된다
    pairing._lockout_until = 0.0  # 잠금 해제 (테스트 격리)
    code2 = pairing.issue_join_code()
    _, pending2 = _pinned_json(
        port, fingerprint, "POST", "/v1/pair",
        body={"join_code": code2, "device_name": "evil\x1b[31m\nphone\x00"},
    )
    assert pairing.approve(pending2["pair_ticket"])
    record = pairing.list_devices()[0]
    assert record.reported_name == "evil[31mphone"


def test_malformed_and_oversized_requests_over_tls(link):
    """깨진 JSON과 대용량 본문을 실 TLS 경로에서 거절한다."""
    port, fingerprint, _ = link
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=3)
    try:
        for _ in range(30):
            try:
                conn.connect()
                break
            except ConnectionRefusedError:
                time.sleep(0.05)
        conn.request("POST", "/v1/pair", body=b'{"join_code": ', headers={"Content-Type": "application/json"})
        assert conn.getresponse().status == 422
        big = b"x" * (40 * 1024)
        conn2 = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=3)
        conn2.request("POST", "/v1/pair", body=big, headers={"Content-Type": "application/json"})
        assert conn2.getresponse().status == 413
        conn2.close()
    finally:
        conn.close()


def test_expired_token_rejected_over_tls(link):
    """만료된 토큰은 실 TLS 경로에서 401을 받고 재페어링으로 복구된다."""
    port, fingerprint, pairing = link
    code = pairing.issue_join_code()
    _, pending = _pinned_json(
        port, fingerprint, "POST", "/v1/pair",
        body={"join_code": code, "device_name": "staff-phone"},
    )
    assert pairing.approve(pending["pair_ticket"])
    token = _pinned_json(
        port, fingerprint, "POST", "/v1/pair", body={"pair_ticket": pending["pair_ticket"]}
    )[1]["device_token"]
    assert _pinned_json(port, fingerprint, "GET", "/v1/status", token=token)[0] == 200

    # 토큰 만료 시각을 과거로 이동 (레지스트리 내부 상태 조작)
    record = next(iter(pairing._records.values()))
    record.token_expires_at = time.time() - 1
    assert _pinned_json(port, fingerprint, "GET", "/v1/status", token=token)[0] == 401


def test_explicit_disconnect_over_tls(link):
    """명시 끊김 통지가 실 TLS 경로에서 기기를 즉시 끊김으로 만든다."""
    from services.device_presence import PRESENCE_OFFLINE, PRESENCE_ONLINE, presence_state

    port, fingerprint, pairing = link
    code = pairing.issue_join_code()
    _, pending = _pinned_json(
        port, fingerprint, "POST", "/v1/pair",
        body={"join_code": code, "device_name": "staff-phone"},
    )
    assert pairing.approve(pending["pair_ticket"])
    token = _pinned_json(
        port, fingerprint, "POST", "/v1/pair", body={"pair_ticket": pending["pair_ticket"]}
    )[1]["device_token"]

    assert _pinned_json(port, fingerprint, "GET", "/v1/status", token=token)[0] == 200
    device = pairing.list_devices()[0]
    assert presence_state(device, time.time()) == PRESENCE_ONLINE

    assert _pinned_json(port, fingerprint, "POST", "/v1/disconnect", token=token)[0] == 200
    device = pairing.list_devices()[0]
    assert presence_state(device, time.time()) == PRESENCE_OFFLINE
