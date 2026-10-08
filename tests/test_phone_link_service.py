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
    svc = PhoneLinkService(
        port=_free_port(),
        data_path=data,
        token_store_path=str(tmp_path / "devices.json"),
        cert_dir=str(tmp_path / "api_cert"),
    )
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


def test_start_advertises_reachable_addr_not_virtual(service, monkeypatch):
    """WSL 가상 어댑터가 getaddrinfo 첫 항목이어도 실제 LAN 주소를 광고한다.

    172.27.208.1(vEthernet WSL)이 ips[0]이면 폰이 도달 못 해 연결시간 초과 —
    order_serving_ips가 Internet 프로필 어댑터의 주소를 골라야 한다.
    """
    snap = {
        "Addresses": [
            {"InterfaceAlias": "vEthernet (WSL (Hyper-V firewall))", "IPAddress": "172.27.208.1"},
            {"InterfaceAlias": "이더넷", "IPAddress": "192.168.31.233"},
        ],
        "Profiles": [{"InterfaceAlias": "이더넷", "Name": "네트워크", "IPv4Connectivity": 4}],
        "Adapters": [
            {"Name": "이더넷", "InterfaceDescription": "Realtek PCIe GbE", "Status": "Up"},
            {
                "Name": "vEthernet (WSL (Hyper-V firewall))",
                "InterfaceDescription": "Hyper-V Virtual Ethernet Adapter",
                "Status": "Up",
            },
        ],
    }
    monkeypatch.setattr(
        "services.phone_link_service.detect_lan_ips",
        lambda: ["172.27.208.1", "192.168.31.233"],
    )
    monkeypatch.setattr(
        "services.network_path_service._collect_snapshot", lambda: snap
    )

    payload = service.start()

    port = payload["addr"].rsplit(":", 1)[1]
    assert payload["addr"] == f"https://192.168.31.233:{port}"
    assert payload["alt_addrs"] == [f"https://172.27.208.1:{port}"]


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


def test_registry_survives_server_stop(service, tmp_path: Path):
    """서버가 꺼져도 기기 레지스트리는 조회·관리 가능해야 한다 (네트워크 관리 탭)."""
    payload = service.start()
    addr = f"https://127.0.0.1:{payload['addr'].rsplit(':', 1)[1]}"
    pending = _https_post(addr, "/v1/pair", {"join_code": payload["join_code"], "device_name": "테스트폰"})
    service.approve(pending["pair_ticket"])

    service.stop()
    devices = service.list_devices()
    assert [d.reported_name for d in devices] == ["테스트폰"]

    # 서버 정지 상태에서도 이름 변경·차단이 동작한다
    record_id = devices[0].record_id
    assert service.rename_device(record_id, "입구1번")
    assert service.list_devices()[0].custom_name == "입구1번"
    assert service.revoke_device(record_id)
    assert service.list_devices()[0].revoked is True


def test_forget_device_removes_record(service):
    payload = service.start()
    addr = f"https://127.0.0.1:{payload['addr'].rsplit(':', 1)[1]}"
    pending = _https_post(addr, "/v1/pair", {"join_code": payload["join_code"], "device_name": "테스트폰"})
    service.approve(pending["pair_ticket"])

    record_id = service.list_devices()[0].record_id
    assert service.forget_device(record_id)
    assert service.list_devices() == []


def test_regenerate_cert_rotates_fingerprint_and_keeps_serving(service):
    """인증서 재생성은 지문을 바꾸고 서버를 같은 포트로 재기동한다."""
    payload = service.start()
    old_fp = payload["cert_sha256"]
    addr = f"https://127.0.0.1:{payload['addr'].rsplit(':', 1)[1]}"

    new_payload = service.regenerate_cert()
    assert service.running
    assert new_payload is not None
    assert new_payload["cert_sha256"] != old_fp
    assert new_payload["addr"] == payload["addr"]
    # 새 지문으로도 실제 TLS 요청이 동작한다
    res = _https_post(addr, "/v1/pair", {"join_code": "000000"})
    assert res["error"]["code"] == "EXPIRED_JOIN_CODE"


def test_regenerate_cert_while_stopped(service):
    """서버가 꺼져 있으면 인증서 파일만 지우고 다음 기동에서 새 인증서가 만들어진다."""
    from services.cert_service import cert_sha256_fingerprint

    payload = service.start()
    old_fp = payload["cert_sha256"]
    service.stop()
    assert service.regenerate_cert() is None
    assert not service.running

    new_payload = service.start()
    assert new_payload["cert_sha256"] != old_fp


def test_revoke_all_blocks_every_token(service):
    """전체 차단은 모든 토큰을 무효화하고 레코드는 보존한다."""
    payload = service.start()
    addr = f"https://127.0.0.1:{payload['addr'].rsplit(':', 1)[1]}"
    for name in ("폰A", "폰B"):
        service.reissue_join_code()
        code = service.payload["join_code"]
        pending = _https_post(addr, "/v1/pair", {"join_code": code, "device_name": name})
        service.approve(pending["pair_ticket"])
    assert service.revoke_all() == 2
    assert all(d.revoked for d in service.list_devices())


def test_start_fails_clearly_without_lan_ip(service, monkeypatch):
    """LAN IP를 못 찾으면 IndexError가 아니라 원인 있는 RuntimeError로 실패한다."""
    monkeypatch.setattr("services.phone_link_service.detect_lan_ips", lambda: [])
    with pytest.raises(RuntimeError, match="LAN 주소"):
        service.start()
    assert not service.running


def test_start_fails_clearly_when_port_occupied(service):
    """포트를 다른 프로세스가 점유하면 조용히 죽지 않고 RuntimeError가 난다."""
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("0.0.0.0", service._port))
    blocker.listen(1)
    try:
        with pytest.raises(RuntimeError, match="포트"):
            service.start()
        assert not service.running
    finally:
        blocker.close()
