"""device_presence 순수 함수 테스트 — 연결 상태 판정·표시 이름·처리 단말 표기."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from services.device_presence import (
    ONLINE_THRESHOLD_SEC,
    PC_DEVICE_LABEL,
    display_name,
    display_names,
    operator_label,
    parse_seen_at,
    presence_state,
)


@dataclass
class _Device:
    reported_name: str = "staff-1"
    custom_name: str = ""
    last_seen_at: str = ""
    revoked: bool = False


def _seen_at(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - seconds_ago))


class PresenceStateTest:
    pass


def test_online_within_threshold():
    device = _Device(last_seen_at=_seen_at(10))
    assert presence_state(device, time.time()) == "online"


def test_boundary_45_seconds():
    # last_seen 문자열은 초 단위라 1초 여유를 둔 경계로 검증한다
    now = time.time()
    inside = _Device(last_seen_at=_seen_at(ONLINE_THRESHOLD_SEC - 2))
    outside = _Device(last_seen_at=_seen_at(ONLINE_THRESHOLD_SEC + 2))
    assert presence_state(inside, now) == "online"
    assert presence_state(outside, now) == "offline"


def test_revoked_beats_online_and_pending():
    device = _Device(last_seen_at=_seen_at(1), revoked=True)
    assert presence_state(device, time.time(), pending=True) == "revoked"


def test_pending_beats_offline():
    device = _Device()
    assert presence_state(device, time.time(), pending=True) == "pending"


def test_never_seen_is_offline():
    assert presence_state(_Device(), time.time()) == "offline"


def test_display_name_alias_wins():
    assert display_name(_Device(reported_name="Galaxy", custom_name="입구1")) == "입구1"


def test_display_name_duplicate_ordinal():
    assert display_name(_Device(reported_name="Galaxy"), 1) == "Galaxy (2)"


def test_display_names_numbers_only_duplicates():
    devices = [
        _Device(reported_name="Galaxy S24"),
        _Device(reported_name="Galaxy S24"),
        _Device(reported_name="iPhone"),
    ]
    names = display_names(devices)
    assert names[id(devices[0])] == "Galaxy S24 (1)"
    assert names[id(devices[1])] == "Galaxy S24 (2)"
    assert names[id(devices[2])] == "iPhone"


def test_operator_label_pc_fallback():
    assert operator_label(None, lambda _d: None, order_received=True) == PC_DEVICE_LABEL
    assert operator_label(None, lambda _d: None, order_received=False) == ""


def test_operator_label_prefers_registry_name():
    record = {"device_id": "abc123", "device_name": "옛이름"}
    info = _Device(reported_name="Galaxy", custom_name="입구1")
    assert operator_label(record, lambda _d: info, order_received=True) == "입구1"


def test_operator_label_snapshot_then_short_hash():
    record = {"device_id": "abc123def", "device_name": "스냅샷폰"}
    assert operator_label(record, lambda _d: None, order_received=True) == "스냅샷폰"
    record2 = {"device_id": "abc123def", "device_name": ""}
    assert "abc123" in operator_label(record2, lambda _d: None, order_received=True)


def test_parse_seen_at_invalid():
    assert parse_seen_at("") is None
    assert parse_seen_at("not a time") is None
