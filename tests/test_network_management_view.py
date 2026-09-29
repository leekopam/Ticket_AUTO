"""네트워크 관리 탭 표시 상태 계산 테스트 — Flet 없이 순수 로직만 검증한다."""
from __future__ import annotations

import json
import time
import unittest

from models.order_model import Order
from services.pairing_service import DeviceInfo, PendingApproval
from views.network_management_view import (
    build_device_history,
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
    last_rtt_ms: int | None = None,
    beat_interval_sec: float | None = None,
    missed_beats: int = 0,
) -> DeviceInfo:
    return DeviceInfo(
        record_id=record_id,
        device_uid=device_uid,
        reported_name=reported_name,
        custom_name=custom_name,
        first_seen_at="",
        last_seen_at=last_seen_at,
        revoked=revoked,
        device_ids=device_ids,
        last_rtt_ms=last_rtt_ms,
        beat_interval_sec=beat_interval_sec,
        missed_beats=missed_beats,
    )


def _seen_ago(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - seconds))


class OpsHelpersTest(unittest.TestCase):
    def test_order_id_column_preferred(self) -> None:
        record = {"order_id": "a1", "result_json": '{"order_id": "B2"}'}
        self.assertEqual(_ops_record_order_id(record), "A1")

    def test_result_json_fallback(self) -> None:
        record = {"result_json": json.dumps({"order_id": "b2c3"})}
        self.assertEqual(_ops_record_order_id(record), "B2C3")

    def test_broken_json_returns_empty(self) -> None:
        self.assertEqual(_ops_record_order_id({"result_json": "{"}), "")

    def test_index_uses_fallback(self) -> None:
        ops = [{"result_json": '{"order_id": "B2C3"}', "device_id": "d1"}]
        self.assertIn("B2C3", build_ops_index(ops))

    def test_counts_per_device_id(self) -> None:
        ops = [
            {"device_id": "h1", "state": "succeeded"},
            {"device_id": "h1", "state": "already_processed"},
            {"device_id": "h2", "state": "succeeded"},
            {"device_id": "h1", "state": "failed"},
        ]
        self.assertEqual(build_ops_device_counts(ops), {"h1": 2, "h2": 1})

    def test_only_completed_states_counted(self) -> None:
        ops = [
            {"device_id": "h1", "state": "succeeded"},
            {"device_id": "h1", "state": "failed"},
            {"device_id": "h1", "state": "rejected"},
            {"device_id": "h1", "state": "already_processed"},
        ]
        self.assertEqual(build_ops_device_counts(ops), {"h1": 2})


