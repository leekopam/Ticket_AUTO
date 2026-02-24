"""Entrypoint mode tests for main module."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class MainEntrypointModeTest(unittest.TestCase):
    def test_main_entrypoint_calls_dashboard(self) -> None:
        source = Path("main.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)

        found_guard = False
        found_dashboard_call = False
        for node in tree.body:
            if not isinstance(node, ast.If):
                continue
            if not isinstance(node.test, ast.Compare):
                continue
            if not isinstance(node.test.left, ast.Name) or node.test.left.id != "__name__":
                continue
            found_guard = True
            for stmt in node.body:
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                    func = stmt.value.func
                    if isinstance(func, ast.Name) and func.id == "run_dashboard_app":
                        found_dashboard_call = True
                        break

        self.assertTrue(found_guard, "__main__ 가드가 필요합니다.")
        self.assertTrue(found_dashboard_call, "main.py는 run_dashboard_app()을 호출해야 합니다.")

    def test_application_class_still_exists(self) -> None:
        source = Path("main.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        class_names = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertIn("Application", class_names)


if __name__ == "__main__":
    unittest.main()
