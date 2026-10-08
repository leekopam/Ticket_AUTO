"""packaged_e2e.py — 패키징 exe 게이트의 매처·계약 검증."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.qa.packaged_e2e import (
    DEFAULT_EXE,
    classify_selfcheck_report,
    match_log_error,
    match_log_warning,
)

ROOT = Path(__file__).resolve().parents[1]


class LogFatalMatcherTest(unittest.TestCase):
    """치명 계층 — 부트로더/프로세스 사망급 오류만 실패로 잡는다."""

    def test_pyinstaller_boot_failure_matches(self) -> None:
        line = "[12345] Failed to execute script 'main' due to unhandled exception"
        self.assertEqual(match_log_error(line), "pyinstaller_boot")

    def test_fatal_python_error_matches(self) -> None:
        line = "Fatal Python error: PyEval_RestoreThread: the function must be called"
        self.assertEqual(match_log_error(line), "fatal_python")

    def test_critical_matches(self) -> None:
        line = "2026-01-01 CRITICAL services.api: 서버 초기화 실패"
        self.assertEqual(match_log_error(line), "critical")

    def test_handled_traceback_is_not_fatal(self) -> None:
        """핸들된 앱 예외(데이터 부재 검색 실패 등)는 치명으로 오탐하지 않는다."""
        line = "Traceback (most recent call last):"
        self.assertIsNone(match_log_error(line))

    def test_normal_error_level_is_not_fatal(self) -> None:
        line = "2026-01-01 ERROR services.excel_service: 데이터 파일이 없습니다"
        self.assertIsNone(match_log_error(line))


class LogWarnMatcherTest(unittest.TestCase):
    """경고 계층 — 핸들된 예외 흔적을 집계해 회귀 신호로 보고한다."""

    def test_traceback_is_warning(self) -> None:
        self.assertEqual(
            match_log_warning("Traceback (most recent call last):"),
            "traceback",
        )

    def test_import_error_is_warning(self) -> None:
        line = "ModuleNotFoundError: No module named 'flet_desktop'"
        self.assertEqual(match_log_warning(line), "import_error")

    def test_dll_error_is_warning(self) -> None:
        line = "DLL load failed while importing _core"
        self.assertEqual(match_log_warning(line), "dll_error")

    def test_normal_line_does_not_match(self) -> None:
        line = "2026-01-01 INFO main: 대시보드 초기화 완료"
        self.assertIsNone(match_log_warning(line))


class SelfCheckReportTest(unittest.TestCase):
    def test_ok_report_has_no_failures(self) -> None:
        report = {"env": "frozen", "ok": True, "failed": [], "results": []}
        self.assertEqual(classify_selfcheck_report(report), [])

    def test_failed_list_returned(self) -> None:
        report = {"ok": False, "failed": ["imports", "lan_server"], "results": []}
        self.assertEqual(
            classify_selfcheck_report(report), ["imports", "lan_server"]
        )

    def test_fallback_scans_results_when_failed_missing(self) -> None:
        report = {
            "ok": False,
            "results": [
                {"name": "imports", "ok": True},
                {"name": "camera_enum", "ok": False},
            ],
        }
        self.assertEqual(classify_selfcheck_report(report), ["camera_enum"])


class GateWiringTest(unittest.TestCase):
    def test_default_exe_under_dist(self) -> None:
        self.assertEqual(
            DEFAULT_EXE,
            ROOT / "dist" / "Ticket_AUTO_flat" / "Ticket_AUTO_flat.exe",
        )

    def test_harness_exposes_gate_surface(self) -> None:
        """게이트가 호출하는 제어 명령이 하니스에 실제로 구현돼 있다."""
        import e2e_harness

        self.assertTrue(hasattr(e2e_harness, "send_control_command"))
        self.assertTrue(hasattr(e2e_harness, "create_test_workbook"))
        self.assertTrue(hasattr(e2e_harness, "TEST_QR_URL"))
        self.assertTrue(
            hasattr(e2e_harness.FakeDashboardRuntimeApp, "process_phone_qr")
        )


if __name__ == "__main__":
    unittest.main()
