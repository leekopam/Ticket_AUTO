"""처리 현황 조회 탭 뷰 상태 계산 단위 테스트."""

import unittest

from models.order_model import Order
from views.work_log_flet_view import (
    RECONCILE_STATUS,
    WorkLogViewState,
    build_ops_index,
    build_work_log_view_state,
    device_chip_colors,
    format_work_time,
    parse_goods_item,
)


def _order(
    order_number: str,
    *,
    name: str = "홍길동",
    phone: str = "010-1234-5678",
    received_at: str = "",
    processing_time: str = "",
    order_status: str = "",
    goods: list[str] | None = None,
) -> Order:
    return Order(
        order_number=order_number,
        name=name,
        phone=phone,
        seat="A-1",
        goods=goods or [],
        received_at=received_at,
        processing_time=processing_time,
        order_status=order_status,
    )


class FormatWorkTimeTest(unittest.TestCase):
    def test_full_timestamp_trimmed(self) -> None:
        self.assertEqual(format_work_time("2026-04-26 14:32:11"), "04-26 14:32:11")

    def test_short_and_empty_values(self) -> None:
        self.assertEqual(format_work_time(""), "-")
        self.assertEqual(format_work_time("14:32"), "14:32")


class ParseGoodsItemTest(unittest.TestCase):
    def test_qty_suffix_split(self) -> None:
        self.assertEqual(parse_goods_item("응원봉 x2"), ("응원봉", 2))
        self.assertEqual(parse_goods_item("슬로건 ×3"), ("슬로건", 3))

    def test_no_suffix_defaults_one(self) -> None:
        self.assertEqual(parse_goods_item("티셔츠"), ("티셔츠", 1))


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


