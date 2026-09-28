"""LAN 기기 페어링 서비스.

join_code(일회용·유효기간) → PC 승인 → device_token 발급 흐름을 관리한다.
토큰은 해시(SHA-256)만 `.runtime/api_devices.json`에 저장한다.

v2: 기기 레지스트리(별칭·접속 시각·차단 상태·device_uid)를 같은 파일에 저장한다.
- `token_hash`는 인증·작업 소유 식별자. `device_uid`는 폰이 보관하는 설치 식별자로
  재페어링 시 같은 기기로 병합해 별칭과 이력을 유지한다.
- `revoke`는 토큰만 무효화하고 레코드(이름·이력)는 보존한다.
- v1 파일(`token_hash`+`device_name`)은 로드 시 자동 변환하고, 저장 시 `device_name`
  키를 병기해 구 버전 빌드로 되돌려도 인증이 유지되게 한다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import string
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from project_paths import resolve_project_path

logger = logging.getLogger(__name__)


JOIN_CODE_TTL_SEC = 600.0
PAIR_TICKET_TTL_SEC = 300.0
TOKEN_FILE = ".runtime/api_devices.json"
# 잘못된 코드 연속 제출 시 잠금 (브루트포스 방어)
FAILURE_LOCKOUT_THRESHOLD = 10
FAILURE_LOCKOUT_SEC = 60.0
# 별칭 입력 제한
ALIAS_MAX_LENGTH = 20
# 폰이 보고한 기기 이름 제한 — 별칭보다 길게 받되 제어문자는 걸러낸다
REPORTED_NAME_MAX_LENGTH = 64
# last_seen 디스크 반영 간격 — 매 요청 쓰기는 불필요한 I/O라 스로틀한다
ACTIVITY_SAVE_INTERVAL_SEC = 60.0
# 토큰 슬라이딩 만료 — 마지막 인증 활동부터 24시간. 방치·분실 폰이 계속 유효하지 않게 한다
TOKEN_TTL_SEC = 24 * 3600.0
_DEVICE_UID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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
    device_uid: str = ""


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
    # 같은 device_uid 레코드가 이미 있으면 True — 운영자가 위장 재페어링을 구분하게 한다
    known_device: bool = False


@dataclass
class _DeviceRecord:
    """디스크에 저장되는 기기 한 대의 상태."""

    record_id: str  # "uid:<device_uid>" 또는 uid가 없으면 "hash:<token_hash>"
    token_hash: str = ""
    token_expires_at: float = 0.0  # 0 = 만료 없음 (이전 버전에서 발급된 토큰)
    previous_token_hashes: list[str] = field(default_factory=list)
    device_uid: str = ""
    reported_name: str = ""
    custom_name: str = ""
    first_seen_at: str = ""
    last_seen_at: str = ""
    revoked: bool = False


@dataclass(frozen=True)
class DeviceInfo:
    """UI에 노출하는 기기 정보 스냅샷."""

    record_id: str
    device_uid: str
    reported_name: str
    custom_name: str
    first_seen_at: str
    last_seen_at: str
    revoked: bool
    device_ids: tuple[str, ...] = ()  # 현재+과거 토큰 해시 — 작업 이력 집계용


def sanitize_alias(value: str) -> str:
    """별칭 입력을 정규화한다: 공백 트림 + 제어문자 제거 + 길이 제한."""
    text = "".join(ch for ch in str(value or "").strip() if ch.isprintable())
    return text[:ALIAS_MAX_LENGTH]


def sanitize_reported_name(value: str) -> str:
    """폰이 보낸 기기 이름을 정규화한다 — UI 표시되므로 제어문자를 걸러낸다."""
    text = "".join(ch for ch in str(value or "").strip() if ch.isprintable())
    return text[:REPORTED_NAME_MAX_LENGTH]


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class PairingService:
    """페어링 코드/승인/토큰 수명주기와 기기 레지스트리를 관리한다 (스레드 안전)."""

    def __init__(self, token_store_path: str | None = None, *, token_ttl_sec: float = TOKEN_TTL_SEC):
        self._lock = threading.RLock()
        self._token_ttl_sec = float(token_ttl_sec)
        self._join_codes: dict[str, _JoinCode] = {}
        self._tickets: dict[str, _PairTicket] = {}
        self._consecutive_failures = 0
        self._lockout_until = 0.0
        self._records: dict[str, _DeviceRecord] = {}  # record_id -> record
        self._token_path = (
            Path(token_store_path)
            if token_store_path
            else resolve_project_path(TOKEN_FILE)
        )
        self._last_persisted = 0.0
        self._dirty = False
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

    def request_pair(
        self,
        join_code: str = "",
        pair_ticket: str = "",
        device_name: str = "",
        device_uid: str = "",
    ) -> PairRequestResult:
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
                    logger.warning("참가 코드 연속 실패 — %d초 잠금", int(FAILURE_LOCKOUT_SEC))
                return PairRequestResult(state="error", error_code="EXPIRED_JOIN_CODE")

            self._consecutive_failures = 0
            entry.used = True
            ticket = secrets.token_urlsafe(24)
            uid = str(device_uid or "")[:64]
            if not _DEVICE_UID_PATTERN.match(uid):
                uid = ""
            self._tickets[ticket] = _PairTicket(
                ticket=ticket,
                device_name=sanitize_reported_name(device_name),
                device_uid=uid,
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
                    known_device=bool(
                        t.device_uid
                        and self._find_by_uid(self._records.values(), t.device_uid)
                    ),
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
            token_hash = self._hash_token(token)
            records = dict(self._records)
            record = self._find_by_uid(records.values(), ticket.device_uid)
            now = _now_str()
            if record is None:
                uid = ticket.device_uid
                record = _DeviceRecord(
                    record_id=f"uid:{uid}" if uid else f"hash:{token_hash}",
                    device_uid=uid,
                    first_seen_at=now,
                )
            else:
                # 저장 실패 시 인메모리 오염을 막기 위해 복사본을 바꾼다
                record = replace(
                    record, previous_token_hashes=list(record.previous_token_hashes)
                )
                # 같은 device_uid로 재페어링: 토큰만 교체하고 별칭·이력을 유지한다
                if record.token_hash and record.token_hash != token_hash:
                    record.previous_token_hashes.append(record.token_hash)
            record.token_hash = token_hash
            record.token_expires_at = (
                time.time() + self._token_ttl_sec if self._token_ttl_sec > 0 else 0.0
            )
            record.device_uid = ticket.device_uid or record.device_uid
            record.reported_name = ticket.device_name or record.reported_name
            record.last_seen_at = now
            record.revoked = False
            records[record.record_id] = record
            self._save_tokens(records)
            self._records = records
            ticket.state = "approved"
            ticket.device_token = token
            logger.info("기기 페어링 승인: %s (%s)", record.record_id, record.reported_name)
            return token

    def reject(self, pair_ticket: str) -> bool:
        with self._lock:
            self._purge_expired()
            ticket = self._tickets.get(pair_ticket)
            if ticket is None or ticket.state != "pending":
                return False
            ticket.state = "rejected"
            logger.info("기기 페어링 거절: %s", ticket.device_name)
            return True

    # ------------------------------------------------------------------
    # token validation / revocation
    # ------------------------------------------------------------------

    def _find_by_token_hash(self, token_hash: str) -> _DeviceRecord | None:
        now = time.time()
        for record in self._records.values():
            # 상수 시간 비교 — 토큰 해시 부분 일치 정보 유출 방지
            if not record.revoked and hmac.compare_digest(
                record.token_hash, token_hash
            ):
                if record.token_expires_at and record.token_expires_at <= now:
                    return None  # 만료된 토큰은 차단과 동일하게 거절
                return record
        return None

    @staticmethod
    def _find_by_uid(records, device_uid: str) -> _DeviceRecord | None:
        if not device_uid:
            return None
        for record in records:
            if record.device_uid == device_uid:
                return record
        return None

    def device_name_for_token(self, token: str) -> str | None:
        with self._lock:
            record = self._find_by_token_hash(self._hash_token(token or ""))
            return record.reported_name if record else None

    def device_id_for_token(self, token: str) -> str | None:
        """표시 이름과 무관한 기기별 작업 소유 식별자를 반환한다."""
        with self._lock:
            token_hash = self._hash_token(token or "")
            record = self._find_by_token_hash(token_hash)
            return token_hash if record else None

    def record_for_device_id(self, device_id: str) -> DeviceInfo | None:
        """과거 해시 포함해 작업 소유 식별자로 기기 레코드를 조회한다."""
        with self._lock:
            for record in self._records.values():
                if record.token_hash == device_id or device_id in record.previous_token_hashes:
                    return self._to_info(record)
            return None

    def record_activity(self, device_id: str, *, force_save: bool = False) -> None:
        """인증 성공한 기기의 활동 시각을 갱신한다. 디스크 반영은 스로틀한다."""
        with self._lock:
            record = self._find_by_token_hash(device_id)
            if record is None:
                return
            record.last_seen_at = _now_str()
            now = time.time()
            # 슬라이딩 만료 — 활동 중인 기기의 토큰 수명을 연장한다
            if self._token_ttl_sec > 0:
                record.token_expires_at = now + self._token_ttl_sec
            if force_save or now - self._last_persisted >= ACTIVITY_SAVE_INTERVAL_SEC:
                try:
                    self._save_tokens(self._records)
                except OSError:
                    # 활동 시각 저장 실패가 인증을 깨면 안 된다
                    logger.warning("기기 활동 시각 저장 실패", exc_info=True)
                    self._dirty = True
                    return
                self._last_persisted = now
                self._dirty = False
            else:
                self._dirty = True

    def flush(self) -> None:
        """미반영 활동 시각을 디스크에 기록한다 (앱 종료 시 호출)."""
        with self._lock:
            if self._dirty:
                try:
                    self._save_tokens(self._records)
                except OSError:
                    logger.warning("기기 활동 시각 flush 실패", exc_info=True)
                    return
                self._dirty = False
                self._last_persisted = time.time()

    def revoke_token(self, token: str) -> bool:
        """토큰을 폐기한다. 레코드와 별칭은 revoked 상태로 보존한다."""
        with self._lock:
            key = self._hash_token(token or "")
            found = self._find_by_token_hash(key)
            if found is None:
                return False
            records = dict(self._records)
            record = replace(
                found, previous_token_hashes=list(found.previous_token_hashes)
            )
            if record.token_hash:
                record.previous_token_hashes.append(record.token_hash)
            record.token_hash = ""
            record.revoked = True
            records[record.record_id] = record
            self._save_tokens(records)
            self._records = records
            logger.info("기기 토큰 폐기: %s", record.record_id)
            return True

    def revoke_device(self, record_id: str) -> bool:
        """레코드 ID로 기기를 차단한다. 레코드와 이름은 보존한다."""
        with self._lock:
            found = self._records.get(record_id or "")
            if found is None or found.revoked:
                return False
            records = dict(self._records)
            record = replace(
                found, previous_token_hashes=list(found.previous_token_hashes)
            )
            if record.token_hash:
                record.previous_token_hashes.append(record.token_hash)
            record.token_hash = ""
            record.revoked = True
            records[record.record_id] = record
            self._save_tokens(records)
            self._records = records
            logger.info("기기 차단: %s", record_id)
            return True

    def revoke_all(self) -> int:
        """행사 종료 시 전체 토큰 폐기. 레코드는 보존하고 revoked로 전환한다."""
        with self._lock:
            active = [r for r in self._records.values() if not r.revoked]
            if not active:
                return 0
            records = {}
            for record in self._records.values():
                if record.revoked:
                    records[record.record_id] = record
                    continue
                record = replace(
                    record, previous_token_hashes=list(record.previous_token_hashes)
                )
                if record.token_hash:
                    record.previous_token_hashes.append(record.token_hash)
                record.token_hash = ""
                record.revoked = True
                records[record.record_id] = record
            self._save_tokens(records)
            self._records = records
            logger.info("전체 기기 차단: %d대", len(active))
            return len(active)

    # ------------------------------------------------------------------
    # 기기 목록 / 관리
    # ------------------------------------------------------------------

    def list_devices(self) -> list[DeviceInfo]:
        """등록된 전체 기기를 최근 활동순으로 반환한다."""
        with self._lock:
            devices = [self._to_info(r) for r in self._records.values()]
        devices.sort(key=lambda d: d.last_seen_at, reverse=True)
        return devices

    def rename_device(self, record_id: str, alias: str) -> bool:
        """별칭을 설정한다. 빈 값이면 해제하고 원래 이름 표기로 돌아간다."""
        raw = str(alias or "")
        if len(raw.strip()) > ALIAS_MAX_LENGTH:
            return False
        with self._lock:
            record = self._records.get(record_id or "")
            if record is None:
                return False
            records = dict(self._records)
            record = replace(record, custom_name=sanitize_alias(raw))
            records[record_id] = record
            self._save_tokens(records)
            self._records = records
            return True

    def forget_device(self, record_id: str) -> bool:
        """레코드를 완전히 삭제한다(이력 보존이 필요 없을 때만 사용)."""
        with self._lock:
            if record_id not in self._records:
                return False
            records = dict(self._records)
            del records[record_id]
            self._save_tokens(records)
            self._records = records
            return True

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

    @staticmethod
    def _to_info(record: _DeviceRecord) -> DeviceInfo:
        return DeviceInfo(
            record_id=record.record_id,
            device_uid=record.device_uid,
            reported_name=record.reported_name,
            custom_name=record.custom_name,
            first_seen_at=record.first_seen_at,
            last_seen_at=record.last_seen_at,
            revoked=record.revoked,
            device_ids=tuple(
                h for h in [record.token_hash, *record.previous_token_hashes] if h
            ),
        )

    def _save_tokens(self, records: dict[str, _DeviceRecord]) -> None:
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "devices": [
                {
                    # v1 호환: 구 버전 로더는 token_hash/device_name만 읽는다
                    "token_hash": record.token_hash,
                    "token_expires_at": record.token_expires_at,
                    "device_name": record.reported_name,
                    "device_uid": record.device_uid,
                    "previous_token_hashes": list(record.previous_token_hashes),
                    "reported_name": record.reported_name,
                    "custom_name": record.custom_name,
                    "first_seen_at": record.first_seen_at,
                    "last_seen_at": record.last_seen_at,
                    "revoked": record.revoked,
                }
                for record in records.values()
            ],
        }
        temp_path = self._token_path.with_name(
            f"{self._token_path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temp_path, self._token_path)
            self._last_persisted = time.time()
        finally:
            temp_path.unlink(missing_ok=True)

    def _load_tokens(self) -> None:
        if not self._token_path.exists():
            return
        try:
            data = json.loads(self._token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 손상 파일은 지우지 않고 보존한 뒤 빈 상태로 시작한다
            backup = self._token_path.with_name(
                f"{self._token_path.name}.corrupt-{int(time.time())}"
            )
            try:
                os.replace(self._token_path, backup)
            except OSError:
                pass
            return
        for item in data.get("devices", []):
            token_hash = str(item.get("token_hash", ""))
            reported = str(item.get("reported_name") or item.get("device_name") or "")
            device_uid = str(item.get("device_uid", ""))
            if not _DEVICE_UID_PATTERN.match(device_uid):
                device_uid = ""
            record_id = f"uid:{device_uid}" if device_uid else f"hash:{token_hash}"
            try:
                token_expires_at = float(item.get("token_expires_at") or 0.0)
            except (TypeError, ValueError):
                token_expires_at = 0.0
            record = _DeviceRecord(
                record_id=record_id,
                token_hash=token_hash,
                token_expires_at=token_expires_at,
                previous_token_hashes=[
                    str(v) for v in item.get("previous_token_hashes", []) if str(v)
                ],
                device_uid=device_uid,
                reported_name=reported,
                custom_name=str(item.get("custom_name", "")),
                first_seen_at=str(item.get("first_seen_at", "")),
                last_seen_at=str(item.get("last_seen_at", "")),
                revoked=bool(item.get("revoked", False)),
            )
            self._records[record.record_id] = record
