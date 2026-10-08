"""패키징 exe 품질 게이트 — 셀프체크 + 부팅 스모크 + --e2e 기능 경로.

PyInstaller 산출물(`dist/Ticket_AUTO_flat/Ticket_AUTO_flat.exe`)에서만
드러나는 회귀를 잡는다:

- phase 1 (selfcheck): exe --self-check — exe 내부에서 import·DLL·QR 왕복·
  카메라/프린터/오디오 열거·Playwright 실기동·LAN 서버 바인드를 검사한다.
- phase 2 (boot): 정상 기동 → 창 핸들·프로세스 생존 → app.log 에러 스캔 → 종료.
  번들 누락(ImportError·DLL)·초기화 크래시를 잡는다.
- phase 3 (e2e): exe --e2e --native — 제어 서버로 LAN 기동·런타임 시작·
  스텁 폰 페어링·스캔·재스캔까지 실제 HTTPS 왕복을 검증한다.

UIA는 사용하지 않는다 — 창 존재는 Win32 EnumWindows 수준에서만 확인한다.

사용:
    python scripts/qa/packaged_e2e.py [--exe <exe 경로>]
        [--skip-selfcheck] [--skip-boot] [--skip-e2e]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from e2e_harness import (  # noqa: E402
    TEST_ORDER_NUMBER,
    TEST_QR_URL,
    create_test_workbook,
    find_free_port,
    send_control_command,
)

DEFAULT_EXE = _REPO_ROOT / "dist" / "Ticket_AUTO_flat" / "Ticket_AUTO_flat.exe"
GATE_WINDOW_TITLE = "TicketAUTO-PackagedGate"

# app.log 치명 오류 패턴 (kind, 정규식) — 프로세스를 죽이는 부트로더/치명 계열.
# 핸들된 앱 예외(데이터 부재 검색 실패 등)는 Traceback을 남기지만 앱이 계속
# 동작하므로 실패가 아니라 경고로만 집계한다. 진짜 번들 결함(기동 시 ImportError
# 누락·DLL 부재)은 프로세스 조기 종료 또는 아래 치명 표식으로 잡힌다.
_LOG_FATAL_PATTERNS: tuple[tuple[str, str], ...] = (
    ("pyinstaller_boot", r"Failed to execute script"),
    ("fatal_python", r"Fatal Python error"),
    ("critical", r"\bCRITICAL\b"),
    ("system_error", r"\bSystemError\b"),
)

# 실패는 아니지만 회귀 신호로 보고하는 패턴 (핸들된 예외 포함).
_LOG_WARN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("traceback", r"Traceback \(most recent call last\)"),
    ("import_error", r"\b(?:ImportError|ModuleNotFoundError)\b"),
    ("dll_error", r"DLL load failed"),
)

WINDOW_TIMEOUT_SEC = 60.0
BOOT_HOLD_SEC = 4.0
EXIT_TIMEOUT_SEC = 15.0
SELFCHECK_TIMEOUT_SEC = 300.0
CONTROL_TIMEOUT_SEC = 60.0


def match_log_error(line: str) -> str | None:
    """app.log 한 줄이 치명 오류이면 kind를, 아니면 None을 반환한다."""
    for kind, pattern in _LOG_FATAL_PATTERNS:
        if re.search(pattern, line, re.IGNORECASE):
            return kind
    return None


def match_log_warning(line: str) -> str | None:
    """치명은 아니지만 보고 가치가 있는 예외 흔적이면 kind를 반환한다."""
    for kind, pattern in _LOG_WARN_PATTERNS:
        if re.search(pattern, line, re.IGNORECASE):
            return kind
    return None


def classify_selfcheck_report(report: dict) -> list[str]:
    """self-check 리포트에서 실패한 검사 이름 목록을 반환한다."""
    failed = report.get("failed")
    if isinstance(failed, list):
        return [str(name) for name in failed]
    return [
        str(r.get("name", "?"))
        for r in report.get("results", [])
        if isinstance(r, dict) and not r.get("ok", False)
    ]


def _app_log_path(exe_path: Path) -> Path:
    """패키징 앱의 로그 파일 경로 — exe 기준 .runtime/app.log."""
    return exe_path.parent / ".runtime" / "app.log"


def _log_errors_since(log_path: Path, offset: int) -> list[str]:
    """offset 이후 새로 기록된 로그에서 치명 오류 라인을 모은다."""
    if not log_path.is_file():
        return []
    with open(log_path, encoding="utf-8", errors="replace") as fp:
        fp.seek(offset)
        lines = fp.read().splitlines()
    return [line for line in lines if match_log_error(line)]


def _log_warnings_since(log_path: Path, offset: int) -> list[str]:
    """offset 이후 새로 기록된 로그에서 경고성 예외 라인을 모은다."""
    if not log_path.is_file():
        return []
    with open(log_path, encoding="utf-8", errors="replace") as fp:
        fp.seek(offset)
        lines = fp.read().splitlines()
    return [line for line in lines if match_log_warning(line)]


def _descendant_pids(root_pid: int) -> set[int]:
    """Toolhelp32 스냅샷으로 자식 프로세스까지 포함한 pid 집합을 반환한다."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot in (-1, 0xFFFFFFFFFFFFFFFF):
        return set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        parent_of: dict[int, int] = {}
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parent_of[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    result = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parent_of.items():
            if parent in result and pid not in result:
                result.add(pid)
                changed = True
    return result


def _find_windows_for_process(root_pid: int) -> list[tuple[int, str]]:
    """프로세스 트리가 소유한 보이는 최상위 창 (hwnd, 제목) 목록."""
    import win32gui
    import win32process

    pids = _descendant_pids(root_pid)
    found: list[tuple[int, str]] = []

    def _visit(hwnd: int, _extra: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid in pids:
            title = win32gui.GetWindowText(hwnd)
            if title:
                found.append((hwnd, title))

    win32gui.EnumWindows(_visit, None)
    return found


def _find_window_by_title(title: str) -> int | None:
    """정확한 제목의 보이는 최상위 창 핸들을 반환한다."""
    import win32gui

    found: list[int] = []

    def _visit(hwnd: int, _extra: object) -> None:
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
            found.append(hwnd)

    win32gui.EnumWindows(_visit, None)
    return found[0] if found else None


def _close_and_wait(proc: subprocess.Popen, hwnds: list[int]) -> bool:
    """창에 WM_CLOSE를 보내고 종료를 기다린다. 실패 시 프로세스 트리를 강제 종료."""
    import win32con
    import win32gui

    for hwnd in hwnds:
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:
            pass
    try:
        proc.wait(timeout=EXIT_TIMEOUT_SEC)
        return True
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, timeout=15,
        )
        proc.wait(timeout=10)
        return False


def _wait_control_ping(control_url: str, timeout_sec: float = CONTROL_TIMEOUT_SEC) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{control_url}/ping", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def phase_selfcheck(exe: Path, work_dir: Path) -> tuple[bool, str]:
    """exe --self-check를 실행해 리포트 ok 여부를 판정한다."""
    report_path = work_dir / "selfcheck_report.json"
    try:
        proc = subprocess.run(
            [str(exe), "--self-check", "--out", str(report_path)],
            capture_output=True, text=True,
            timeout=SELFCHECK_TIMEOUT_SEC, cwd=str(work_dir),
        )
    except subprocess.TimeoutExpired:
        return False, f"self-check 시간 초과({SELFCHECK_TIMEOUT_SEC}s)"
    if not report_path.is_file():
        tail = (proc.stdout or proc.stderr or "")[-300:]
        return False, f"self-check 리포트 미생성 (exit={proc.returncode}) {tail}"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failed = classify_selfcheck_report(report)
    if failed:
        return False, f"self-check 실패 항목: {', '.join(failed)}"
    count = len(report.get("results", []))
    return True, f"self-check {count}건 통과 (env={report.get('env')})"


def phase_boot(exe: Path) -> tuple[bool, str]:
    """정상 기동 → 창 출현·생존 → 로그 에러 스캔 → 종료."""
    log_path = _app_log_path(exe)
    log_offset = log_path.stat().st_size if log_path.is_file() else 0

    proc = subprocess.Popen([str(exe)])
    try:
        hwnds: list[tuple[int, str]] = []
        deadline = time.monotonic() + WINDOW_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False, f"부팅 중 프로세스 종료 (exit={proc.returncode})"
            hwnds = _find_windows_for_process(proc.pid)
            if hwnds:
                break
            time.sleep(0.5)
        if not hwnds:
            return False, f"창이 {WINDOW_TIMEOUT_SEC}s 내에 나타나지 않음"

        hold_end = time.monotonic() + BOOT_HOLD_SEC
        while time.monotonic() < hold_end:
            if proc.poll() is not None:
                return False, f"창 표시 후 즉시 종료 (exit={proc.returncode})"
            time.sleep(0.25)

        errors = _log_errors_since(log_path, log_offset)
        if errors:
            return False, f"부팅 로그 치명 오류: {errors[0][:200]}"

        clean = _close_and_wait(proc, [h for h, _ in hwnds])
        if not clean:
            return False, "WM_CLOSE 후 프로세스가 종료되지 않음 (강제 종료됨)"
        errors = _log_errors_since(log_path, log_offset)
        if errors:
            return False, f"종료 구간 로그 치명 오류: {errors[0][:200]}"
        warnings = _log_warnings_since(log_path, log_offset)
        warn_note = f" (핸들된 예외 {len(warnings)}건 — 신호로만 기록)" if warnings else ""
        return True, (
            f"창 '{hwnds[0][1]}' 표시·{BOOT_HOLD_SEC}s 생존·"
            f"치명 로그 없음·정상 종료{warn_note}"
        )
    finally:
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=15,
            )


