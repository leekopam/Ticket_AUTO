"""LAN API v1 서버 계약 테스트 (FastAPI TestClient, TLS는 실행 계층에서 검증)."""
from __future__ import annotations

import threading
import uuid
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from services.api_v1_server import ActionRegistry, create_api_v1_app
from services.excel_service import ExcelService
from services.pairing_service import PairingService


def _make_orders_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "주문목록"
    ws.append(["주문번호", "주문자명", "주문자연락처", "좌석번호", "주문상태", "[상품1]티켓"])
    ws.append(["AAAA1111_BBBB2222", "홍길동", "010-1234-5678", "A-1", "결제완료", 1])
    ws.append(["CCCC3333_DDDD4444", "김철수", "010-9999-8888", "A-2", "주문취소", 2])
    ws.append(["EEEE5555_FFFF6666", "이영희", "010-5555-4444", "A-3", "결제완료", 1])
    wb.save(path)


@pytest.fixture
def env(tmp_path: Path):
    data = tmp_path / "data.xlsx"
    _make_orders_xlsx(data)
    excel = ExcelService(str(data))
    pairing = PairingService(str(tmp_path / "devices.json"))
    client = TestClient(create_api_v1_app(excel, pairing))
    return {"excel": excel, "pairing": pairing, "client": client}


