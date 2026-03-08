"""Receipt settings file picker callback contract tests (Flet 0.23.x)."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class SettingsFilePickerContractTest(unittest.TestCase):
    def setUp(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.source = source
        self.tree = ast.parse(source)

    def _get_function(self, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        self.fail(f"{name} function was not found.")

    def test_file_picker_handlers_are_sync(self) -> None:
        """Flet 0.23.x에서 FilePicker는 동기 호출 + on_result 콜백 패턴을 사용한다."""
        for name in ("pick_image_for_selected", "on_add_image", "on_save_as", "on_load", "on_pick_scan_sound"):
            func = self._get_function(name)
            self.assertIsInstance(
                func, ast.FunctionDef,
                f"{name}은 Flet 0.23.x에서 동기(sync) 함수여야 합니다",
            )

    def test_on_result_callbacks_are_registered(self) -> None:
        """FilePicker 결과는 on_result 콜백으로 처리되어야 한다."""
        self.assertIn("on_result", self.source)


if __name__ == "__main__":
    unittest.main()
