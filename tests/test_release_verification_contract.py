from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.boogle import release_verify


ROOT = Path(__file__).resolve().parents[1]


class ReleaseVerificationContractTest(unittest.TestCase):
    def test_release_verification_script_reuses_existing_tools(self) -> None:
        script = ROOT / "scripts" / "qa" / "verify_release.ps1"
        self.assertTrue(script.exists(), "scripts/qa/verify_release.ps1 must exist.")

        source = script.read_text(encoding="utf-8-sig")
        required_fragments = [
            "[switch]$Fast",
            "[switch]$E2E",
            "[switch]$Release",
            "[string]$ResultsPath",
            "tests\\e2e",
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

    def test_release_seals_the_requested_run_instead_of_an_older_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            results_root = root / "results"
            stale = results_root / "zz_older_run"
            stale.mkdir(parents=True)
            (stale / "exe-smoke.log").write_text("old run", encoding="utf-8")
            artifacts_dir = root / "sealed"
            metrics_path = root / "metrics.json"
            manifest_path = root / "artifacts.json"

            def run_release(command, *, cwd):
                result_dir = Path(command[command.index("-ResultsPath") + 1])
                self.assertEqual(result_dir.parent, results_root)
                result_dir.mkdir(parents=True)
                for name in ("summary.md", "pytest.xml", "exe-smoke.log"):
                    (result_dir / name).write_text(f"current {name}", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)

            env = {
                "BOOGLE_METRICS_PATH": str(metrics_path),
                "BOOGLE_ARTIFACTS_PATH": str(manifest_path),
                "BOOGLE_ARTIFACTS_DIR": str(artifacts_dir),
            }
            with (
                patch.dict(os.environ, env),
                patch.object(release_verify, "RESULTS_ROOT", results_root),
                patch.object(release_verify, "_find_powershell", return_value="powershell"),
                patch.object(release_verify.subprocess, "run", side_effect=run_release),
            ):
                self.assertEqual(release_verify.main(), 0)

            self.assertEqual(
                (artifacts_dir / "exe-smoke.log").read_text(encoding="utf-8"),
                "current exe-smoke.log",
            )
            metrics = {
                item["name"]: item["value"]
                for item in json.loads(metrics_path.read_text(encoding="utf-8"))
            }
            self.assertEqual(metrics["release/sealed_files"], 3)
            self.assertEqual(metrics["release/exe_smoke_ok"], 1)


if __name__ == "__main__":
    unittest.main()
