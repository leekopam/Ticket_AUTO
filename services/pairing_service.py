"""LAN 기기 페어링 서비스.

join_code(일회용·유효기간) → PC 승인 → device_token 발급 흐름을 관리한다.
토큰은 해시(SHA-256)만 `.runtime/api_devices.json`에 저장한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import string
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from project_paths import resolve_project_path


JOIN_CODE_TTL_SEC = 600.0
PAIR_TICKET_TTL_SEC = 300.0
TOKEN_FILE = ".runtime/api_devices.json"
# 잘못된 코드 연속 제출 시 잠금 (브루트포스 방어)
FAILURE_LOCKOUT_THRESHOLD = 10
FAILURE_LOCKOUT_SEC = 60.0


@dataclass
class _JoinCode:
    code: str
    expires_at: float
    used: bool = False


@dataclass
class _PairTicket:
    ticket: str
    device_name: str
    expires_at: float
    state: str = "pending"  # pending | approved | rejected
    device_token: str = ""


@dataclass
class PairRequestResult:
    state: str  # pending_approval | approved | rejected | error
    pair_ticket: str = ""
    device_token: str = ""
    error_code: str = ""


@dataclass
class PendingApproval:
    pair_ticket: str
    device_name: str
    requested_at: float


class PairingService:
    """페어링 코드/승인/토큰 수명주기를 관리한다 (스레드 안전)."""

    def __init__(self, token_store_path: str | None = None):
        self._lock = threading.RLock()
        self._join_codes: dict[str, _JoinCode] = {}
        self._tickets: dict[str, _PairTicket] = {}
        self._consecutive_failures = 0
        self._lockout_until = 0.0
        self._token_hashes: dict[str, str] = {}  # token_hash -> device_name
        self._token_path = (
            Path(token_store_path)
            if token_store_path
            else resolve_project_path(TOKEN_FILE)
        )
        self._load_tokens()

    # ------------------------------------------------------------------
    # join code
    # ------------------------------------------------------------------

    def issue_join_code(self) -> str:
        """일회용 6자리 참가 코드를 발급한다."""
        with self._lock:
            self._join_codes.clear()
            code = "".join(secrets.choice(string.digits) for _ in range(6))
            self._join_codes[code] = _JoinCode(
                code=code, expires_at=time.time() + JOIN_CODE_TTL_SEC
            )
            return code

    def invalidate_join_codes(self) -> None:
        """QR 유출 의심 등으로 발급된 모든 코드를 무효화한다."""
        with self._lock:
            self._join_codes.clear()

    # ------------------------------------------------------------------
    # pair request flow
    # ------------------------------------------------------------------

    def request_pair(self, join_code: str = "", pair_ticket: str = "", device_name: str = "") -> PairRequestResult:
        with self._lock:
            self._purge_expired()

            if pair_ticket:
                return self._poll_ticket(pair_ticket)

            if time.time() < self._lockout_until:
                return PairRequestResult(state="error", error_code="PAIRING_LOCKED")

            entry = self._join_codes.get(join_code or "")
            if entry is None or entry.used:
                self._consecutive_failures += 1
                if self._consecutive_failures >= FAILURE_LOCKOUT_THRESHOLD:
                    self._lockout_until = time.time() + FAILURE_LOCKOUT_SEC
                    self._consecutive_failures = 0
                return PairRequestResult(state="error", error_code="EXPIRED_JOIN_CODE")

            self._consecutive_failures = 0
            entry.used = True
            ticket = secrets.token_urlsafe(24)
            self._tickets[ticket] = _PairTicket(
                ticket=ticket,
                device_name=(device_name or "")[:64],
                expires_at=time.time() + PAIR_TICKET_TTL_SEC,
            )
            return PairRequestResult(state="pending_approval", pair_ticket=ticket)

    def _poll_ticket(self, pair_ticket: str) -> PairRequestResult:
        ticket = self._tickets.get(pair_ticket)
        if ticket is None:
            return PairRequestResult(state="error", error_code="EXPIRED_JOIN_CODE")
        if ticket.state == "approved":
            token = ticket.device_token
            del self._tickets[pair_ticket]
            return PairRequestResult(state="approved", device_token=token)
        if ticket.state == "rejected":
            del self._tickets[pair_ticket]
            return PairRequestResult(state="rejected")
        return PairRequestResult(state="pending_approval", pair_ticket=pair_ticket)

    def pending_approvals(self) -> list[PendingApproval]:
        with self._lock:
            self._purge_expired()
            return [
                PendingApproval(
                    pair_ticket=t.ticket,
                    device_name=t.device_name,
                    requested_at=t.expires_at - PAIR_TICKET_TTL_SEC,
                )
                for t in self._tickets.values()
                if t.state == "pending"
            ]

    def approve(self, pair_ticket: str) -> str:
        """PC 운영자 승인 → device_token 발급. 반환값은 토큰(실패 시 빈 문자열)."""
        with self._lock:
            self._purge_expired()
            ticket = self._tickets.get(pair_ticket)
            if ticket is None or ticket.state != "pending":
                return ""
            token = secrets.token_urlsafe(32)
            updated = {**self._token_hashes, self._hash_token(token): ticket.device_name}
            self._save_tokens(updated)
            self._token_hashes = updated
            ticket.state = "approved"
            ticket.device_token = token
            return token

    def reject(self, pair_ticket: str) -> bool:
        with self._lock:
            self._purge_expired()
            ticket = self._tickets.get(pair_ticket)
            if ticket is None or ticket.state != "pending":
                return False
            ticket.state = "rejected"
            return True

    # ------------------------------------------------------------------
    # token validation / revocation
    # ------------------------------------------------------------------

    def device_name_for_token(self, token: str) -> str | None:
        with self._lock:
            return self._token_hashes.get(self._hash_token(token or ""))

    def device_id_for_token(self, token: str) -> str | None:
        """표시 이름과 무관한 기기별 작업 소유 식별자를 반환한다."""
        with self._lock:
            token_hash = self._hash_token(token or "")
            return token_hash if token_hash in self._token_hashes else None

    def revoke_token(self, token: str) -> bool:
        with self._lock:
            key = self._hash_token(token or "")
            if key not in self._token_hashes:
                return False
            updated = {k: name for k, name in self._token_hashes.items() if k != key}
            self._save_tokens(updated)
            self._token_hashes = updated
            return True

    def revoke_all(self) -> int:
        """행사 종료 시 전체 토큰 폐기."""
        with self._lock:
            count = len(self._token_hashes)
            if count:
                self._save_tokens({})
                self._token_hashes.clear()
            return count

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _purge_expired(self) -> None:
        now = time.time()
        self._join_codes = {
            code: entry
            for code, entry in self._join_codes.items()
            if entry.expires_at > now
        }
        self._tickets = {
            key: ticket
            for key, ticket in self._tickets.items()
            if ticket.expires_at > now
        }

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _save_tokens(self, hashes: dict[str, str]) -> None:
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "devices": [
                {"token_hash": key, "device_name": name}
                for key, name in hashes.items()
            ]
        }
        temp_path = self._token_path.with_name(
            f"{self._token_path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temp_path, self._token_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _load_tokens(self) -> None:
        try:
            data = json.loads(self._token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in data.get("devices", []):
            token_hash = str(item.get("token_hash", ""))
            if token_hash:
                self._token_hashes[token_hash] = str(item.get("device_name", ""))
