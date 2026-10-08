"""release APK 실기기 게이트를 E2E 경로에 포함한다.

`scripts/qa/apk_e2e.py --ui`를 그대로 실행한다 — 설치 → 기동 → 카메라 권한
승인 → 페어링 화면 앵커 → 테마 메뉴 → logcat ANR/FATAL 감시까지 실기기에서
uiautomator2로 검증한다. 게이트가 시작 시 `pm clear`로 미페어링 상태를 만든다.

장비·APK가 없으면 사유와 함께 skip한다 (e2e_device 스위트 규칙).

환경변수로 대상을 바꾼다:
- TICKET_AUTO_APK: 테스트할 APK 경로 (기본: 형제 repo의 app-release.apk)
- TICKET_AUTO_APK_SERIAL: adb 기기 시리얼 (기본: 스크립트 기본값)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE_SCRIPT = _REPO_ROOT / "scripts" / "qa" / "apk_e2e.py"
_DEFAULT_APK = (
    _REPO_ROOT.parent
    / "Ticket_AUTO_Android"
    / "build"
    / "app"
    / "outputs"
    / "flutter-apk"
    / "app-release.apk"
)
# install(-r, 최대 300s) + UI 대기 + monitor_seconds 여유를 포함한 상한
_GATE_TIMEOUT_SEC = 600.0


@pytest.fixture(scope="module")
def apk_gate_target() -> tuple[Path, str]:
    """게이트 대상 APK와 기기 시리얼. 없으면 모듈 전체 skip."""
    from scripts.qa.apk_e2e import DEFAULT_SERIAL, resolve_adb

    apk = Path(os.environ.get("TICKET_AUTO_APK") or _DEFAULT_APK)
    if not apk.is_file():
        pytest.skip(f"APK 없음: {apk} (build_apk.bat으로 먼저 빌드)")

    serial = os.environ.get("TICKET_AUTO_APK_SERIAL") or DEFAULT_SERIAL
    try:
        adb = resolve_adb("")
    except FileNotFoundError as exc:
        pytest.skip(str(exc))
    out = subprocess.run(
        [adb, "devices"], capture_output=True, text=True, timeout=15
    ).stdout
    online = {
        line.split()[0]
        for line in out.splitlines()
        if line.strip().endswith("\tdevice")
    }
    if serial not in online:
        pytest.skip(f"기기 미연결: {serial} (연결된 기기: {sorted(online) or '없음'})")
    return apk, serial


def test_release_apk_ui_gate(apk_gate_target):
    """release APK의 설치·기동·첫 화면 UI·ANR 감시 게이트."""
    apk, serial = apk_gate_target
    screenshot = Path(
        os.environ.get(
            "BOOGLE_ARTIFACTS_DIR",
            _REPO_ROOT / "artifacts" / "test-results",
        )
    ) / "apk-ui-gate.png"
    proc = subprocess.run(
        [
            sys.executable,
            str(_GATE_SCRIPT),
            "--apk",
            str(apk),
            "--ui",
            "--serial",
            serial,
            "--screenshot",
            str(screenshot),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GATE_TIMEOUT_SEC,
    )
    tail = (proc.stdout or "")[-3000:]
    assert proc.returncode == 0, f"APK 게이트 실패 (exit={proc.returncode}):\n{tail}"
