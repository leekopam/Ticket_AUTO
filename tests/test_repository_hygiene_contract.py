from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryHygieneContractTest(unittest.TestCase):
    def test_obsolete_project_files_are_removed(self) -> None:
        obsolete_paths = [
            ROOT / "FYI" / "test.py",
            ROOT / "receipt_form.json",
            ROOT / "영수증 양식.json",
            ROOT / "views" / "error_view.py",
            ROOT / ".serena" / "memories" / "memory_maintenance.md",
        ]
        for path in obsolete_paths:
            self.assertFalse(path.exists(), f"더 이상 사용하지 않는 파일입니다: {path}")

    def test_vscode_configuration_has_no_user_absolute_path(self) -> None:
        for path in (ROOT / ".vscode" / "launch.json", ROOT / ".vscode" / "settings.json"):
            source = path.read_text(encoding="utf-8-sig")
            self.assertNotIn("C:\\\\Users\\\\", source)
            self.assertNotIn("AppData\\\\Local\\\\Programs\\\\Python", source)

    def test_root_excel_exports_are_ignored(self) -> None:
        ignored_lines = (ROOT / ".gitignore").read_text(encoding="utf-8-sig").splitlines()
        self.assertIn("/*.xlsx", ignored_lines)


if __name__ == "__main__":
    unittest.main()
