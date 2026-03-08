"""Receipt settings ExpansionTile compatibility contract tests."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class SettingsExpansionTileContractTest(unittest.TestCase):
    def setUp(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.tree = ast.parse(source)

    @staticmethod
    def _is_ft_expansion_tile_call(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ft"
            and node.func.attr == "ExpansionTile"
        )

    def test_expansion_tile_constructor_does_not_use_removed_expanded_keyword(self) -> None:
        for node in ast.walk(self.tree):
            if not self._is_ft_expansion_tile_call(node):
                continue
            keyword_names = {kw.arg for kw in node.keywords if kw.arg}
            self.assertNotIn("expanded", keyword_names)

    def test_expansion_tile_constructor_uses_initially_expanded_keyword(self) -> None:
        calls = [node for node in ast.walk(self.tree) if self._is_ft_expansion_tile_call(node)]
        self.assertGreaterEqual(len(calls), 1)
        for node in calls:
            keyword_names = {kw.arg for kw in node.keywords if kw.arg}
            self.assertIn("initially_expanded", keyword_names)


if __name__ == "__main__":
    unittest.main()
