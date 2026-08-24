from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuildScriptContractTest(unittest.TestCase):
    def test_windows_build_script_automates_repeatable_release_build(self) -> None:
        script = ROOT / "scripts" / "build" / "build_windows.ps1"
        self.assertTrue(script.exists(), "scripts/build/build_windows.ps1 must exist.")

        source = script.read_text(encoding="utf-8-sig")
        required_fragments = [
            "$ErrorActionPreference = \"Stop\"",
            "$env:PLAYWRIGHT_BROWSERS_PATH = \"0\"",
            "-m\", \"playwright\", \"install\", \"chromium\"",
            "-m\", \"PyInstaller\", \"--clean\", \"--noconfirm\"",
            "build_support\\specs\\Ticket_AUTO_flat.spec",
            "playwright\\driver\\package\\.local-browsers",
            "Resources\\templates\\receipt_layout.json",
            "Resources\\data\\data.xlsx",
        ]
        for fragment in required_fragments:
            self.assertIn(fragment, source)
        self.assertNotIn("receipt_form.json", source)

    def test_grouped_batch_wrapper_invokes_windows_build_script(self) -> None:
        wrapper = ROOT / "scripts" / "build" / "build_windows.bat"
        self.assertTrue(wrapper.exists(), "scripts/build/build_windows.bat must exist.")

        source = wrapper.read_text(encoding="utf-8-sig").lower()
        self.assertIn("powershell", source)
        self.assertIn('set "pythonpath="', source)
        self.assertIn("%~dp0build_windows.ps1", source)

    def test_setup_script_resolves_repository_root_before_installing(self) -> None:
        script = ROOT / "scripts" / "setup" / "setup_windows.bat"
        self.assertTrue(script.exists(), "scripts/setup/setup_windows.bat must exist.")

        source = script.read_text(encoding="utf-8-sig").lower()
        self.assertIn('cd /d "%~dp0..\\.."', source)
        self.assertIn("python -m venv .venv", source)
        self.assertIn("install -r requirements.txt", source)
        self.assertIn("playwright install chromium", source)


if __name__ == "__main__":
    unittest.main()
