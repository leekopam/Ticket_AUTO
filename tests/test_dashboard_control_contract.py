"""Dashboard control mapping tests."""
from __future__ import annotations

import unittest

from models.order_model import Order


class DashboardControlContractTest(unittest.TestCase):
    def test_button_state_mapping(self) -> None:
        try:
            from views.dashboard_flet_view import compute_button_enabled
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(compute_button_enabled("IDLE"), (True, False, False))
        self.assertEqual(compute_button_enabled("RUNNING"), (False, True, True))
        self.assertEqual(compute_button_enabled("RECOVERING"), (False, True, True))
        self.assertEqual(compute_button_enabled("ERROR"), (True, False, False))

    def test_tab_resolution_switches_content(self) -> None:
        try:
            from views.dashboard_flet_view import resolve_tab_content
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        ticket = object()
        receipt = object()
        self.assertIs(resolve_tab_content("ticket", ticket, receipt), ticket)
        self.assertIs(resolve_tab_content("receipt", ticket, receipt), receipt)

    def test_order_refresh_policy_only_refreshes_ticket_tab_outside_transient_states(self) -> None:
        try:
            from views.dashboard_flet_view import should_auto_refresh_order_views
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertTrue(should_auto_refresh_order_views("ticket", "RUNNING"))
        self.assertTrue(should_auto_refresh_order_views("ticket", "ERROR"))
        self.assertFalse(should_auto_refresh_order_views("ticket", "STARTING"))
        self.assertFalse(should_auto_refresh_order_views("ticket", "STOPPING"))
        self.assertFalse(should_auto_refresh_order_views("receipt", "RUNNING"))

    def test_preserved_dropdown_selection_is_kept_only_when_order_still_exists(self) -> None:
        try:
            from views.dashboard_flet_view import resolve_preserved_order_selection
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        available = {"A-100", "B-200"}
        self.assertEqual(resolve_preserved_order_selection("A-100", available), "A-100")
        self.assertIsNone(resolve_preserved_order_selection("C-300", available))
        self.assertIsNone(resolve_preserved_order_selection(None, available))

    def test_file_timestamp_helper_only_flags_real_changes_after_initial_snapshot(self) -> None:
        try:
            from views.dashboard_flet_view import has_file_timestamp_changed
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertFalse(has_file_timestamp_changed(None, 100))
        self.assertFalse(has_file_timestamp_changed(100, None))
        self.assertFalse(has_file_timestamp_changed(100, 100))
        self.assertTrue(has_file_timestamp_changed(100, 101))

    def test_order_search_signature_changes_when_received_status_changes(self) -> None:
        try:
            from views.dashboard_flet_view import build_order_search_signature
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        before = [
            Order(
                order_number="A-100",
                name="홍길동",
                phone="010-0000-0000",
                seat="A1",
                goods=["테스트 티켓 x1"],
                received_at="",
            )
        ]
        after = [
            Order(
                order_number="A-100",
                name="홍길동",
                phone="010-0000-0000",
                seat="A1",
                goods=["테스트 티켓 x1"],
                received_at="2026-03-09 13:00:00",
            )
        ]

        self.assertNotEqual(
            build_order_search_signature("", "전체", ["테스트 티켓"], before),
            build_order_search_signature("", "전체", ["테스트 티켓"], after),
        )


if __name__ == "__main__":
    unittest.main()