class BuildWorkLogViewStateTest(unittest.TestCase):
    def test_only_received_orders_listed(self) -> None:
        """목록은 수령 완료 건만 — 확인필요/미처리는 집계로만 간다."""
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00"),
            _order("A2", order_status=RECONCILE_STATUS),
            _order("A3", order_status="결제완료"),
        ]
        state = build_work_log_view_state(orders, {}, [])
        self.assertEqual([r.order_number for r in state.rows], ["A1"])
        self.assertEqual(state.completed_count, 1)
        self.assertEqual(state.pending_count, 2)

    def test_processing_time_column_preferred_over_received_at(self) -> None:
        """재스캔으로 수령확인이 갱신돼도 표시 시각은 실제 처리시간 컬럼 값을 따른다."""
        orders = [
            _order(
                "A1",
                received_at="2026-04-26 15:30:00",
                processing_time="2026-04-26 10:00:00",
            ),
        ]
        state = build_work_log_view_state(orders, {}, [])
        self.assertEqual(state.rows[0].time_text, "04-26 10:00:00")

    def test_time_falls_back_to_received_at_when_processing_time_blank(self) -> None:
        """처리시간 컬럼이 없는 구형 데이터는 수령 시각을 표시한다."""
        orders = [_order("A1", received_at="2026-04-26 10:00:00")]
        state = build_work_log_view_state(orders, {}, [])
        self.assertEqual(state.rows[0].time_text, "04-26 10:00:00")

    def test_newest_first_and_seq_descending(self) -> None:
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00"),
            _order("A2", received_at="2026-04-26 11:00:00"),
            _order("A3", received_at="2026-04-26 12:00:00"),
        ]
        state = build_work_log_view_state(orders, {}, [])
        self.assertEqual([r.order_number for r in state.rows], ["A3", "A2", "A1"])
        self.assertEqual([r.seq for r in state.rows], [3, 2, 1])

    def test_selection_produces_detail(self) -> None:
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00", goods=["입장권 x1", "아메리카노 x2"]),
        ]
        ops_index = {"A1": {"order_id": "A1", "device_id": "phone-7", "updated_at": "2026-04-26 10:00:05"}}
        state = build_work_log_view_state(
            orders, ops_index, {"입장권"}, selected_order_number="A1",
        )
        self.assertIsNotNone(state.detail)
        # 레지스트리에 없는 해시는 단축 표기로 떨어진다
        self.assertEqual(state.detail.device_text, "알 수 없는 기기 (phone-)")
        self.assertEqual(state.detail.ticket_items, ("입장권 x1",))
        self.assertEqual(state.detail.goods_items, ("아메리카노 x2",))
        self.assertTrue(state.rows[0].is_selected)

    def test_row_items_split_ticket_and_goods(self) -> None:
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00", goods=["입장권 x1", "아메리카노 x2"]),
        ]
        state = build_work_log_view_state(orders, {}, {"입장권"})
        self.assertEqual(state.rows[0].ticket_items, ("입장권 x1",))
        self.assertEqual(state.rows[0].goods_items, ("아메리카노 x2",))

    def test_row_shows_operator_device(self) -> None:
        """행에도 처리 단말이 표시된다 — 상세를 열지 않아도 누가 처리했는지 보인다."""
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00"),
            _order("A2", received_at="2026-04-26 11:00:00"),
        ]
        ops_index = {
            "A1": {"order_id": "A1", "device_id": "uid-9", "device_name": "민기의 S24"},
        }
        state = build_work_log_view_state(orders, ops_index, [])
        by_order = {r.order_number: r for r in state.rows}
        self.assertEqual(by_order["A2"].device_text, "PC")
        self.assertEqual(by_order["A1"].device_text, "민기의 S24")

    def test_device_chip_colors_stable(self) -> None:
        """같은 기기명은 항상 같은 색 — 검색 결과가 바뀌어도 유지된다."""
        self.assertEqual(device_chip_colors("폰 1"), device_chip_colors("폰 1"))
        self.assertEqual(device_chip_colors("PC"), ("#EEF1F4", "#4B5A6E"))

    def test_search_filters_rows(self) -> None:
        orders = [
            _order("A1", name="김민수", received_at="2026-04-26 10:00:00", goods=["응원봉 x1"]),
            _order("A2", name="이서연", received_at="2026-04-26 11:00:00", goods=["슬로건 x1"]),
        ]
        state = build_work_log_view_state(orders, {}, [], query="김민수")
        self.assertEqual([r.order_number for r in state.rows], ["A1"])

    def test_search_by_goods_name(self) -> None:
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00", goods=["응원봉 x1"]),
            _order("A2", received_at="2026-04-26 11:00:00", goods=["슬로건 x1"]),
        ]
        state = build_work_log_view_state(orders, {}, [], query="슬로건")
        self.assertEqual([r.order_number for r in state.rows], ["A2"])

    def test_search_by_phone_digits(self) -> None:
        orders = [
            _order("A1", phone="010-1111-2222", received_at="2026-04-26 10:00:00"),
            _order("A2", phone="010-9999-0000", received_at="2026-04-26 11:00:00"),
        ]
        state = build_work_log_view_state(orders, {}, [], query="11112222")
        self.assertEqual([r.order_number for r in state.rows], ["A1"])

    def test_search_empty_result_message(self) -> None:
        orders = [_order("A1", received_at="2026-04-26 10:00:00")]
        state = build_work_log_view_state(orders, {}, [], query="없는이름")
        self.assertEqual(state.rows, ())
        self.assertEqual(state.empty_text, "검색 결과가 없습니다.")

    def test_metrics_ignore_query(self) -> None:
        """검색으로 행이 가려져도 상단 집계는 행사 전체 기준으로 유지된다."""
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00"),
            _order("A2"),
        ]
        state = build_work_log_view_state(orders, {}, [], query="없는이름")
        self.assertEqual(state.completed_count, 1)
        self.assertEqual(state.pending_count, 1)

    def test_goods_remaining_counts_unreceived_qty(self) -> None:
        """상품별 미수령 = 전체 주문 수량 − 수령 완료 수량 (티켓 분류 제외)."""
        orders = [
            _order("A1", received_at="2026-04-26 10:00:00", goods=["응원봉 x2", "입장권 x1"]),
            _order("A2", goods=["응원봉 x3", "슬로건 x1"]),
            _order("A3", goods=["슬로건 x2"]),
        ]
        state = build_work_log_view_state(orders, {}, {"입장권"})
        remaining = {item.name: item.remaining for item in state.goods_remaining}
        # 응원봉: 주문 2+3=5, 수령 2 → 3 / 슬로건: 1+2=3 수령 0 → 3 / 티켓 제외
        self.assertEqual(remaining, {"응원봉": 3, "슬로건": 3})

    def test_goods_remaining_excludes_ticket_products(self) -> None:
        orders = [_order("A1", goods=["입장권 x5"])]
        state = build_work_log_view_state(orders, {}, {"입장권"})
        self.assertEqual(state.goods_remaining, ())

    def test_missing_selection_leaves_detail_hidden(self) -> None:
        orders = [_order("A1", received_at="2026-04-26 10:00:00")]
        state = build_work_log_view_state(orders, {}, [], selected_order_number="ZZ9")
        self.assertIsNone(state.detail)
        self.assertFalse(state.rows[0].is_selected)

    def test_empty_state_message(self) -> None:
        state = build_work_log_view_state([], {}, [])
        self.assertIsInstance(state, WorkLogViewState)
        self.assertEqual(state.empty_text, "아직 처리된 주문이 없습니다.")
        self.assertEqual(state.completed_count, 0)
        self.assertEqual(state.pending_count, 0)


if __name__ == "__main__":
    unittest.main()
