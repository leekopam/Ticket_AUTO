"""Dashboard search auto-refresh contract tests."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class DashboardSearchRefreshContractTest(unittest.TestCase):
    def setUp(self) -> None:
        source = Path("views/dashboard_flet_view.py").read_text(encoding="utf-8-sig")
        self.source = source
        self.tree = ast.parse(source)

    def test_search_field_assigns_on_change_handler(self) -> None:
        found = False
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Attribute):
                continue
            target = node.targets[0]
            if (
                isinstance(target.value, ast.Name)
                and target.value.id == "search_field"
                and target.attr == "on_change"
            ):
                found = True
                break
        self.assertTrue(found)

    def test_runtime_event_uses_order_refresh_policy(self) -> None:
        found = False
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "should_auto_refresh_order_views":
                continue
            found = True
            break
        self.assertTrue(found)

    def test_dashboard_starts_excel_watch_thread(self) -> None:
        found = False
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "Thread":
                continue
            for keyword in node.keywords:
                if keyword.arg != "target":
                    continue
                if isinstance(keyword.value, ast.Name) and keyword.value.id == "watch_excel_changes":
                    found = True
                    break
            if found:
                break
        self.assertTrue(found)

    def test_initial_ticket_tab_is_set_before_runtime_subscription(self) -> None:
        set_tab_index = self.source.find('set_tab("ticket", push_update=True)')
        subscribe_index = self.source.find("self._runtime_manager.subscribe(on_runtime_event)")
        self.assertNotEqual(set_tab_index, -1)
        self.assertNotEqual(subscribe_index, -1)
        self.assertLess(set_tab_index, subscribe_index)


if __name__ == "__main__":
    unittest.main()
