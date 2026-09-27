"""티켓 확인 B 레이아웃의 결과 상태와 검색 행 표시 계약."""

from __future__ import annotations

import unittest

from models.order_model import Order
from views.dashboard_flet_view import (
    ACCENT_PRIMARY_DEEP,
    ACCENT_PRIMARY_SOFT,
    build_search_result_row_state,
    build_search_result_rows,
    resolve_ticket_result_state,
    select_search_result_row,
)


class TicketCheckUiTest(unittest.TestCase):
    def test_result_never_claims_completion_before_receipt_is_saved(self) -> None:
        order = Order(order_number="TEST-1", name="테스트 사용자")
        processing = resolve_ticket_result_state(order, processing=True)
        self.assertEqual(processing.title, "수령 처리 중")

        order.received_at = "2026-09-27 14:32:08"
        self.assertEqual(
            resolve_ticket_result_state(order, processing=True).title,
            "수령 처리 중",
        )
        completed = resolve_ticket_result_state(order)
        self.assertEqual(completed.title, "수령 완료")
        self.assertIn(order.received_at, completed.detail)
        self.assertEqual(completed.border_color, ACCENT_PRIMARY_DEEP)

    def test_empty_and_error_results_have_distinct_guidance(self) -> None:
        idle = resolve_ticket_result_state(None)
        self.assertIn("스캔", idle.detail)
        failed = resolve_ticket_result_state(
            Order(order_number="TEST-1", name="테스트 사용자"),
            error="엑셀 파일을 확인해주세요.",
        )
        self.assertEqual(failed.title, "수령 처리 실패")
        self.assertIn("엑셀 파일", failed.detail)

    def test_selected_order_row_keeps_text_and_has_visible_marker(self) -> None:
        orders = [
            Order(order_number="TEST-1", name="테스트 사용자"),
            Order(order_number="TEST-2", name="테스트 사용자 2"),
        ]
        states = tuple(
            build_search_result_row_state(order, [], index)
            for index, order in enumerate(orders)
        )
        rows = build_search_result_rows(states, selected_order_number="TEST-2")
        self.assertEqual(rows[0].bgcolor, states[0].row_bg)
        self.assertEqual(rows[1].bgcolor, ACCENT_PRIMARY_SOFT)
        self.assertEqual(rows[1].border.left.color, ACCENT_PRIMARY_DEEP)
        self.assertEqual(rows[1].data, "TEST-2")
        self.assertEqual(rows[1].animate.duration, 120)

        select_search_result_row(rows, "TEST-1")
        self.assertEqual(rows[0].bgcolor, ACCENT_PRIMARY_SOFT)
        self.assertEqual(rows[1].bgcolor, states[1].row_bg)
        self.assertEqual(rows[0].border.left.color, ACCENT_PRIMARY_DEEP)


if __name__ == "__main__":
    unittest.main()
