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
