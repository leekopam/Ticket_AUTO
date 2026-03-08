"""Receipt settings image fit compatibility contract tests."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class SettingsImageFitContractTest(unittest.TestCase):
    def setUp(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.source = source
        self.tree = ast.parse(source)

    def test_view_does_not_reference_removed_boxfit_api(self) -> None:
        self.assertNotIn("ft.BoxFit", self.source)

    def test_view_declares_image_fit_compatibility_aliases(self) -> None:
        assigned_names: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned_names.add(target.id)

        self.assertIn("_IMAGE_FIT", assigned_names)
        self.assertIn("IMAGE_FIT_CONTAIN", assigned_names)
        self.assertIn("IMAGE_FIT_FILL", assigned_names)


if __name__ == "__main__":
    unittest.main()
