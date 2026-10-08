"""패키징 exe를 --e2e 하니스로 기동해 UIA 드라이버+watchdog을 붙이는 fixture.

- exe가 없으면 스위트 전체를 skip한다(소스 환경에서의 우연한 실패 방지).
- 각 세션 창 제목은 유니크하게 정해 동시 실행/잔여 창과 충돌하지 않게 한다.
- watchdog은 창이 나타난 뒤 hwnd를 연결해 기동~시맨틱 트리 구간도 감시한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from e2e_harness import create_test_workbook, find_free_port, wait_for_port

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")

if sys.platform == "win32":
    from e2e_native.uia_driver import NativeWindowDriver
    from e2e_packaged.watchdog import ResponsivenessWatchdog

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_EXE = _REPO_ROOT / "dist" / "Ticket_AUTO_flat" / "Ticket_AUTO_flat.exe"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--packaged-exe",
        action="store",
        default=str(_DEFAULT_EXE),
        help="테스트 대상 패키징 exe 경로",
    )
    parser.addoption(
        "--packaged-src",
        action="store_true",
        default=False,
        help="exe 대신 소스 실행(main.py --e2e)으로 하니스 경로만 검증",
    )


def _kill_window_process_tree(title: str) -> None:
    """창 제목으로 소유 프로세스를 찾아 프로세스 트리 전체를 종료한다."""
    try:
        import win32con
        import win32gui
        import win32process
    except ImportError:
        return
    hwnds: list[int] = []

    def _visit(hwnd: int, _extra: object) -> None:
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title:
            hwnds.append(hwnd)

    win32gui.EnumWindows(_visit, None)
    for hwnd in hwnds:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    time.sleep(1.0)
    for hwnd in hwnds:
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            continue
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )


@pytest.fixture(scope="session")
def packaged_app(request, tmp_path_factory):
    """패키징 exe를 --e2e 하니스 네이티브 창으로 기동한다."""
    if request.config.getoption("--packaged-src"):
        launch_cmd = [
            sys.executable,
            str(_REPO_ROOT / "main.py"),
            "--e2e",
        ]
    else:
        exe_path = Path(request.config.getoption("--packaged-exe"))
        if not exe_path.is_file():
            pytest.skip(f"패키징 exe 없음: {exe_path} (build_pc.bat 후 실행)")
        launch_cmd = [str(exe_path), "--e2e"]

    runtime_dir = tmp_path_factory.mktemp("e2e_packaged_runtime")
    data_file = runtime_dir / "seed" / "data.xlsx"
    data_file.parent.mkdir(parents=True, exist_ok=True)
    create_test_workbook(data_file)

    control_port = find_free_port()
    window_title = f"e2e_packaged_{os.getpid()}"
    log_path = runtime_dir / "packaged-app.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        launch_cmd
        + [
            "--native",
            "--title", window_title,
            "--control-port", str(control_port),
            "--runtime-dir", str(runtime_dir),
            "--data-file", str(data_file),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    dog = ResponsivenessWatchdog(proc.pid)
    dog.start()

    try:
        if not wait_for_port(control_port, timeout_sec=60.0):
            raise RuntimeError("제어 서버 기동 실패")
        # 방금 빌드된 exe는 Defender 콜드스캔으로 첫 창 표시가 3분을 넘길 수 있다
        # (실측: 신규 빌드 첫 실행에서 180s 초과 관측).
        driver = NativeWindowDriver.wait_for_window(window_title, timeout=300.0)
        dog.set_hwnd(driver.hwnd)
        # 대시보드 시맨틱 트리가 materialize될 때까지 기다린다.
        driver.wait_for_tree(timeout=90.0)
    except Exception:
        dog.stop()
        _kill_window_process_tree(window_title)
        proc.kill()
        log_file.close()
        output = ""
        try:
            output = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except Exception:
            pass
        pytest.fail(f"패키징 exe 기동 실패:\n{output}")

    expect_hang = {"value": False}
    yield {
        "driver": driver,
        "control_url": f"http://127.0.0.1:{control_port}",
        "watchdog": dog,
        "expect_hang": lambda: expect_hang.__setitem__("value", True),
    }

    report = dog.stop()
    _kill_window_process_tree(window_title)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log_file.close()
    # 세션 종료 시점에 hang/비정상 이벤트가 있었으면 실패로 표시한다.
    # 단, 테스트가 expect_hang()으로 유도한 hang은 '감지됨'이 정상이다.
    if expect_hang["value"]:
        if not any(e.kind == "hung" for e in report.events):
            pytest.fail("유도한 hang이 watchdog에 감지되지 않았다")
        bad = [e for e in report.events if e.kind != "hung"]
        if bad:
            pytest.fail(
                "hang 외 비정상 이벤트 감지:\n"
                + "\n".join(report.summary_lines())
            )
    elif not report.healthy:
        pytest.fail(
            "패키징 exe 응답 없음 감지:\n" + "\n".join(report.summary_lines())
        )


@pytest.fixture
def driver(packaged_app):
    return packaged_app["driver"]


@pytest.fixture
def control_url(packaged_app):
    return packaged_app["control_url"]
