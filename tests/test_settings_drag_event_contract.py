"""Receipt settings drag event compatibility contract tests."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class SettingsDragEventContractTest(unittest.TestCase):
    def setUp(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.tree = ast.parse(source)

    def test_drag_update_handlers_do_not_access_removed_delta_fields(self) -> None:
        forbidden = {"delta_x", "delta_y"}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                self.fail(f"removed drag event field access found: {node.attr}")


if __name__ == "__main__":
    unittest.main()
