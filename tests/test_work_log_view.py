"""티켓 업무 탭 뷰 상태 계산 단위 테스트."""

import unittest
from types import SimpleNamespace

from models.order_model import Order
from views.work_log_flet_view import (
    RECONCILE_STATUS,
    WorkLogViewState,
    build_ops_index,
    build_work_log_view_state,
    filter_work_log_orders,
    format_work_time,
)


def _order(
    order_number: str,
    *,
    received_at: str = "",
    order_status: str = "",
    goods: list[str] | None = None,
) -> Order:
    return Order(
        order_number=order_number,
        name="홍길동",
        phone="010-1234-5678",
        seat="A-1",
        goods=goods or [],
        received_at=received_at,
        order_status=order_status,
    )


class FilterWorkLogOrdersTest(unittest.TestCase):
    def test_received_and_reconcile_orders_included(self) -> None:
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00"),
            _order("A2"),
            _order("A3", order_status=RECONCILE_STATUS),
            _order("A4", order_status="결제완료"),
        ]
        result = filter_work_log_orders(orders)
        self.assertEqual([o.order_number for o in result], ["A1", "A3"])

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(filter_work_log_orders([]), [])
        self.assertEqual(filter_work_log_orders(None), [])


class FormatWorkTimeTest(unittest.TestCase):
    def test_full_timestamp_trimmed(self) -> None:
        self.assertEqual(format_work_time("2026-04-26 14:32:11"), "04-26 14:32:11")

    def test_short_and_empty_values(self) -> None:
        self.assertEqual(format_work_time(""), "-")
        self.assertEqual(format_work_time("14:32"), "14:32")


class BuildOpsIndexTest(unittest.TestCase):
    def test_latest_record_wins(self) -> None:
        ops = [
            {"order_id": "A1", "device_id": "phone-1", "updated_at": "10:00", "state": "accepted"},
            {"order_id": "A1", "device_id": "phone-1", "updated_at": "10:05", "state": "succeeded"},
        ]
        index = build_ops_index(ops)
        self.assertEqual(index["A1"]["state"], "succeeded")

    def test_missing_order_id_skipped(self) -> None:
        self.assertEqual(build_ops_index([{"order_id": ""}, {}]), {})

    def test_result_json_order_id_fallback(self) -> None:
        """스캔 작업은 order_id 열이 비어 result_json에만 주문번호가 들어간다."""
        ops = [{"order_id": "", "device_id": "phone-1", "result_json": '{"order_id": "A1"}'}]
        index = build_ops_index(ops)
        self.assertEqual(index["A1"]["device_id"], "phone-1")

    def test_result_json_fallback_ignored_when_order_id_present(self) -> None:
        ops = [{"order_id": "A1", "device_id": "d", "result_json": '{"order_id": "B2"}'}]
        self.assertIn("A1", build_ops_index(ops))


class BuildWorkLogViewStateTest(unittest.TestCase):
    def test_newest_first_and_seq_descending(self) -> None:
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00"),
            _order("A2", received_at="2026-04-26 11:00:00"),
            _order("A3", received_at="2026-04-26 12:00:00"),
        ]
        state = build_work_log_view_state(orders, {}, [])
        self.assertEqual([r.order_number for r in state.rows], ["A3", "A2", "A1"])
        # 처리 순번은 오래된 건이 1
        self.assertEqual([r.seq for r in state.rows], [3, 2, 1])
        self.assertEqual(state.count_text, "처리 3건")

    def test_reconcile_order_uses_op_updated_at(self) -> None:
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00"),
            _order("A9", order_status=RECONCILE_STATUS),
        ]
        ops_index = {"A9": {"order_id": "A9", "updated_at": "2026-04-26 12:00:00", "device_id": "phone-1"}}
        state = build_work_log_view_state(orders, ops_index, [])
        self.assertEqual(state.rows[0].order_number, "A9")
        self.assertEqual(state.rows[0].badge_text, RECONCILE_STATUS)
        self.assertIn("확인필요 1건", state.count_text)

    def test_selection_produces_detail(self) -> None:
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00", goods=["입장권 x1", "아메리카노 x2"]),
        ]
        ops_index = {"A1": {"order_id": "A1", "device_id": "phone-7", "updated_at": "2026-04-26 10:00:05"}}
        info = SimpleNamespace(
            reported_name="staff-phone", custom_name="입구1번", last_seen_at="", revoked=False
        )
        state = build_work_log_view_state(
            orders, ops_index, {"입장권"}, selected_order_number="A1",
            device_lookup=lambda _device_id: info,
        )
        self.assertIsNotNone(state.detail)
        # 레지스트리 별칭이 해시/보고 이름보다 우선한다
        self.assertEqual(state.detail.device_text, "입구1번")
        self.assertEqual(state.detail.ticket_items, ("입장권 x1",))
        self.assertEqual(state.detail.goods_items, ("아메리카노 x2",))
        self.assertTrue(state.rows[0].is_selected)

    def test_missing_selection_leaves_detail_hidden(self) -> None:
        orders = [_order("A1", received_at="2026-04-26 10:00:00")]
        state = build_work_log_view_state(orders, {}, [], selected_order_number="ZZ9")
        self.assertIsNone(state.detail)
        self.assertFalse(state.rows[0].is_selected)

    def test_pc_processed_order_shows_pc_label(self) -> None:
        """_operations에 없는 PC 본체 처리 건은 처리 단말이 'PC'로 표시된다."""
        orders = [_order("A1", received_at="2026-04-26 10:00:00")]
        state = build_work_log_view_state(
            orders, {}, [], selected_order_number="A1", device_lookup=lambda _d: None
        )
        self.assertEqual(state.detail.device_text, "PC")

    def test_unknown_device_shows_short_hash(self) -> None:
        orders = [_order("A1", received_at="2026-04-26 10:00:00")]
        ops_index = {"A1": {"order_id": "A1", "device_id": "abcdef0123", "device_name": ""}}
        state = build_work_log_view_state(
            orders, ops_index, [], selected_order_number="A1", device_lookup=lambda _d: None
        )
        self.assertIn("abcdef", state.detail.device_text)

    def test_empty_state_message(self) -> None:
        state = build_work_log_view_state([], {}, [])
        self.assertIsInstance(state, WorkLogViewState)
        self.assertEqual(state.empty_text, "아직 처리된 주문이 없습니다.")
        self.assertEqual(state.count_text, "처리 0건")


if __name__ == "__main__":
    unittest.main()