def _pair_device(env) -> str:
    pairing = env["pairing"]
    client = env["client"]
    code = pairing.issue_join_code()
    pending = client.post("/v1/pair", json={"join_code": code, "device_name": "t1"}).json()
    assert pending["state"] == "pending_approval"
    pairing.approve(pending["pair_ticket"])
    approved = client.post("/v1/pair", json={"pair_ticket": pending["pair_ticket"]}).json()
    assert approved["state"] == "approved"
    return approved["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_unauthorized(env):
    res = env["client"].get("/v1/status")
    assert res.status_code == 401


def test_api_schema_is_not_exposed_on_lan(env):
    assert env["client"].get("/openapi.json").status_code == 404


def test_pair_and_status(env):
    token = _pair_device(env)
    res = env["client"].get("/v1/status", headers=_auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["server_id"] == "ticket-auto-pc"
    assert body["dataset_generation"]
    assert body["data_version"]
    assert body["paused"] is False


def test_bad_join_code_rejected(env):
    res = env["client"].post("/v1/pair", json={"join_code": "000000"})
    assert res.json()["error"]["code"] == "EXPIRED_JOIN_CODE"


def test_order_lookup_masked(env):
    token = _pair_device(env)
    res = env["client"].get("/v1/orders/AAAA1111_BBBB2222", headers=_auth(token))
    body = res.json()
    assert body["state"] == "ok"
    order = body["order"]
    assert order["order_number"] == "AAAA1111_BBBB2222"
    assert order["name"] == "홍*동"
    assert order["phone"] == "010-****-5678"
    assert order["received"] is False


def test_order_not_found(env):
    token = _pair_device(env)
    res = env["client"].get("/v1/orders/NOPE_0000", headers=_auth(token))
    assert res.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_phone_scan_runs_pc_handler_once_and_reports_verified_result(tmp_path: Path):
    data = tmp_path / "data.xlsx"
    _make_orders_xlsx(data)
    excel = ExcelService(str(data))
    pairing = PairingService(str(tmp_path / "devices.json"))
    calls: list[str] = []

    def handle_scan(qr_url: str) -> dict[str, str]:
        calls.append(qr_url)
        assert excel.mark_order_received("AAAA1111_BBBB2222", "2026-09-27 12:00:00")
        return {"state": "succeeded", "order_id": "AAAA1111_BBBB2222", "message": "수령 완료"}

    env = {"client": TestClient(create_api_v1_app(excel, pairing, scan_handler=handle_scan)), "pairing": pairing}
    token = _pair_device(env)
    request_id = str(uuid.uuid4())
    qr_url = "https://witchform.com/qrcode_link.php?opaque=abc"
    payload = {"request_id": request_id, "qr_url": qr_url}
    assert env["client"].post("/v1/scan", json=payload, headers=_auth(token)).json()["state"] in {"accepted", "succeeded"}
    for _ in range(50):
        result = env["client"].get(f"/v1/actions/{request_id}", headers=_auth(token)).json()
        if result["state"] == "succeeded":
            break
        time.sleep(0.02)
    assert result["state"] == "succeeded"
    assert result["order_id"] == "AAAA1111_BBBB2222"
    assert env["client"].post("/v1/scan", json=payload, headers=_auth(token)).json()["state"] == "succeeded"
    assert calls == [qr_url]
    assert excel.find_order("AAAA1111_BBBB2222").is_received
    other = _pair_device(env)
    assert env["client"].get(f"/v1/actions/{request_id}", headers=_auth(other)).status_code == 404
    assert env["client"].post("/v1/scan", json=payload, headers=_auth(other)).status_code == 404
    bad = env["client"].post(
        "/v1/scan",
        json={"request_id": str(uuid.uuid4()), "qr_url": "https://witchform.com.evil.test/qrcode_link.php"},
        headers=_auth(token),
    ).json()
    assert bad["error"]["code"] == "INVALID_QR"


def test_phone_scan_records_order_id_in_operations(tmp_path: Path):
    """스캔 작업 종결 시 result의 주문번호가 _operations.order_id에도 기록되어야 한다."""
    data = tmp_path / "data.xlsx"
    _make_orders_xlsx(data)
    excel = ExcelService(str(data))
    pairing = PairingService(str(tmp_path / "devices.json"))

    def handle_scan(qr_url: str) -> dict[str, str]:
        return {"state": "succeeded", "order_id": "AAAA1111_BBBB2222", "message": "수령 완료"}

    env = {"client": TestClient(create_api_v1_app(excel, pairing, scan_handler=handle_scan)), "pairing": pairing}
    token = _pair_device(env)
    request_id = str(uuid.uuid4())
    env["client"].post(
        "/v1/scan",
        json={"request_id": request_id, "qr_url": "https://witchform.com/qrcode_link.php?opaque=abc"},
        headers=_auth(token),
    )
    for _ in range(50):
        ops = excel.list_operations()
        if ops and ops[-1].get("state") == "succeeded":
            break
        time.sleep(0.02)
    assert ops[-1]["order_id"] == "AAAA1111_BBBB2222"
    assert ops[-1]["device_id"]
    # 처리 시점 기기 이름 스냅샷이 기록되어야 한다 (페어링 이름 t1)
    assert ops[-1]["device_name"] == "t1"


def test_action_receipt_flow_and_xlsx_write(env):
    token = _pair_device(env)
    env["excel"].ensure_dataset_id()
    generation = env["client"].get("/v1/status", headers=_auth(token)).json()["dataset_generation"]

    request_id = str(uuid.uuid4())
    res = env["client"].post("/v1/actions", json={
        "request_id": request_id,
        "order_id": "AAAA1111_BBBB2222",
        "action": "receipt",
        "dataset_generation": generation,
    }, headers=_auth(token))
    assert res.json()["state"] == "accepted"

    # 같은 request_id 재전송 → 재실행 없이 같은 상태 반환 (멱등성)
    again = env["client"].post("/v1/actions", json={
        "request_id": request_id,
        "order_id": "AAAA1111_BBBB2222",
        "action": "receipt",
        "dataset_generation": generation,
    }, headers=_auth(token))
    assert again.json()["state"] == "accepted"

    # 폰이 WebView 처리 성공을 보고 → XLSX 기록
    report = env["client"].post(f"/v1/actions/{request_id}/result",
                               json={"state": "succeeded"}, headers=_auth(token))
    assert report.json()["state"] == "succeeded"

    order = env["excel"].find_order("AAAA1111_BBBB2222")
    assert order is not None and order.received_at

    # 종결 후 같은 request_id는 저장된 결과를 그대로 반환
    replay = env["client"].get(f"/v1/actions/{request_id}", headers=_auth(token))
    assert replay.json()["state"] == "succeeded"


def test_action_is_scoped_to_issuing_token_even_with_same_device_name(env):
    owner = _pair_device(env)
    other = _pair_device(env)
    request_id = str(uuid.uuid4())
    action = {
        "request_id": request_id,
        "order_id": "AAAA1111_BBBB2222",
        "action": "receipt",
        "dataset_generation": "",
    }
    assert env["client"].post("/v1/actions", json=action, headers=_auth(owner)).json()["state"] == "accepted"

    assert env["client"].get(f"/v1/actions/{request_id}", headers=_auth(other)).status_code == 404
    assert env["client"].post("/v1/actions", json=action, headers=_auth(other)).status_code == 404
    assert env["client"].post(
        f"/v1/actions/{request_id}/result",
        json={"state": "succeeded"},
        headers=_auth(other),
    ).status_code == 404
    assert not env["excel"].find_order(action["order_id"]).is_received
    assert env["client"].get(f"/v1/actions/{request_id}", headers=_auth(owner)).status_code == 200


def test_duplicate_in_progress_rejected(env):
    token = _pair_device(env)
    payload = lambda rid: {
        "request_id": rid, "order_id": "AAAA1111_BBBB2222",
        "action": "receipt", "dataset_generation": "",
    }
    assert env["client"].post("/v1/actions", json=payload(str(uuid.uuid4())), headers=_auth(token)).json()["state"] == "accepted"
    dup = env["client"].post("/v1/actions", json=payload(str(uuid.uuid4())), headers=_auth(token))
    assert dup.json()["error"]["code"] == "DUPLICATE_IN_PROGRESS"


def test_stale_dataset_rejected(env):
    token = _pair_device(env)
    res = env["client"].post("/v1/actions", json={
        "request_id": str(uuid.uuid4()),
        "order_id": "AAAA1111_BBBB2222",
        "action": "receipt",
        "dataset_generation": "old-generation",
    }, headers=_auth(token))
    assert res.json()["error"]["code"] == "STALE_DATASET"


def test_cancelled_and_received_orders_rejected(env):
    token = _pair_device(env)
    cancelled = env["client"].post("/v1/actions", json={
        "request_id": str(uuid.uuid4()), "order_id": "CCCC3333_DDDD4444",
        "action": "receipt", "dataset_generation": "",
    }, headers=_auth(token))
    assert cancelled.json()["error"]["code"] == "ORDER_CANCELLED"

    env["excel"].mark_order_received("EEEE5555_FFFF6666", "2026-01-01 09:00:00")
    received = env["client"].post("/v1/actions", json={
        "request_id": str(uuid.uuid4()), "order_id": "EEEE5555_FFFF6666",
        "action": "receipt", "dataset_generation": "",
    }, headers=_auth(token))
    assert received.json()["error"]["code"] == "ALREADY_PROCESSED"


def test_write_failure_becomes_needs_reconciliation(env, monkeypatch):
    token = _pair_device(env)
    request_id = str(uuid.uuid4())
    env["client"].post("/v1/actions", json={
        "request_id": request_id, "order_id": "AAAA1111_BBBB2222",
        "action": "receipt", "dataset_generation": "",
    }, headers=_auth(token))

    # XLSX 저장 실패를 강제한다 — 자동 재클릭 없이 확인필요로 전이되어야 한다
    monkeypatch.setattr(env["excel"], "mark_order_received", lambda *a, **k: False)
    res = env["client"].post(f"/v1/actions/{request_id}/result",
                             json={"state": "succeeded"}, headers=_auth(token))
    assert res.json()["state"] == "needs_reconciliation"


def test_registry_restores_interrupted_ops(env, tmp_path: Path):
    excel = env["excel"]
    excel.ensure_dataset_id()
    registry = ActionRegistry(excel)
    registry.register("req-crash", "AAAA1111_BBBB2222", "receipt", "phone-1")

    # 서버 재시작을 새 레지스트리로 시뮬레이션한다
    reloaded = ActionRegistry(excel)
    record = reloaded.get("req-crash")
    assert record is not None
    assert record["state"] == "needs_reconciliation"


def test_pause_blocks_new_actions(env):
    token = _pair_device(env)
    assert env["client"].post("/v1/admin/pause?paused=true", headers=_auth(token)).status_code == 404
    env["client"].app.state.paused = True
    res = env["client"].post("/v1/actions", json={
        "request_id": str(uuid.uuid4()), "order_id": "AAAA1111_BBBB2222",
        "action": "receipt", "dataset_generation": "",
    }, headers=_auth(token))
    assert res.json()["error"]["code"] == "PAUSED"


def test_oversized_body_rejected(env):
    token = _pair_device(env)
    res = env["client"].post(
        "/v1/actions",
        content=b"x" * (40 * 1024),
        headers={**_auth(token), "Content-Type": "application/json"},
    )
    assert res.status_code == 413


def test_streamed_oversized_body_without_content_length_rejected(env):
    token = _pair_device(env)
    res = env["client"].post(
        "/v1/actions",
        content=iter([b"x" * (16 * 1024)] * 3),
        headers={**_auth(token), "Content-Type": "application/json"},
    )
    assert res.status_code == 413


def test_orders_since_returns_changed_flag(env):
    token = _pair_device(env)
    first = env["client"].get("/v1/orders", headers=_auth(token)).json()
    assert first["changed"] is True
    second = env["client"].get("/v1/orders", params={"since": first["data_version"]}, headers=_auth(token)).json()
    assert second["changed"] is False


def test_scan_saturated_slots_rejected(tmp_path: Path):
    """동시 스캔이 상한에 닿으면 새 요청은 429로 거절되고 풀리면 다시 받는다."""
    import threading

    data = tmp_path / "data.xlsx"
    _make_orders_xlsx(data)
    excel = ExcelService(str(data))
    pairing = PairingService(str(tmp_path / "devices.json"))
    started = threading.Event()
    release = threading.Event()
    in_flight = 0

    def handle_scan(qr_url: str) -> dict[str, str]:
        nonlocal in_flight
        in_flight += 1
        if in_flight >= 4:
            started.set()
        release.wait(5)
        return {"state": "succeeded", "order_id": "AAAA1111_BBBB2222"}

    client = TestClient(create_api_v1_app(excel, pairing, scan_handler=handle_scan))
    env = {"client": client, "pairing": pairing}
    token = _pair_device(env)
    payload = lambda: {
        "request_id": str(uuid.uuid4()),
        # 슬롯 포화 검증이 목적이므로 요청마다 다른 QR을 쓴다 (동일 QR은 귀속된다).
        "qr_url": f"https://witchform.com/qrcode_link.php?opaque={uuid.uuid4()}",
    }
    for _ in range(4):
        assert client.post("/v1/scan", json=payload(), headers=_auth(token)).status_code == 200
    assert started.wait(5)
    busy = client.post("/v1/scan", json=payload(), headers=_auth(token))
    assert busy.status_code == 429
    assert busy.json()["error"]["code"] == "SERVER_BUSY"

    release.set()
    time.sleep(0.1)
    again = client.post("/v1/scan", json=payload(), headers=_auth(token))
    assert again.status_code == 200


def test_malformed_json_body_rejected(env):
    """깨진 JSON 본문은 422로 거절된다 (경계 검증)."""
    token = _pair_device(env)
    res = env["client"].post(
        "/v1/actions",
        content=b'{"request_id": ',
        headers={**_auth(token), "Content-Type": "application/json"},
    )
    assert res.status_code == 422


def test_disconnect_marks_device_offline_immediately(env):
    """명시 끊김 통지는 하트비트 타임아웃 없이 기기를 즉시 offline으로 바꾼다."""
    from services.device_presence import PRESENCE_OFFLINE, PRESENCE_ONLINE, presence_state

    token = _pair_device(env)
    env["client"].get("/v1/status", headers=_auth(token))
    device_id = env["pairing"].device_id_for_token(token)
    record = env["pairing"].record_for_device_id(device_id)
    assert presence_state(record, time.time()) == PRESENCE_ONLINE

    res = env["client"].post("/v1/disconnect", headers=_auth(token))
    assert res.status_code == 200
    record = env["pairing"].record_for_device_id(device_id)
    assert presence_state(record, time.time()) == PRESENCE_OFFLINE

    # 토큰은 유효 — 다음 인증 활동이 오면 자동으로 온라인 복귀한다
    assert env["client"].get("/v1/status", headers=_auth(token)).status_code == 200
    record = env["pairing"].record_for_device_id(device_id)
    assert presence_state(record, time.time()) == PRESENCE_ONLINE


def test_status_reports_rtt_and_beat_metrics(env):
    """폰이 보낸 RTT 헤더와 하트비트 도착 간격이 기기 품질 지표로 누적된다."""
    token = _pair_device(env)
    env["client"].get("/v1/status", headers=_auth(token))
    env["client"].get("/v1/status", headers={**_auth(token), "X-Client-Rtt-Ms": "42"})
    env["client"].get("/v1/status", headers=_auth(token))  # 헤더 없으면 RTT 갱신 안 함

    pairing = env["pairing"]
    info = pairing.record_for_device_id(pairing.device_id_for_token(token))
    assert info.last_rtt_ms == 42
    assert info.beat_interval_sec is not None  # 3회 관측 → 간격 산출됨
    assert info.missed_beats == 0


def test_disconnect_requires_auth(env):
    assert env["client"].post("/v1/disconnect").status_code == 401


def test_devices_requires_auth(env):
    assert env["client"].get("/v1/devices").status_code == 401


def test_devices_returns_self_only(env):
    """모바일 상태 표시 — 호출 기기 자신만 반환, 타 기기 정보는 노출하지 않는다."""
    token_a = _pair_device(env)
    _pair_device(env)  # 타 기기가 있어도 목록에 나오지 않아야 함
    res = env["client"].get("/v1/devices", headers=_auth(token_a))
    assert res.status_code == 200
    devices = res.json()["devices"]
    assert len(devices) == 1

    self_dev = devices[0]
    assert self_dev["self"] is True
    assert self_dev["presence"] == "online"
    assert self_dev["signal_level"] == 3  # 방금 인증 활동 → 양호
    assert 0 <= self_dev["last_seen_sec"] <= 20
    # RTT 미보고 상태 — 품질은 확인 중
    assert self_dev["quality"] == {"key": "loading", "label": "확인 중", "lit": 0}
    # 노출 필드는 이름/상태/시각뿐 — 내부 식별자는 보내지 않는다
    assert "device_uid" not in self_dev
    assert "token" not in self_dev


def test_devices_quality_matches_pc_tab_judgment(env):
    """모바일의 품질 표시가 PC 네트워크 탭과 같은 판정(connection_quality)을 따른다."""
    token = _pair_device(env)
    # 폰이 직전 왕복시간 105ms를 보고 → PC 탭의 "지연 주의"와 동일해야 한다
    env["client"].get("/v1/status", headers={**_auth(token), "X-Client-Rtt-Ms": "105"})
    res = env["client"].get("/v1/devices", headers=_auth(token))
    assert res.status_code == 200
    quality = res.json()["devices"][0]["quality"]
    assert quality == {"key": "warn", "label": "지연 주의", "lit": 2}

    # 정상 응답으로 회복하면 양호로 바뀐다
    env["client"].get("/v1/status", headers={**_auth(token), "X-Client-Rtt-Ms": "40"})
    res = env["client"].get("/v1/devices", headers=_auth(token))
    assert res.json()["devices"][0]["quality"] == {"key": "good", "label": "양호", "lit": 4}


def test_devices_no_other_device_info_leaks(env):
    """다른 기기의 이름·상태·식별자가 응답에 섞이지 않는지 확인한다."""
    token_a = _pair_device(env)
    token_b = _pair_device(env)
    pairing = env["pairing"]
    device_b = pairing.record_for_device_id(pairing.device_id_for_token(token_b))
    assert pairing.rename_device(device_b.record_id, "피어폰별칭")

    res = env["client"].get("/v1/devices", headers=_auth(token_a))
    assert res.status_code == 200
    body = res.text
    assert "피어폰별칭" not in body
    assert device_b.record_id not in body


def test_devices_revoked_self_gets_401(env):
    """차단된 기기의 토큰은 인증 거부 — 앱의 기존 401→재페어링 경로로 처리된다."""
    token = _pair_device(env)
    pairing = env["pairing"]
    record = pairing.record_for_device_id(pairing.device_id_for_token(token))
    assert pairing.revoke_device(record.record_id)

    res = env["client"].get("/v1/devices", headers=_auth(token))
    assert res.status_code == 401


def _mark_received(env, order_number: str) -> None:
    assert env["excel"].mark_order_received(order_number, "2026-09-28 12:00:00")


def test_work_log_requires_auth(env):
    assert env["client"].get("/v1/work-log").status_code == 401


def test_work_log_lists_only_processed_with_masking(env):
    token = _pair_device(env)
    _mark_received(env, "AAAA1111_BBBB2222")  # 홍길동
    res = env["client"].get("/v1/work-log", headers=_auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["changed"] is True
    assert body["total"] == 1
    item = body["items"][0]
    assert item["order_number"] == "AAAA1111_BBBB2222"
    assert item["name"] == "홍길동"          # 업무 목록은 이름 원문
    assert item["phone"] == "010-****-5678"
    assert item["status"] == "수령완료"
    assert item["device_name"] == "PC"      # _operations 없는 건 = PC 처리
    assert item["goods"]                    # 티켓/상품 정보 포함
    assert item["seat"] == "A-1"
    # 미수령 주문은 목록에 나오지 않는다
    assert all(i["order_number"] != "EEEE5555_FFFF6666" for i in body["items"])


def test_work_log_since_shortcircuits_unchanged(env):
    token = _pair_device(env)
    first = env["client"].get("/v1/work-log", headers=_auth(token)).json()
    res = env["client"].get(
        "/v1/work-log", headers=_auth(token), params={"since": first["data_version"]}
    )
    body = res.json()
    assert body["changed"] is False
    assert "items" not in body


def test_work_log_limit_bounds(env):
    token = _pair_device(env)
    assert env["client"].get("/v1/work-log", headers=_auth(token), params={"limit": 0}).status_code == 422
    assert env["client"].get("/v1/work-log", headers=_auth(token), params={"limit": 999}).status_code == 422


def test_work_log_device_name_from_ops(env):
    """_operations에 기록된 건은 기기 별칭으로 표시된다."""
    token = _pair_device(env)
    record_id = env["pairing"].list_devices()[0].record_id
    env["pairing"].rename_device(record_id, "매표소폰")
    _mark_received(env, "AAAA1111_BBBB2222")
    # 폰이 처리한 것처럼 _operations에 기록
    device_id = env["pairing"].device_id_for_token(token)
    env["excel"].append_operation({
        "request_id": "req-1",
        "order_id": "AAAA1111_BBBB2222",
        "action": "receipt",
        "state": "succeeded",
        "device_id": device_id,
        "updated_at": "2026-09-28 12:00:00",
    })
    body = env["client"].get("/v1/work-log", headers=_auth(token)).json()
    item = body["items"][0]
    assert item["device_name"] == "매표소폰"


def test_same_qr_scan_from_two_devices_coalesces_to_one_execution(tmp_path: Path):
    """폰 2대가 동일 QR을 동시에 스캔하면 처리는 1회만 실행되고 두 요청 모두 같은 결과를 받는다."""
    data = tmp_path / "data.xlsx"
    _make_orders_xlsx(data)
    excel = ExcelService(str(data))
    pairing = PairingService(str(tmp_path / "devices.json"))
    calls: list[str] = []
    gate = threading.Event()

    def handle_scan(qr_url: str) -> dict[str, str]:
        calls.append(qr_url)
        gate.wait(timeout=5)
        return {"state": "succeeded", "order_id": "AAAA1111_BBBB2222", "message": "수령 완료"}

    env = {"client": TestClient(create_api_v1_app(excel, pairing, scan_handler=handle_scan)), "pairing": pairing}
    token_a = _pair_device(env)
    token_b = _pair_device(env)
    qr_url = "https://witchform.com/qrcode_link.php?opaque=shared"
    rid_a, rid_b = str(uuid.uuid4()), str(uuid.uuid4())

    res_a = env["client"].post(
        "/v1/scan", json={"request_id": rid_a, "qr_url": qr_url}, headers=_auth(token_a)
    ).json()
    assert res_a["state"] == "accepted"
    # A의 처리가 진행 중인 동안 B가 같은 QR을 스캔 — 귀속되어 별도 실행되지 않는다
    res_b = env["client"].post(
        "/v1/scan", json={"request_id": rid_b, "qr_url": qr_url}, headers=_auth(token_b)
    ).json()
    assert res_b["state"] == "accepted"
    assert res_b["request_id"] == rid_b
    gate.set()

    def wait_terminal(rid: str, token: str) -> dict:
        result = {}
        for _ in range(100):
            result = env["client"].get(f"/v1/actions/{rid}", headers=_auth(token)).json()
            if result["state"] in {"succeeded", "already_processed", "failed", "needs_reconciliation", "rejected"}:
                return result
            time.sleep(0.02)
        return result

    final_a = wait_terminal(rid_a, token_a)
    final_b = wait_terminal(rid_b, token_b)
    assert final_a["state"] == "succeeded"
    assert final_b["state"] == "succeeded"
    assert final_a["order_id"] == final_b["order_id"] == "AAAA1111_BBBB2222"
    assert calls == [qr_url]


def test_scan_after_terminal_reruns_normally(tmp_path: Path):
    """첫 요청이 종결된 뒤 같은 QR 재스캔은 귀속되지 않고 정상 재처리된다."""
    data = tmp_path / "data.xlsx"
    _make_orders_xlsx(data)
    excel = ExcelService(str(data))
    pairing = PairingService(str(tmp_path / "devices.json"))
    calls: list[str] = []

    def handle_scan(qr_url: str) -> dict[str, str]:
        calls.append(qr_url)
        return {"state": "already_processed", "order_id": "AAAA1111_BBBB2222", "message": "이미 수령된 주문입니다."}

    env = {"client": TestClient(create_api_v1_app(excel, pairing, scan_handler=handle_scan)), "pairing": pairing}
    token = _pair_device(env)
    qr_url = "https://witchform.com/qrcode_link.php?opaque=again"

    for _ in range(2):
        rid = str(uuid.uuid4())
        env["client"].post("/v1/scan", json={"request_id": rid, "qr_url": qr_url}, headers=_auth(token))
        for _ in range(100):
            result = env["client"].get(f"/v1/actions/{rid}", headers=_auth(token)).json()
            if result["state"] == "already_processed":
                break
            time.sleep(0.02)
        assert result["state"] == "already_processed"
    assert calls == [qr_url, qr_url]
