from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.qa.smoke_packaged_exe import run_executable_smoke


class PackagedExecutableSmokeTest(unittest.TestCase):
    def test_reports_early_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            helper = root / "exit_immediately.py"
            helper.write_text("raise SystemExit(7)\n", encoding="utf-8")
            log_path = root / "smoke.log"

            success, message = run_executable_smoke(
                [sys.executable, str(helper)],
                startup_seconds=0.5,
                log_path=log_path,
            )

            self.assertFalse(success)
            self.assertIn("Exit code: 7", message)
            self.assertTrue(log_path.exists())

    def test_accepts_process_that_stays_alive_and_cleans_it_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            helper = root / "stay_alive.py"
            helper.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            log_path = root / "smoke.log"

            success, message = run_executable_smoke(
                [sys.executable, str(helper)],
                startup_seconds=0.2,
                log_path=log_path,
            )

            self.assertTrue(success)
            self.assertIn("remained alive", message)
            self.assertTrue(log_path.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows 패키징 프로세스 정리 검사")
    def test_cleans_detached_processes_from_packaged_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            helper = root / "stay_alive.py"
            helper.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

            with patch(
                "scripts.qa.smoke_packaged_exe._terminate_windows_processes_below"
            ) as cleanup:
                success, _ = run_executable_smoke(
                    [sys.executable, str(helper)],
                    startup_seconds=0.2,
                    log_path=root / "smoke.log",
                    cleanup_root=root,
                )

            self.assertTrue(success)
            cleanup.assert_called_once_with(root)

    def test_rejects_missing_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing = root / "missing.exe"

            success, message = run_executable_smoke(
                [str(missing)],
                startup_seconds=0.1,
                log_path=root / "smoke.log",
            )

            self.assertFalse(success)
            self.assertIn("Executable not found", message)


if __name__ == "__main__":
    unittest.main()
