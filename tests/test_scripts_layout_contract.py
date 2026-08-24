from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScriptsLayoutContractTest(unittest.TestCase):
    def test_script_files_are_grouped_by_responsibility(self) -> None:
        expected_paths = [
            ROOT / "scripts" / "build" / "build_windows.bat",
            ROOT / "scripts" / "build" / "build_windows.ps1",
            ROOT / "scripts" / "setup" / "setup_windows.bat",
            ROOT / "scripts" / "qa" / "verify_release.ps1",
            ROOT / "scripts" / "qa" / "smoke_packaged_exe.py",
            ROOT / "build_support" / "specs" / "Ticket_AUTO.spec",
            ROOT / "build_support" / "specs" / "Ticket_AUTO_flat.spec",
        ]
        for path in expected_paths:
            self.assertTrue(path.is_file(), f"정리된 스크립트 경로가 필요합니다: {path}")

        legacy_paths = [
            ROOT / "build_windows.bat",
            ROOT / "setup.bat",
            ROOT / "Ticket_AUTO.spec",
            ROOT / "Ticket_AUTO_flat.spec",
            ROOT / "check.py",
            ROOT / "scripts" / "build_windows.ps1",
            ROOT / "scripts" / "verify_release.ps1",
            ROOT / "scripts" / "smoke_packaged_exe.py",
            ROOT / "scripts" / "diagnostics" / "inspect_excel_rows.py",
        ]
        for path in legacy_paths:
            self.assertFalse(path.exists(), f"이전 스크립트 경로가 남아 있습니다: {path}")

    def test_generated_diagnostic_outputs_are_ignored(self) -> None:
        ignored_lines = (ROOT / ".gitignore").read_text(encoding="utf-8-sig").splitlines()
        self.assertIn("/output.json", ignored_lines)
        self.assertIn("/output_rows.json", ignored_lines)


if __name__ == "__main__":
    unittest.main()
