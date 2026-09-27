"""LAN API v1 서버 계약 테스트 (FastAPI TestClient, TLS는 실행 계층에서 검증)."""
from __future__ import annotations

import uuid
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
    env["client"].post("/v1/admin/pause?paused=true", headers=_auth(token))
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


def test_orders_since_returns_changed_flag(env):
    token = _pair_device(env)
    first = env["client"].get("/v1/orders", headers=_auth(token)).json()
    assert first["changed"] is True
    second = env["client"].get("/v1/orders", params={"since": first["data_version"]}, headers=_auth(token)).json()
    assert second["changed"] is False
