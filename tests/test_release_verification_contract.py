from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseVerificationContractTest(unittest.TestCase):
    def test_release_verification_script_reuses_existing_tools(self) -> None:
        script = ROOT / "scripts" / "qa" / "verify_release.ps1"
        self.assertTrue(script.exists(), "scripts/qa/verify_release.ps1 must exist.")

        source = script.read_text(encoding="utf-8-sig")
        required_fragments = [
            "[switch]$Fast",
            "[switch]$Release",
            "artifacts\\test-results",
            "--junitxml",
            "TICKET_AUTO_RUN_PLAYWRIGHT_SMOKE",
            "scripts\\build\\build_windows.ps1",
            "-SkipTests",
            "smoke_packaged_exe.py",
            "build_support\\specs\\Ticket_AUTO_flat.spec",
            "summary.md",
        ]
        for fragment in required_fragments:
            self.assertIn(fragment, source)

    def test_generated_test_artifacts_are_ignored(self) -> None:
        source = (ROOT / ".gitignore").read_text(encoding="utf-8-sig")
        self.assertIn("artifacts/", source.splitlines())


if __name__ == "__main__":
    unittest.main()