def phase_e2e(exe: Path, work_dir: Path) -> tuple[bool, str]:
    """--e2e 하니스 기동 → 실 TLS 페어링·스캔·재스캔을 검증한다."""
    runtime_dir = work_dir / "e2e_runtime"
    seed = work_dir / "seed" / "data.xlsx"
    seed.parent.mkdir(parents=True, exist_ok=True)
    create_test_workbook(seed)

    control_port = find_free_port()
    control_url = f"http://127.0.0.1:{control_port}"
    title = f"{GATE_WINDOW_TITLE}-{os.getpid()}"

    proc = subprocess.Popen(
        [
            str(exe), "--e2e", "--native",
            "--port", "0",
            "--control-port", str(control_port),
            "--runtime-dir", str(runtime_dir),
            "--data-file", str(seed),
            "--title", title,
        ]
    )
    try:
        if not _wait_control_ping(control_url):
            exit_hint = f" exit={proc.returncode}" if proc.poll() is not None else ""
            return False, f"제어 서버가 {CONTROL_TIMEOUT_SEC}s 내에 응답하지 않음{exit_hint}"

        link = send_control_command(control_url, {"cmd": "phone_link_start"})
        addr = link["result"]["addr"]

        try:
            started = send_control_command(control_url, {"cmd": "runtime_start"})
        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                return False, (
                    "exe 내장 하니스가 runtime_start 미지원(구형) — "
                    "scripts/build/build_windows.ps1로 재빌드 필요"
                )
            raise
        if started["result"]["state"] != "RUNNING":
            return False, f"런타임 시작 실패: {started['result']}"

        pair = send_control_command(
            control_url,
            {"cmd": "phone_pair", "device_name": "게이트폰",
             "device_uid": "packaged-gate-phone", "approve": True},
        )
        token = pair["result"]["token"]
        if not token:
            return False, f"페어링 토큰 미발급: {pair['result']}"

        status = send_control_command(
            control_url, {"cmd": "phone_status", "token": token}
        )
        if status["result"]["http"] != 200:
            return False, f"status 실패: {status['result']}"

        scan = send_control_command(
            control_url,
            {"cmd": "phone_scan", "token": token, "qr_url": TEST_QR_URL,
             "request_id": "pkg-gate-scan-1"},
        )["result"]
        if scan.get("state") != "succeeded":
            return False, f"첫 스캔 결과 이상: {scan}"

        rescan = send_control_command(
            control_url,
            {"cmd": "phone_scan", "token": token, "qr_url": TEST_QR_URL,
             "request_id": "pkg-gate-scan-2"},
        )["result"]
        if rescan.get("state") != "already_processed":
            return False, f"재스캔 결과 이상: {rescan}"

        hwnd = _find_window_by_title(title)
        hwnds = [hwnd] if hwnd else [h for h, _ in _find_windows_for_process(proc.pid)]
        _close_and_wait(proc, hwnds)
        return True, (
            f"LAN 기동({addr})·런타임·페어링·status·스캔(succeeded)·"
            f"재스캔(already_processed) 통과 — order={TEST_ORDER_NUMBER}"
        )
    finally:
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=15,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="패키징 exe 품질 게이트")
    parser.add_argument("--exe", default=str(DEFAULT_EXE), help="테스트 대상 exe 경로")
    parser.add_argument("--skip-selfcheck", action="store_true")
    parser.add_argument("--skip-boot", action="store_true")
    parser.add_argument("--skip-e2e", action="store_true")
    args = parser.parse_args(argv)

    exe = Path(args.exe).resolve()
    if not exe.is_file():
        print(f"[FAIL] exe 없음: {exe}")
        return 2

    phases: list[tuple[str, object]] = []
    if not args.skip_selfcheck:
        phases.append(("selfcheck", phase_selfcheck))
    if not args.skip_boot:
        phases.append(("boot", phase_boot))
    if not args.skip_e2e:
        phases.append(("e2e", phase_e2e))

    all_ok = True
    with tempfile.TemporaryDirectory(prefix="pkg_gate_") as tmp:
        work_dir = Path(tmp)
        for name, phase in phases:
            print(f"[{name}] 실행 중 ...", flush=True)
            try:
                if name == "selfcheck":
                    ok, detail = phase(exe, work_dir)
                elif name == "e2e":
                    ok, detail = phase(exe, work_dir)
                else:
                    ok, detail = phase(exe)
            except Exception as exc:  # 게이트 자체 예외도 실패로 보고
                ok, detail = False, f"게이트 예외: {type(exc).__name__}: {exc}"
            all_ok &= ok
            print(f"[{name}] {'PASS' if ok else 'FAIL'} - {detail}", flush=True)

    print("=" * 60)
    print("PASS - 패키징 exe 게이트 전체 통과" if all_ok else "FAIL - 실패 단계 확인")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
