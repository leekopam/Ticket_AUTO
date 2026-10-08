"""패키징 exe(--e2e 하니스) 대상 UIA E2E + 응답없음 감시.

소스 환경의 e2e_native 시나리오를 출고 바이너리에 그대로 적용해,
패키징에서만 죽는 기능 회귀와 '응답 없음'을 잡는다.
watchdog은 fixture가 자동 부착하며, 세션 종료 시 hang 이벤트가 있으면 실패한다.
"""
from __future__ import annotations

import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")

from e2e_harness import (  # noqa: E402
    TEST_ORDER_NUMBER,
    request_hang,
    send_control_command,
)


def _ensure_runtime_idle(driver) -> None:
    """런타임이 켜져 있으면 중지 버튼으로 되돌린다."""
    driver.press_escape()
    driver.wait_for_tree()
    if driver.find("Button", "중지") is not None:
        driver.click("Button", "중지")
        driver.wait_for_element("Button", "티켓 확인 시작", exact=True, timeout=15.0)
        time.sleep(0.5)


def _emit_order(control_url: str) -> None:
    send_control_command(
        control_url,
        {
            "cmd": "emit_order",
            "order": {
                "order_number": TEST_ORDER_NUMBER,
                "name": "테스트 사용자",
                "phone": "010-0000-0000",
                "seat": "A-001",
                "goods": ["테스트 상품"],
                "order_status": "결제완료",
            },
        },
    )


def test_p01_packaged_window_exposes_dashboard_controls(driver):
    """P01: 출고 exe의 네이티브 창에 핵심 컨트롤이 노출된다."""
    assert driver.find("Edit", "검색") is not None, "검색 입력이 UIA에 노출되지 않음"
    assert driver.find("Button", "검색") is not None, "검색 버튼이 UIA에 노출되지 않음"
    assert driver.find("Button", "티켓 확인 시작") is not None or driver.find(
        "Button", "중지"
    ) is not None, "런타임 시작/중지 버튼이 UIA에 노출되지 않음"


def test_p02_packaged_order_flow_updates_buyer_panel(driver, control_url):
    """P02: exe에서 주문 이벤트→구매자 패널까지 완주한다(응답없음 없이)."""
    _ensure_runtime_idle(driver)

    driver.click("Button", "티켓 확인 시작", exact=True)
    driver.wait_for_text("중지", timeout=15.0)
    _emit_order(control_url)
    driver.wait_for_text("구매자 이름", "테스트 사용자", timeout=15.0)
    driver.wait_for_text("구매자 좌석", "A-001", timeout=15.0)

    _ensure_runtime_idle(driver)


def test_p03_packaged_runtime_restart_cycle_stays_responsive(driver, control_url):
    """P03: 런타임 시작/중지 반복에서도 exe가 응답 상태를 유지한다."""
    _ensure_runtime_idle(driver)

    for _ in range(2):
        driver.click("Button", "티켓 확인 시작", exact=True)
        driver.wait_for_text("중지", timeout=15.0)
        _emit_order(control_url)
        driver.wait_for_text("구매자 이름", "테스트 사용자", timeout=15.0)
        driver.click("Button", "중지", exact=True)
        driver.wait_for_element("Button", "티켓 확인 시작", exact=True, timeout=15.0)
        time.sleep(0.5)


def test_p04_watchdog_detects_induced_hang(packaged_app, control_url):
    """P04: 제어 서버가 유도한 실제 '응답 없음'을 watchdog이 잡아낸다."""
    packaged_app["expect_hang"]()
    dog = packaged_app["watchdog"]
    driver = packaged_app["driver"]

    request_hang(control_url, seconds=8.0)

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if any(e.kind == "hung" for e in dog.report.events):
            break
        time.sleep(0.3)
    else:
        pytest.fail("유도한 hang이 watchdog에 기록되지 않았다")

    # 정지 해제 후 창이 다시 응답하는지 확인한다.
    driver.wait_for_element("Button", "티켓 확인 시작", exact=True, timeout=30.0)
