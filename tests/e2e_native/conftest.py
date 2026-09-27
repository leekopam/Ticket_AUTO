"""네이티브 Flet 창 + UIA 드라이버를 제공하는 공용 fixture."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from e2e.support import create_test_workbook
from e2e_ui.support import find_free_port, wait_for_port

from e2e_native.uia_driver import NativeWindowDriver

_ENTRY_PATH = Path(__file__).resolve().parents[1] / "e2e_ui" / "web_entry.py"
_WINDOW_TITLE = f"e2e_native_{os.getpid()}"


@pytest.fixture(scope="session")
def native_app(tmp_path_factory):
    """스텁 런타임으로 대시보드를 Windows 네이티브 창으로 기동한다."""
    runtime_dir = tmp_path_factory.mktemp("e2e_native_runtime")
    data_file = runtime_dir / "seed" / "data.xlsx"
    data_file.parent.mkdir(parents=True, exist_ok=True)
    create_test_workbook(data_file)

    control_port = find_free_port()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    log_path = runtime_dir / "native-server.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(_ENTRY_PATH),
            "--native",
            "--title", _WINDOW_TITLE,
            "--control-port", str(control_port),
            "--runtime-dir", str(runtime_dir),
            "--data-file", str(data_file),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )

    try:
        if not wait_for_port(control_port, timeout_sec=15.0):
            raise RuntimeError("제어 서버 기동 실패")
        driver = NativeWindowDriver.wait_for_window(_WINDOW_TITLE, timeout=60.0)
        # 대시보드 시맨틱 트리가 materialize될 때까지 기다린다.
        driver.wait_for_tree(timeout=30.0)
    except Exception:
        proc.kill()
        log_file.close()
        output = ""
        try:
            output = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except Exception:
            pass
        pytest.fail(f"네이티브 대시보드 기동 실패:\n{output}")

    yield {
        "driver": driver,
        "control_url": f"http://127.0.0.1:{control_port}",
    }

    # flet 자식 프로세스가 창을 소유하므로 부모 terminate만으로는 창이 남는다.
    # 창 소유 PID 기준으로 프로세스 트리 전체를 종료한다.
    _kill_window_process_tree(_WINDOW_TITLE)
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log_file.close()


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


@pytest.fixture
def driver(native_app):
    """PostMessage 입력은 창이 뒤에 있어도 동작하므로 전면 활성화하지 않는다."""
    return native_app["driver"]
