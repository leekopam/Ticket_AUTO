"""Dashboard control mapping tests."""
from __future__ import annotations

import unittest


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


if __name__ == "__main__":
    unittest.main()
