"""apk_e2e.py — release APK 게이트의 매처·계약 검증."""
from __future__ import annotations

import unittest
from pathlib import Path

from scripts.qa.apk_e2e import match_anomaly

ROOT = Path(__file__).resolve().parents[1]
PKG = "com.leekopam.ticket_auto_android"


class ApkGateMatcherTest(unittest.TestCase):
    def test_anr_lines_match(self) -> None:
        line = (
            "10-05 12:00:00.123  1234  5678 E ActivityManager: "
            f"ANR in {PKG} (process {PKG})"
        )
        self.assertEqual(match_anomaly(line, PKG), "anr")

    def test_event_log_am_anr_matches(self) -> None:
        line = f"10-05 12:00:00.123  1234  5678 I am_anr  : [0,1111,{PKG},-1,Input dispatching timed out]"
        self.assertEqual(match_anomaly(line, PKG), "anr")

    def test_fatal_exception_matches(self) -> None:
        line = "10-05 12:00:00.123  9999  9999 E AndroidRuntime: FATAL EXCEPTION: main"
        self.assertEqual(match_anomaly(line, PKG), "fatal")

    def test_native_crash_matches(self) -> None:
        line = "10-05 12:00:00.123  9999  9999 F libc    : Fatal signal 11 (SIGSEGV), code 1"
        self.assertEqual(match_anomaly(line, PKG), "native_crash")

    def test_process_death_matches(self) -> None:
        line = f"10-05 12:00:00.123  1234  5678 I ActivityManager: Process {PKG} (pid 9999) has died"
        self.assertEqual(match_anomaly(line, PKG), "process_death")

    def test_obituary_death_matches(self) -> None:
        """OneUI/AOSP에서 force-stop·프로세스 사망 시 실제로 찍히는 로그."""
        line = f"10-05 12:00:00.123  3069  4915 V ActivityManager: Got obituary of 22057:{PKG}"
        self.assertEqual(match_anomaly(line, PKG), "process_death")

    def test_killing_line_matches(self) -> None:
        line = f"10-05 12:00:00.123  1234  5678 I ActivityManager: Killing 9999:{PKG}/u0a999 (adj 0): crash"
        self.assertEqual(match_anomaly(line, PKG), "process_death")

    def test_adbd_echo_of_anr_text_does_not_match(self) -> None:
        """shell 명령 문자열이 adbd 로그에 남아도 ANR로 오탐하지 않는다."""
        line = (
            "10-05 12:00:00.123  5684  5684 I adbd    : adbd service requested "
            f"'shell,v2,TERM=xterm-256color,raw:log -t ActivityManager \"ANR in {PKG}\"'"
        )
        self.assertIsNone(match_anomaly(line, PKG))

    def test_normal_line_does_not_match(self) -> None:
        line = f"10-05 12:00:00.123  9999  9999 I flutter : [{PKG}] 앱 기동 완료"
        self.assertIsNone(match_anomaly(line, PKG))

    def test_other_package_anr_does_not_match(self) -> None:
        line = "10-05 12:00:00.123  1234  5678 E ActivityManager: ANR in com.other.app"
        self.assertIsNone(match_anomaly(line, PKG))


class ApkGateScriptContractTest(unittest.TestCase):
    def test_apk_gate_script_exists_with_required_parts(self) -> None:
        script = ROOT / "scripts" / "qa" / "apk_e2e.py"
        self.assertTrue(script.exists(), "scripts/qa/apk_e2e.py 가 필요합니다")

        source = script.read_text(encoding="utf-8-sig")
        for fragment in [
            "--apk",
            "--serial",
            "--monitor-seconds",
            "ANR in {pkg}",
            "FATAL EXCEPTION",
            "install",
            "logcat",
        ]:
            self.assertIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
