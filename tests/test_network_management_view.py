"""네트워크 관리 탭 표시 상태 계산 테스트 — Flet 없이 순수 로직만 검증한다."""
from __future__ import annotations

import json
import time
import unittest

from services.pairing_service import DeviceInfo, PendingApproval
from views.network_management_view import (
    build_network_view_state,
    build_ops_device_counts,
)
from views.work_log_flet_view import _ops_record_order_id, build_ops_index


def _device(
    record_id: str = "uid:dev-1",
    *,
    device_uid: str = "dev-1",
    reported_name: str = "Galaxy S24",
    custom_name: str = "",
    last_seen_at: str = "",
    revoked: bool = False,
    device_ids: tuple[str, ...] = ("hash-1",),
) -> DeviceInfo:
    return DeviceInfo(
        record_id=record_id,
        device_uid=device_uid,
        reported_name=reported_name,
        custom_name=custom_name,
        first_seen_at="2026-01-01 00:00:00",
        last_seen_at=last_seen_at,
        revoked=revoked,
        device_ids=device_ids,
    )


def _seen_ago(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - seconds))


class OpsRecordOrderIdTest(unittest.TestCase):
    def test_order_id_column_preferred(self) -> None:
        record = {"order_id": "a1b2", "result_json": '{"order_id": "ZZ"}'}
        self.assertEqual(_ops_record_order_id(record), "A1B2")

    def test_result_json_fallback(self) -> None:
        record = {"order_id": "", "result_json": json.dumps({"order_id": "AAAA1111"})}
        self.assertEqual(_ops_record_order_id(record), "AAAA1111")

    def test_broken_json_returns_empty(self) -> None:
        self.assertEqual(_ops_record_order_id({"result_json": "{broken"}), "")

    def test_index_uses_fallback(self) -> None:
        ops = [{"order_id": "", "result_json": '{"order_id": "B2C3"}', "device_id": "h1"}]
        self.assertIn("B2C3", build_ops_index(ops))


class OpsDeviceCountsTest(unittest.TestCase):
    def test_counts_per_device_id(self) -> None:
        ops = [
            {"device_id": "h1", "state": "succeeded"},
            {"device_id": "h1", "state": "already_processed"},
            {"device_id": "h2", "state": "succeeded"},
            {"device_id": "", "state": "succeeded"},
            {},
        ]
        self.assertEqual(build_ops_device_counts(ops), {"h1": 2, "h2": 1})

    def test_only_completed_states_counted(self) -> None:
        """스캔 접수만으로는 건수가 증가하지 않는다 — 완료 상태만 집계."""
        ops = [
            {"device_id": "h1", "state": "succeeded"},
            {"device_id": "h1", "state": "accepted"},
            {"device_id": "h1", "state": "failed"},
            {"device_id": "h1", "state": "rejected"},
            {"device_id": "h1", "state": "needs_reconciliation"},
            {"device_id": "h1"},  # 구형/누락 상태
        ]
        self.assertEqual(build_ops_device_counts(ops), {"h1": 1})


class NetworkViewStateTest(unittest.TestCase):
    def test_empty_state(self) -> None:
        state = build_network_view_state([], [], [], server_running=False, server_addr="", now=time.time())
        self.assertTrue(state.empty_visible)
        self.assertEqual(state.device_rows, ())
        self.assertEqual(state.server_addr_text, "서버가 꺼져 있습니다")
        self.assertIn("전체 0", state.counts_text)

    def test_online_offline_badges(self) -> None:
        now = time.time()
        devices = [
            _device("uid:a", last_seen_at=_seen_ago(5)),
            _device("uid:b", device_uid="b", last_seen_at=_seen_ago(120), device_ids=("h2",)),
            _device("uid:c", device_uid="c", revoked=True, last_seen_at=_seen_ago(5), device_ids=("h3",)),
        ]
        state = build_network_view_state(devices, [], [], server_running=True, server_addr="https://x", now=now)
        badges = [row.status_text for row in state.device_rows]
        self.assertEqual(badges, ["연결됨", "연결 끊김", "차단됨"])
        self.assertIn("연결됨 1", state.counts_text)
        self.assertFalse(state.device_rows[2].can_revoke)

    def test_pending_rows(self) -> None:
        pending = [PendingApproval(pair_ticket="t1", device_name="Fold", requested_at=time.time())]
        state = build_network_view_state([], pending, [], server_running=True, server_addr="", now=time.time())
        self.assertEqual(state.pending_rows[0].pair_ticket, "t1")
        self.assertEqual(state.pending_rows[0].device_name, "Fold")

    def test_processed_count_uses_all_hashes(self) -> None:
        device = _device(device_ids=("cur", "old"))
        ops = [
            {"device_id": "cur", "state": "succeeded"},
            {"device_id": "old", "state": "already_processed"},
            {"device_id": "other", "state": "succeeded"},
        ]
        state = build_network_view_state([device], [], ops, server_running=True, server_addr="", now=time.time())
        self.assertEqual(state.device_rows[0].processed_text, "처리 2건")

    def test_duplicate_names_get_ordinals(self) -> None:
        devices = [
            _device("uid:a", reported_name="Galaxy"),
            _device("uid:b", device_uid="b", reported_name="Galaxy"),
            _device("uid:c", device_uid="c", reported_name="iPhone"),
        ]
        state = build_network_view_state(devices, [], [], server_running=True, server_addr="", now=time.time())
        names = [row.display_name for row in state.device_rows]
        self.assertEqual(names, ["Galaxy (1)", "Galaxy (2)", "iPhone"])

    def test_custom_name_wins(self) -> None:
        device = _device(custom_name="입구폰")
        state = build_network_view_state([device], [], [], server_running=True, server_addr="", now=time.time())
        self.assertEqual(state.device_rows[0].display_name, "입구폰")

    def test_selected_device_recent_ops(self) -> None:
        device = _device(device_ids=("h1",))
        ops = [
            {"device_id": "h1", "order_id": "A1", "state": "succeeded", "updated_at": "2026-01-01 10:00:00"},
            {"device_id": "h9", "order_id": "A9", "state": "failed", "updated_at": "2026-01-01 10:01:00"},
            {"device_id": "h1", "order_id": "", "result_json": '{"order_id": "A2"}', "state": "succeeded", "updated_at": "2026-01-01 10:02:00"},
        ]
        state = build_network_view_state(
            [device], [], ops, server_running=True, server_addr="", now=time.time(),
            selected_record_id="uid:dev-1",
        )
        self.assertIn("Galaxy S24", state.recent_title)
        self.assertEqual([r.order_id for r in state.recent_rows], ["A2", "A1"])
        self.assertTrue(state.device_rows[0].is_selected)


if __name__ == "__main__":
    unittest.main()
