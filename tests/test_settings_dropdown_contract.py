"""Receipt settings dropdown compatibility contract tests."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class SettingsDropdownContractTest(unittest.TestCase):
    def setUp(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.tree = ast.parse(source)

    @staticmethod
    def _is_ft_dropdown_call(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ft"
            and node.func.attr == "Dropdown"
        )

    def test_dropdown_constructor_does_not_use_on_select(self) -> None:
        """Flet 0.23.x Dropdown에는 on_select 파라미터가 없으므로 사용하면 안 된다."""
        for node in ast.walk(self.tree):
            if not self._is_ft_dropdown_call(node):
                continue
            keyword_names = {kw.arg for kw in node.keywords if kw.arg}
            self.assertNotIn(
                "on_select",
                keyword_names,
                "ft.Dropdown()에 on_select 대신 on_change를 사용해야 합니다",
            )

    def test_dropdown_instances_use_on_change_assignment(self) -> None:
        """Flet 0.23.x에서는 Dropdown 이벤트 핸들러로 on_change를 사용해야 한다."""
        dropdown_names: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and self._is_ft_dropdown_call(node.value):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        dropdown_names.add(target.id)
            elif isinstance(node, ast.AnnAssign) and self._is_ft_dropdown_call(node.value):
                if isinstance(node.target, ast.Name):
                    dropdown_names.add(node.target.id)

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
