"""기기 연결 상태와 표시 이름 계산 — 순수 함수만 둔다 (시간/IO 주입 가능).

네트워크 관리 탭과 티켓 업무 탭이 같은 판정 규칙을 공유한다.
"""
from __future__ import annotations

import time
from typing import Callable, Mapping, Protocol


# 하트비트 15초의 3회 미스 허용치
ONLINE_THRESHOLD_SEC = 45.0

# 연결 신호 단계 경계(초) — 하트비트 주기 대비 마지막 관측 경과시간
SIGNAL_GOOD_SEC = 20.0
SIGNAL_FAIR_SEC = ONLINE_THRESHOLD_SEC
SIGNAL_STALE_SEC = 120.0

PRESENCE_ONLINE = "online"
PRESENCE_OFFLINE = "offline"
PRESENCE_REVOKED = "revoked"
PRESENCE_PENDING = "pending"

# 연결 품질 판정 임계 — PC 네트워크 탭과 /v1/devices(모바일)가 같은 규칙을 공유한다
QUALITY_RTT_WARN_MS = 80
QUALITY_RTT_POOR_MS = 200
QUALITY_MISSED_POOR = 3

UNKNOWN_DEVICE_LABEL = "알 수 없는 기기"
PC_DEVICE_LABEL = "PC"


class DeviceLike(Protocol):
    """presence 계산에 필요한 최소 기기 정보."""

    reported_name: str
    custom_name: str
    last_seen_at: str
    revoked: bool


def parse_seen_at(value: str) -> float | None:
    """'YYYY-MM-DD HH:MM:SS' 형식을 epoch 초로 변환한다."""
    try:
        return time.mktime(time.strptime(value.strip(), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def presence_state(
    device: DeviceLike,
    now: float,
    *,
    pending: bool = False,
    threshold_sec: float = ONLINE_THRESHOLD_SEC,
) -> str:
    """차단 > 승인 대기 > 연결됨 > 끊김 우선순위로 상태를 판정한다."""
    if device.revoked:
        return PRESENCE_REVOKED
    if pending:
        return PRESENCE_PENDING
    seen = parse_seen_at(device.last_seen_at)
    if seen is not None and now - seen < threshold_sec:
        return PRESENCE_ONLINE
    return PRESENCE_OFFLINE


def signal_level(last_seen_at: str, now: float) -> int:
    """연결 신호 0~3단계 — 하트비트(15초) 대비 마지막 관측 경과시간 기준.

    3=양호(<20초), 2=보통(<45초, 연결됨 판정과 동일 경계), 1=불안정(<120초), 0=끊김.
    """
    seen = parse_seen_at(last_seen_at)
    if seen is None:
        return 0
    age = now - seen
    if age < SIGNAL_GOOD_SEC:
        return 3
    if age < SIGNAL_FAIR_SEC:
        return 2
    if age < SIGNAL_STALE_SEC:
        return 1
    return 0


def connection_quality(
    last_rtt_ms: int | None,
    missed_beats: int,
    presence: str,
    *,
    server_running: bool = True,
) -> tuple[str, str, int]:
    """연결 품질 (상태키, 라벨, 채워진 막대 수) — 폰이 보고한 RTT와 누락 하트비트 기준.

    PC 네트워크 탭과 모바일 내 연결 상태 화면이 이 결과를 그대로 표시해
    양쪽 표시가 항상 일치한다. 측정 불가 상태는 막대 0칸.
    """
    if not server_running:
        return "inactive", "측정 중지", 0
    if presence == PRESENCE_REVOKED:
        return "inactive", "차단됨", 0
    if presence != PRESENCE_ONLINE:
        return "inactive", "연결 없음", 0
    if last_rtt_ms is None:
        return "loading", "확인 중", 0
    missed = missed_beats or 0
    if last_rtt_ms >= QUALITY_RTT_POOR_MS or missed >= QUALITY_MISSED_POOR:
        return "poor", "불안정", 1
    if last_rtt_ms >= QUALITY_RTT_WARN_MS or missed > 0:
        return "warn", "지연 주의", 2
    return "good", "양호", 4


def display_name(device: DeviceLike, duplicate_ordinal: int | None = None) -> str:
    """별칭 우선, 없으면 보고 이름. 중복 그룹에서는 '(1)' 형태의 순번을 붙인다."""
    name = device.custom_name or device.reported_name or "휴대폰"
    if duplicate_ordinal is not None:
        return f"{name} ({duplicate_ordinal + 1})"
    return name


def display_names(devices: list[DeviceLike]) -> dict[int, str]:
    """이름이 겹치는 기기에만 순번을 붙인 표시 이름 맵(id(device)→이름)을 만든다."""
    counts: dict[str, int] = {}
    for device in devices:
        base = device.custom_name or device.reported_name or "휴대폰"
        counts[base] = counts.get(base, 0) + 1
    ordinals: dict[str, int] = {}
    result: dict[int, str] = {}
    for device in devices:
        base = device.custom_name or device.reported_name or "휴대폰"
        ordinal = ordinals.get(base, 0)
        ordinals[base] = ordinal + 1
        result[id(device)] = display_name(
            device, ordinal if counts[base] > 1 else None
        )
    return result


def operator_label(
    ops_record: Mapping[str, str] | None,
    device_lookup: Callable[[str], DeviceLike | None],
    *,
    order_received: bool,
) -> str:
    """처리 단말 표기: 현재 이름 → 스냅샷 → 단축 해시 → PC 폴백 순으로 결정한다.

    - ops_record None + 수령 완료: _operations에 없는 PC 본체 처리로 간주해 "PC"
    - 레코드 있음: 레지스트리 현재 이름(별칭 우선) → device_name 스냅샷 → 앞 6자리 해시
    """
    if ops_record is None:
        return PC_DEVICE_LABEL if order_received else ""
    device_id = str(ops_record.get("device_id") or "").strip()
    if not device_id:
        return PC_DEVICE_LABEL if order_received else ""
    info = device_lookup(device_id)
    if info is not None:
        return display_name(info)
    snapshot = str(ops_record.get("device_name") or "").strip()
    if snapshot:
        return snapshot
    return f"{UNKNOWN_DEVICE_LABEL} ({device_id[:6]})"
