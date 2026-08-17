from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuildScriptContractTest(unittest.TestCase):
    def test_windows_build_script_automates_repeatable_release_build(self) -> None:
        script = ROOT / "scripts" / "build_windows.ps1"
        self.assertTrue(script.exists(), "scripts/build_windows.ps1 must exist.")

        source = script.read_text(encoding="utf-8-sig")
        required_fragments = [
            "$ErrorActionPreference = \"Stop\"",
            "$env:PLAYWRIGHT_BROWSERS_PATH = \"0\"",
            "-m\", \"playwright\", \"install\", \"chromium\"",
            "-m\", \"PyInstaller\", \"--clean\", \"--noconfirm\"",
            "Ticket_AUTO_flat.spec",
            "playwright\\driver\\package\\.local-browsers",
            "Resources\\templates\\receipt_layout.json",
            "Resources\\data\\data.xlsx",
            "receipt_form.json",
        ]
        for fragment in required_fragments:
            self.assertIn(fragment, source)

    def test_root_batch_wrapper_invokes_windows_build_script(self) -> None:
        wrapper = ROOT / "build_windows.bat"
        self.assertTrue(wrapper.exists(), "build_windows.bat must exist for one-command builds.")

        source = wrapper.read_text(encoding="utf-8-sig").lower()
        self.assertIn("powershell", source)
        self.assertIn('set "pythonpath="', source)
        self.assertIn("scripts\\build_windows.ps1", source)


if __name__ == "__main__":
    unittest.main()
