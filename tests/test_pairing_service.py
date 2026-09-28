"""PairingService 페어링 코드/승인/토큰 수명주기 테스트."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from services import pairing_service
from services.pairing_service import PairingService


@pytest.fixture
def pairing(tmp_path: Path) -> PairingService:
    return PairingService(str(tmp_path / "devices.json"))


def test_pair_flow_pending_then_approved(pairing: PairingService):
    code = pairing.issue_join_code()

    first = pairing.request_pair(join_code=code, device_name="staff-phone-1")
    assert first.state == "pending_approval"
    assert first.pair_ticket

    # 승인 전 폴링은 pending을 유지한다
    poll = pairing.request_pair(pair_ticket=first.pair_ticket)
    assert poll.state == "pending_approval"

    token = pairing.approve(first.pair_ticket)
    assert token

    approved = pairing.request_pair(pair_ticket=first.pair_ticket)
    assert approved.state == "approved"
    assert approved.device_token == token

    # 승인 완료된 티켓은 재사용할 수 없다
    again = pairing.request_pair(pair_ticket=first.pair_ticket)
    assert again.state == "error"


def test_join_code_is_one_time(pairing: PairingService):
    code = pairing.issue_join_code()
    assert pairing.request_pair(join_code=code).state == "pending_approval"
    # 같은 코드로 두 번째 기기는 등록 불가
    second = pairing.request_pair(join_code=code, device_name="attacker")
    assert second.state == "error"
    assert second.error_code == "EXPIRED_JOIN_CODE"


def test_issuing_new_join_code_invalidates_previous_code(pairing: PairingService):
    old_code = pairing.issue_join_code()
    new_code = pairing.issue_join_code()

    assert pairing.request_pair(join_code=old_code).state == "error"
    assert pairing.request_pair(join_code=new_code).state == "pending_approval"


def test_expired_join_code(pairing: PairingService, monkeypatch):
    code = pairing.issue_join_code()
    # 만료 시점 이후로 시간을 이동시킨다
    entry = pairing._join_codes[code]
    monkeypatch.setattr(entry, "expires_at", time.time() - 1)
    result = pairing.request_pair(join_code=code)
    assert result.state == "error"


def test_expired_approval_cannot_issue_token(pairing: PairingService):
    code = pairing.issue_join_code()
    pending = pairing.request_pair(join_code=code, device_name="expired-phone")
    pairing._tickets[pending.pair_ticket].expires_at = time.time() - 1

    assert pairing.approve(pending.pair_ticket) == ""
    assert pairing.request_pair(pair_ticket=pending.pair_ticket).state == "error"
    assert pairing.revoke_all() == 0


def test_wrong_code_lockout(pairing: PairingService):
    pairing.issue_join_code()
    for _ in range(pairing_service.FAILURE_LOCKOUT_THRESHOLD):
        pairing.request_pair(join_code="000000")
    # 임계치 도달 후에는 유효한 코드도 잠금된다
    result = pairing.request_pair(join_code=pairing.issue_join_code())
    assert result.state == "error"
    assert result.error_code == "PAIRING_LOCKED"


def test_reject_flow(pairing: PairingService):
    code = pairing.issue_join_code()
    pending = pairing.request_pair(join_code=code, device_name="x")
    assert pairing.reject(pending.pair_ticket)
    poll = pairing.request_pair(pair_ticket=pending.pair_ticket)
    assert poll.state == "rejected"


def test_token_validation_and_revocation(pairing: PairingService, tmp_path: Path):
    code = pairing.issue_join_code()
    pending = pairing.request_pair(join_code=code, device_name="staff-1")
    token = pairing.approve(pending.pair_ticket)
    assert pairing.device_name_for_token(token) == "staff-1"

    # 재시작(재로드) 후에도 토큰이 유지되어야 한다
    reloaded = PairingService(str(tmp_path / "devices.json"))
    assert reloaded.device_name_for_token(token) == "staff-1"

    assert reloaded.revoke_token(token)
    assert reloaded.device_name_for_token(token) is None
    assert not reloaded.revoke_token("not-a-token")


def test_token_store_failure_does_not_approve_or_revoke_in_memory(pairing: PairingService, monkeypatch):
    code = pairing.issue_join_code()
    pending = pairing.request_pair(join_code=code, device_name="staff")

    def fail_save(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(pairing, "_save_tokens", fail_save)
    with pytest.raises(OSError):
        pairing.approve(pending.pair_ticket)
    assert pairing.request_pair(pair_ticket=pending.pair_ticket).state == "pending_approval"
    assert pairing.revoke_all() == 0

    monkeypatch.undo()
    token = pairing.approve(pending.pair_ticket)
    assert token
    monkeypatch.setattr(pairing, "_save_tokens", fail_save)
    with pytest.raises(OSError):
        pairing.revoke_token(token)
    assert pairing.device_name_for_token(token) == "staff"


def test_revoke_all(pairing: PairingService):
    for name in ("a", "b"):
        code = pairing.issue_join_code()
        pairing.approve(pairing.request_pair(join_code=code, device_name=name).pair_ticket)
    assert pairing.revoke_all() == 2
    assert pairing.revoke_all() == 0


# ----------------------------------------------------------------------
# 기기 레지스트리 v2
# ----------------------------------------------------------------------


def _approve_one(pairing: PairingService, name: str = "phone-1", uid: str = "") -> str:
    code = pairing.issue_join_code()
    pending = pairing.request_pair(join_code=code, device_name=name, device_uid=uid)
    return pairing.approve(pending.pair_ticket)


def test_v1_file_migrates_to_v2(tmp_path: Path):
    path = tmp_path / "devices.json"
    path.write_text(
        '{"devices": [{"token_hash": "abc123", "device_name": "staff-1"}]}',
        encoding="utf-8",
    )
    service = PairingService(str(path))
    devices = service.list_devices()
    assert len(devices) == 1
    assert devices[0].reported_name == "staff-1"
    assert devices[0].custom_name == ""
    assert devices[0].revoked is False


def test_corrupt_file_is_preserved_and_starts_empty(tmp_path: Path):
    path = tmp_path / "devices.json"
    path.write_text("{broken json", encoding="utf-8")
    service = PairingService(str(path))
    assert service.list_devices() == []
    # 손상 원본은 지우지 않고 보존한다
    assert list(tmp_path.glob("devices.json.corrupt-*"))


def test_record_activity_updates_last_seen(pairing: PairingService, tmp_path: Path):
    token = _approve_one(pairing, "staff-1")
    pairing.record_activity(pairing.device_id_for_token(token), force_save=True)
    device = pairing.list_devices()[0]
    assert device.last_seen_at
    assert device.first_seen_at


def test_activity_save_is_throttled(pairing: PairingService, tmp_path: Path):
    token = _approve_one(pairing, "staff-1")
    path = tmp_path / "devices.json"
    before = path.read_text(encoding="utf-8")
    device_id = pairing.device_id_for_token(token)
    pairing.record_activity(device_id)  # 스로틀 구간 안이라 디스크 미반영
    assert path.read_text(encoding="utf-8") == before
    pairing.flush()
    assert "last_seen_at" in path.read_text(encoding="utf-8")


def test_rename_device_persists_alias(pairing: PairingService, tmp_path: Path):
    token = _approve_one(pairing, "staff-1")
    record_id = pairing.list_devices()[0].record_id
    assert pairing.rename_device(record_id, "  입구1번  ")
    reloaded = PairingService(str(tmp_path / "devices.json"))
    device = reloaded.list_devices()[0]
    assert device.custom_name == "입구1번"
    assert device.reported_name == "staff-1"


def test_rename_device_rejects_invalid_and_clears_on_empty(pairing: PairingService):
    _approve_one(pairing)
    record_id = pairing.list_devices()[0].record_id
    assert not pairing.rename_device(record_id, "a" * 21)
    assert not pairing.rename_device("no-such-record", "x")
    assert pairing.rename_device(record_id, "입구")
    assert pairing.rename_device(record_id, "")
    assert pairing.list_devices()[0].custom_name == ""


def test_revoke_keeps_record_and_name(pairing: PairingService, tmp_path: Path):
    token = _approve_one(pairing, "staff-1")
    pairing.rename_device(pairing.list_devices()[0].record_id, "입구1번")
    assert pairing.revoke_token(token)
    assert pairing.device_id_for_token(token) is None
    reloaded = PairingService(str(tmp_path / "devices.json"))
    devices = reloaded.list_devices()
    assert len(devices) == 1
    assert devices[0].revoked is True
    assert devices[0].custom_name == "입구1번"


def test_revoke_all_preserves_records(pairing: PairingService):
    _approve_one(pairing, "a")
    _approve_one(pairing, "b")
    assert pairing.revoke_all() == 2
    devices = pairing.list_devices()
    assert len(devices) == 2
    assert all(d.revoked for d in devices)


def test_repair_with_same_device_uid_merges_record(pairing: PairingService, tmp_path: Path):
    uid = "11111111-2222-4333-8444-555555555555"
    old_token = _approve_one(pairing, "staff-1", uid)
    pairing.rename_device(pairing.list_devices()[0].record_id, "입구1번")
    pairing.revoke_token(old_token)

    new_token = _approve_one(pairing, "staff-1-renamed", uid)
    devices = pairing.list_devices()
    assert len(devices) == 1  # 새 레코드가 아니라 병합
    assert devices[0].custom_name == "입구1번"  # 별칭 유지
    assert devices[0].revoked is False
    # 기존 해시는 과거 이력 조회용으로 보존
    assert pairing.record_for_device_id(pairing._hash_token(old_token)) is not None
    assert pairing.device_id_for_token(new_token)


def test_repair_without_uid_creates_new_record(pairing: PairingService):
    _approve_one(pairing, "a")
    pairing.revoke_all()
    _approve_one(pairing, "a")
    assert len(pairing.list_devices()) == 2


def test_forget_device_removes_record(pairing: PairingService):
    token = _approve_one(pairing)
    record_id = pairing.list_devices()[0].record_id
    assert pairing.forget_device(record_id)
    assert pairing.list_devices() == []
    assert pairing.device_id_for_token(token) is None


def test_reported_name_is_sanitized(pairing: PairingService):
    """폰이 보낸 기기 이름의 제어문자는 저장 전 제거된다 (UI 주입 방지)."""
    token = _approve_one(pairing, "evil\x1b[31m\nphone\x00")
    record = pairing.list_devices()[0]
    assert record.reported_name == "evil[31mphone"
    assert pairing.device_id_for_token(token)


def test_pending_approval_marks_known_device(pairing: PairingService):
    """같은 device_uid의 재페어링 요청은 승인 대기 목록에서 구분 표시된다."""
    uid = "550e8400-e29b-41d4-a716-446655440000"
    _approve_one(pairing, "staff-1", uid)
    pairing.revoke_all()

    known = pairing.request_pair(join_code=pairing.issue_join_code(), device_name="staff-1", device_uid=uid)
    assert known.state == "pending_approval"
    pending = pairing.pending_approvals()
    assert len(pending) == 1 and pending[0].known_device is True

    pairing.reject(pending[0].pair_ticket)
    unknown = pairing.request_pair(join_code=pairing.issue_join_code(), device_name="new", device_uid="other-uid")
    assert unknown.state == "pending_approval"
    assert pairing.pending_approvals()[0].known_device is False


def test_expired_pair_ticket_poll_fails(pairing: PairingService):
    """승인 유효기간이 지난 티켓 폴링은 토큰 없이 실패한다."""
    pending = pairing.request_pair(join_code=pairing.issue_join_code(), device_name="late")
    assert pending.state == "pending_approval"
    pairing._tickets[pending.pair_ticket].expires_at = time.time() - 1
    assert pairing.request_pair(pair_ticket=pending.pair_ticket).state == "error"
    # 만료된 티켓은 승인해도 토큰이 나오지 않는다
    assert not pairing.approve(pending.pair_ticket)
