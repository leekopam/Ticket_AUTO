"""Flet web 대시보드 서버와 Playwright 페이지를 제공하는 공용 fixture."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from e2e.support import create_test_workbook
from e2e_ui.support import find_free_port, wait_for_port

_ENTRY_PATH = Path(__file__).resolve().parent / "web_entry.py"
_ARTIFACTS_DIR = os.environ.get("BOOGLE_ARTIFACTS_DIR")


# 제어 서버/semantics 헬퍼는 e2e_ui.support로 이동했다.
# (테스트 모듈에서 e2e_ui.support를 직접 import해 사용한다.)


@pytest.fixture(scope="session")
def flet_server(tmp_path_factory):
    """격리된 런타임 폴더 + 스텁 런타임으로 대시보드를 web 모드로 기동한다."""
    runtime_dir = tmp_path_factory.mktemp("e2e_ui_runtime")
    data_file = runtime_dir / "seed" / "data.xlsx"
    data_file.parent.mkdir(parents=True, exist_ok=True)
    create_test_workbook(data_file)

    port = find_free_port()
    control_port = find_free_port()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    # 서버 출력은 파일로 리다이렉트한다. 파이프를 비우지 않고 두면
    # 로그가 버퍼를 채웠을 때 서버가 블록될 수 있다.
    log_path = runtime_dir / "flet-server.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(_ENTRY_PATH),
            "--port", str(port),
            "--control-port", str(control_port),
            "--runtime-dir", str(runtime_dir),
            "--data-file", str(data_file),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    ready = wait_for_port(control_port, timeout_sec=15.0) and wait_for_port(
        port, timeout_sec=60.0
    )
    if not ready:
        proc.kill()
        log_file.close()
        output = ""
        try:
            output = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except Exception:
            pass
        pytest.fail(f"대시보드 web 서버 기동 실패:\n{output}")

    yield {
        "base_url": f"http://127.0.0.1:{port}",
        "control_url": f"http://127.0.0.1:{control_port}",
        "runtime_dir": runtime_dir,
    }

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log_file.close()


@pytest.fixture
def page(flet_server, request):
    """semantics 접근성이 활성화된 Playwright 페이지를 제공한다."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # E2E_HEADED=1이면 브라우저를 화면에 띄우고 slow_mo로 동작을 보기 좋게 늦춘다.
        headed = os.environ.get("E2E_HEADED") == "1"
        browser = p.chromium.launch(
            headless=not headed,
            slow_mo=400 if headed else 0,
        )
        context = browser.new_context(
            viewport={"width": 1800, "height": 920},
            permissions=["clipboard-read", "clipboard-write"],
        )
        pw_page = context.new_page()
        # 실패 스크린샷 훅이 참조할 수 있도록 노드에 보관한다.
        request.node._e2e_page = pw_page
        pw_page.goto(flet_server["base_url"], wait_until="domcontentloaded")
        # Flutter 접근성 트리를 활성화해 role/aria-label 탐색을 가능하게 한다.
        pw_page.wait_for_selector("flt-semantics-placeholder", timeout=60000)
        pw_page.locator("flt-semantics-placeholder").evaluate("e => e.click()")
        # 버튼이 여러 개 렌더될 때까지 semantics 트리 완성을 기다린다.
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if pw_page.locator('flt-semantics[role="button"]').count() > 5:
                break
            time.sleep(0.4)
        else:
            pytest.fail("대시보드 semantics 트리가 초기화되지 않았습니다.")
        yield pw_page
        # 런타임이 켜진 채 끝나면 다음 테스트가 오염되므로 중지 상태로 돌려놓는다.
        try:
            stop_button = pw_page.get_by_role("button", name="중지", exact=True)
            if stop_button.count() and stop_button.first.is_visible():
                stop_button.first.click()
                pw_page.get_by_role(
                    "button", name="티켓 확인 시작", exact=True
                ).wait_for(state="visible", timeout=10000)
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """UI 테스트 실패 시 스크린샷을 artifact로 제출한다."""
    report = yield
    if report.when != "call" or not report.failed:
        return report
    pw_page = getattr(item, "_e2e_page", None)
    if pw_page is None or _ARTIFACTS_DIR is None:
        return report
    try:
        target_dir = Path(_ARTIFACTS_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(
            ch if ch.isalnum() or ch in "._-" else "_" for ch in item.nodeid
        )
        pw_page.screenshot(path=str(target_dir / f"failure-{safe_name}.png"))
    except Exception:
        pass
    return report
