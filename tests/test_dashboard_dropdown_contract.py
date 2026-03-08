"""Dashboard dropdown compatibility contract tests."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class DashboardDropdownContractTest(unittest.TestCase):
    def setUp(self) -> None:
        source = Path("views/dashboard_flet_view.py").read_text(encoding="utf-8-sig")
        self.tree = ast.parse(source)

    def test_dropdown_instances_do_not_assign_on_select(self) -> None:
        """Flet 0.23.x에서는 Dropdown에 on_select가 존재하지 않으므로 on_change를 사용해야 한다."""
        dropdown_names: set[str] = set()
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            is_dropdown = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "ft"
                and func.attr == "Dropdown"
            )
            if not is_dropdown:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    dropdown_names.add(target.id)

        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Attribute):
                continue
            target = node.targets[0]
            if not isinstance(target.value, ast.Name):
                continue
            if target.value.id not in dropdown_names:
                continue
            self.assertNotEqual(
                target.attr, "on_select",
                f"Dropdown '{target.value.id}'은 on_select 대신 on_change를 사용해야 합니다",
            )


if __name__ == "__main__":
    unittest.main()