class NetworkViewStateTest(unittest.TestCase):
    def test_empty_state(self) -> None:
        state = build_network_view_state([], [], [], server_running=False, server_addr="", now=time.time())
        self.assertTrue(state.empty_visible)
        self.assertFalse(state.empty_filtered)
        self.assertEqual(state.device_rows, ())
        self.assertEqual(state.online_count, 0)
        self.assertEqual(state.server_status_text, "서버 중지됨")

    def test_status_counts_and_badges(self) -> None:
        now = time.time()
        devices = [
            _device("uid:a", last_seen_at=_seen_ago(5)),
            _device("uid:b", device_uid="b", last_seen_at=_seen_ago(120), device_ids=("h2",)),
            _device("uid:c", device_uid="c", revoked=True, last_seen_at=_seen_ago(5), device_ids=("h3",)),
        ]
        state = build_network_view_state(devices, [], [], server_running=True, server_addr="https://x", now=now)
        self.assertEqual([row.status_text for row in state.device_rows], ["연결됨", "연결 끊김", "차단됨"])
        self.assertEqual((state.online_count, state.offline_count, state.blocked_count), (1, 1, 1))
        self.assertEqual(state.server_status_text, "서버 실행 중")

    def test_action_flags_by_status(self) -> None:
        """온라인=연결 해제+차단, 오프라인=재연결 요청+차단, 차단=차단 해제."""
        now = time.time()
        devices = [
            _device("uid:a", last_seen_at=_seen_ago(5)),
            _device("uid:b", device_uid="b", last_seen_at=_seen_ago(120), device_ids=("h2",)),
            _device("uid:c", device_uid="c", revoked=True, last_seen_at=_seen_ago(5), device_ids=("h3",)),
        ]
        state = build_network_view_state(devices, [], [], server_running=True, server_addr="", now=now)
        online, offline, blocked = state.device_rows
        self.assertTrue(online.can_disconnect and online.can_revoke)
        self.assertFalse(online.can_reconnect or online.can_unblock)
        self.assertTrue(offline.can_reconnect and offline.can_revoke)
        self.assertFalse(offline.can_disconnect or offline.can_unblock)
        self.assertTrue(blocked.can_unblock)
        self.assertFalse(blocked.can_revoke or blocked.can_disconnect)

    def test_signal_level_reflects_last_seen(self) -> None:
        """기기 행의 신호 단계는 마지막 활동 경과시간으로 산출된다."""
        now = time.time()
        devices = [
            _device("uid:a", last_seen_at=_seen_ago(5)),                       # 양호 3
            _device("uid:b", device_uid="b", last_seen_at=_seen_ago(30)),      # 보통 2
            _device("uid:c", device_uid="c", last_seen_at=_seen_ago(90)),      # 불안정 1
            _device("uid:d", device_uid="d", last_seen_at=_seen_ago(600)),     # 끊김 0
            _device("uid:e", device_uid="e", revoked=True, last_seen_at=_seen_ago(5)),  # 차단 → 0
        ]
        state = build_network_view_state(devices, [], [], server_running=True, server_addr="", now=now)
        self.assertEqual([r.signal_level for r in state.device_rows], [3, 2, 1, 0, 0])

    def test_quality_metrics_text(self) -> None:
        """행 품질 지표 — 폰 보고 응답시간과 누락 횟수를 표시."""
        now = time.time()
        device = _device(
            last_seen_at=_seen_ago(5),
            last_rtt_ms=23,
            missed_beats=2,
        )
        state = build_network_view_state([device], [], [], server_running=True, server_addr="", now=now)
        self.assertEqual(state.device_rows[0].rtt_text, "23")
        self.assertEqual(state.device_rows[0].missed_text, "2")
        # 누락 2건이면 '지연 주의'로 판정된다
        self.assertEqual(state.device_rows[0].quality_key, "warn")
        self.assertEqual(state.device_rows[0].quality_label, "지연 주의")
        # 측정치가 없는 기기는 '—' 표기로 내려간다
        state2 = build_network_view_state([_device()], [], [], server_running=True, server_addr="", now=now)
        self.assertEqual(state2.device_rows[0].rtt_text, "—")
        self.assertEqual(state2.device_rows[0].missed_text, "0")

    def test_last_activity_text(self) -> None:
        now = time.time()
        devices = [
            _device("uid:a", last_seen_at=_seen_ago(5)),
            _device("uid:b", device_uid="b", last_seen_at=_seen_ago(120), device_ids=("h2",)),
            _device("uid:c", device_uid="c", last_seen_at="", device_ids=("h3",)),
        ]
        state = build_network_view_state(devices, [], [], server_running=True, server_addr="", now=now)
        self.assertEqual(state.device_rows[0].last_activity_text, "현재 연결됨")
        self.assertRegex(state.device_rows[1].last_activity_text, r"^\d{2}\.\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(state.device_rows[2].last_activity_text, "—")

    def test_pending_rows(self) -> None:
        pending = [PendingApproval(pair_ticket="t1", device_name="Fold", requested_at=time.time())]
        state = build_network_view_state([], pending, [], server_running=True, server_addr="", now=time.time())
        self.assertEqual(state.pending_rows[0].pair_ticket, "t1")
        self.assertEqual(state.pending_rows[0].device_name, "Fold")
        self.assertEqual(state.pending_count, 1)

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

    def test_filter_by_status(self) -> None:
        now = time.time()
        devices = [
            _device("uid:a", last_seen_at=_seen_ago(5)),
            _device("uid:b", device_uid="b", last_seen_at=_seen_ago(120), device_ids=("h2",)),
            _device("uid:c", device_uid="c", revoked=True, last_seen_at=_seen_ago(5), device_ids=("h3",)),
        ]
        online = build_network_view_state(devices, [], [], server_running=True, server_addr="", now=now, filter_key="online")
        self.assertEqual([r.status_key for r in online.device_rows], ["online"])
        blocked = build_network_view_state(devices, [], [], server_running=True, server_addr="", now=now, filter_key="blocked")
        self.assertEqual([r.status_key for r in blocked.device_rows], ["revoked"])

    def test_search_by_name(self) -> None:
        devices = [
            _device("uid:a", reported_name="Galaxy S24"),
            _device("uid:b", device_uid="b", reported_name="iPhone 15", device_ids=("h2",)),
        ]
        state = build_network_view_state(devices, [], [], server_running=True, server_addr="", now=time.time(), query="iphone")
        self.assertEqual([r.display_name for r in state.device_rows], ["iPhone 15"])
        self.assertTrue(state.empty_visible is False)

    def test_filtered_empty_flag(self) -> None:
        devices = [_device("uid:a", reported_name="Galaxy S24")]
        state = build_network_view_state(devices, [], [], server_running=True, server_addr="", now=time.time(), query="없음")
        self.assertTrue(state.empty_visible)
        self.assertTrue(state.empty_filtered)

    def test_device_history_rows(self) -> None:
        device = _device(device_ids=("h1",))
        ops = [
            {"device_id": "h1", "order_id": "A1", "state": "succeeded", "updated_at": "2026-01-01 10:00:00"},
            {"device_id": "h9", "order_id": "A9", "state": "succeeded", "updated_at": "2026-01-01 10:01:00"},
            {"device_id": "h1", "order_id": "", "result_json": '{"order_id": "A2"}', "state": "succeeded", "updated_at": "2026-01-01 10:02:00"},
            {"device_id": "h1", "order_id": "", "state": "succeeded", "updated_at": "2026-01-01 10:03:00"},
        ]
        orders = [Order(order_number=oid, name="손님", goods=["상품 x1"]) for oid in ("A1", "A2", "A9")]
        rows = build_device_history(device, ops, orders=orders)
        # 최신순으로 h1의 주문 조인 성공 건만 — 다른 기기·주문 없는 기록은 제외
        self.assertEqual([r.order_id for r in rows], ["A2", "A1"])

    def test_device_history_joins_order_goods(self) -> None:
        """내역 다이얼로그는 주문 데이터와 조인해 주문자·티켓/상품을 보여준다."""
        device = _device(device_ids=("h1",))
        ops = [
            {"device_id": "h1", "order_id": "A1", "state": "succeeded", "updated_at": "2026-01-01 10:00:00"},
            {"device_id": "h1", "order_id": "ZZZ", "state": "succeeded", "updated_at": "2026-01-01 10:01:00"},
            {"device_id": "h1", "order_id": "A2", "state": "failed", "updated_at": "2026-01-01 10:02:00"},
        ]
        orders = [
            Order(
                order_number="A1",
                name="김철수",
                goods=["입장권 x1", "아메리카노 x2"],
            ),
            Order(order_number="A2", name="박영희", goods=["콜라 x1"]),
        ]
        rows = build_device_history(device, ops, orders=orders, ticket_names={"입장권"})
        # ZZZ는 주문 데이터 없음, A2는 처리 미완료 — 둘 다 제외되고 A1만 남는다
        self.assertEqual(len(rows), 1)
        joined = rows[0]
        self.assertEqual(joined.customer_name, "김철수")
        self.assertEqual(joined.ticket_items, ("입장권 x1",))
        self.assertEqual(joined.goods_items, ("아메리카노 x2",))

    def test_device_quality_labels(self) -> None:
        """품질 라벨 — 서버 중지/차단/오프라인/미측정/양호 구분."""
        now = time.time()
        cases = [
            (_device(last_seen_at=_seen_ago(5), last_rtt_ms=30), True, "good", "양호"),
            (_device(last_seen_at=_seen_ago(5), last_rtt_ms=250), True, "poor", "불안정"),
            (_device(last_seen_at=_seen_ago(120)), True, "inactive", "연결 없음"),
            (_device(last_seen_at=_seen_ago(5), revoked=True), True, "inactive", "차단됨"),
            (_device(last_seen_at=_seen_ago(5), last_rtt_ms=30), False, "inactive", "측정 중지"),
            (_device(last_seen_at=_seen_ago(5)), True, "loading", "확인 중"),
        ]
        for device, running, key, label in cases:
            with self.subTest(label=label):
                state = build_network_view_state([device], [], [], server_running=running, server_addr="", now=now)
                self.assertEqual(state.device_rows[0].quality_key, key)
                self.assertEqual(state.device_rows[0].quality_label, label)


class QualityLabelCoverageTest(unittest.TestCase):
    """서비스가 내는 모든 상태가 카드 라벨·색상으로 표시되는지 고정한다.

    새 상태가 서비스에 추가됐는데 뷰 라벨이 없으면 '확인 중'으로 빠져
    사용자가 실제 상태를 볼 수 없다 — 매핑 누락을 테스트로 막는다.
    """

    def test_every_service_status_has_label_and_color(self) -> None:
        from services import internet_quality_service as svc
        from views.network_management_view import (
            _QUALITY_STATE_COLORS,
            _QUALITY_STATE_LABELS,
        )

        statuses = {
            value
            for name, value in vars(svc).items()
            if name.startswith("QUALITY_") and isinstance(value, str)
        }
        self.assertGreaterEqual(len(statuses), 4)
        for status in statuses:
            with self.subTest(status=status):
                self.assertIn(status, _QUALITY_STATE_LABELS)
                self.assertIn(status, _QUALITY_STATE_COLORS)


if __name__ == "__main__":
    unittest.main()
