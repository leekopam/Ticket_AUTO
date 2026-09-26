"""U01~U04 대시보드/설정 UI 시나리오 (Flet web + headless Playwright).

외부 IO는 web_entry에서 스텁으로 대체된다:
- 카메라/브라우저 → FakeDashboardRuntimeApp
- 프린터 → FakePrinterBackend (/printer-jobs로 조회)
"""
from __future__ import annotations

import json
import time

from e2e.support import TEST_ORDER_NUMBER

from e2e_ui.support import (
    fetch_printer_jobs,
    fetch_runtime_calls,
    semantics_leaf,
    send_control_command,
    wait_for_button,
    wait_semantics_text,
)

_TIMEOUT_MS = 20000


def _ensure_runtime_idle(page) -> None:
    """시작 버튼이 보이는 중지 상태로 정규화한다."""
    stop_button = page.get_by_role("button", name="중지", exact=True)
    if stop_button.count() and stop_button.first.is_visible():
        stop_button.first.click()
    wait_for_button(page, "티켓 확인 시작", timeout_ms=_TIMEOUT_MS)


def _start_runtime(page) -> None:
    _ensure_runtime_idle(page)
    page.get_by_role("button", name="티켓 확인 시작", exact=True).click()
    wait_for_button(page, "중지", timeout_ms=_TIMEOUT_MS)


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


def _open_settings_drawer(page, marker: str) -> None:
    """설정 서랍을 열어 marker 텍스트가 보일 때까지 핸들을 토글한다.

    서랍 핸들은 role=text의 semantics 노드(설정 열기/설정 닫기)이며,
    열림 상태는 서버 세션에 유지되므로 초기 상태와 무관하게 수렴시킨다.
    """
    for _ in range(2):
        try:
            page.get_by_text(marker, exact=True).first.wait_for(
                state="visible", timeout=6000
            )
            return
        except Exception:
            # 핸들의 병합 텍스트는 항상 "설 정"(세로 2글자)로 끝난다.
            semantics_leaf(page, "설 정").first.click()
            page.wait_for_timeout(800)
    page.get_by_text(marker, exact=True).first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )


def _click_named_switch(page, label: str) -> None:
    switch = page.get_by_role("switch", name=label)
    if switch.count() == 0:
        switch = page.get_by_text(label, exact=True)
    switch.first.click()


def test_u01_runtime_start_stop_transitions(page, flet_server):
    """U01: 런타임 시작→중지 전이가 버튼 상태와 스텁 호출에 반영된다."""
    _ensure_runtime_idle(page)

    page.get_by_role("button", name="티켓 확인 시작", exact=True).click()
    wait_for_button(page, "중지", timeout_ms=_TIMEOUT_MS)

    page.get_by_role("button", name="중지", exact=True).click()
    wait_for_button(page, "티켓 확인 시작", timeout_ms=_TIMEOUT_MS)

    calls = fetch_runtime_calls(flet_server["control_url"])
    assert ["request_stop", None] in calls


def test_u02_dashboard_search_and_order_flow(page, flet_server):
    """U02: 주문 검색→결과 확인→복사→주문 이벤트로 구매자 정보 표시→영수증 출력."""
    control_url = flet_server["control_url"]
    _ensure_runtime_idle(page)

    # 검색: 테스트 워크북에 1건 존재
    search_box = page.get_by_role("textbox", name="주문번호, 이름, 연락처로 검색")
    search_box.fill("테스트 사용자")
    # 재빌드 타이밍에 클릭이 삼켜질 수 있어 결과가 뜰 때까지 재시도한다.
    for attempt in range(3):
        page.get_by_role("button", name="검색", exact=True).click()
        try:
            wait_semantics_text(
                page, "검색 필터 건수", "1건", timeout_ms=_TIMEOUT_MS // 2
            )
            break
        except AssertionError:
            if attempt == 2:
                raise

    # 결과 행의 복사 버튼 → 실제 클립보드에 해당 주문번호가 들어간다.
    # (행 셀 텍스트는 Flutter web semantics에서 materialize되지 않아
    #  복사 값으로 행 내용을 검증한다.)
    page.get_by_role("button", name="주문번호 복사").first.click()
    page.wait_for_timeout(300)
    clipboard = page.evaluate("() => navigator.clipboard.readText()")
    assert clipboard == TEST_ORDER_NUMBER

    # 런타임 주문 이벤트로 구매자 정보 패널이 채워진다.
    _start_runtime(page)
    _emit_order(control_url)
    wait_semantics_text(page, "구매자 이름", "테스트 사용자", timeout_ms=_TIMEOUT_MS)
    wait_semantics_text(page, "구매자 좌석", "A-001", timeout_ms=_TIMEOUT_MS)

    # 구매자 출력 버튼 → 실제 출력 파이프라인 → 스텁 프린터 큐 기록.
    # (snackbar는 Flutter web semantics에 materialize되지 않아 큐로 검증한다.)
    page.get_by_role("button", name="출력", exact=True).click()
    deadline = time.monotonic() + 15
    jobs = []
    while time.monotonic() < deadline:
        jobs = fetch_printer_jobs(control_url)
        if jobs:
            break
        time.sleep(0.3)
    assert any(TEST_ORDER_NUMBER in job["job_name"] for job in jobs)


def test_u03_ticket_settings_persist(page, flet_server):
    """U03: 티켓 확인 설정 서랍의 오프라인 스캔 스위치가 파일에 저장된다."""
    runtime_dir = flet_server["runtime_dir"]
    debug_path = runtime_dir / ".runtime" / "ticket_debug_settings.json"
    _ensure_runtime_idle(page)

    _open_settings_drawer(page, "티켓 확인 설정")

    before = (
        json.loads(debug_path.read_text(encoding="utf-8")).get("offline_scan_mode")
        if debug_path.exists()
        else None
    )
    _click_named_switch(page, "오프라인 스캔 테스트 모드")
    wait_semantics_text(
        page, "오프라인 스캔", "저장 완료", timeout_ms=_TIMEOUT_MS
    )

    deadline = time.monotonic() + 10
    saved = None
    while time.monotonic() < deadline:
        if debug_path.exists():
            saved = json.loads(debug_path.read_text(encoding="utf-8")).get(
                "offline_scan_mode"
            )
            if saved is not None and saved != before:
                break
        time.sleep(0.3)
    assert debug_path.exists(), "디버그 설정 파일이 생성되지 않았습니다"
    assert saved != before


def test_u04_receipt_settings_persist(page, flet_server):
    """U04: 영수증 양식 탭의 자동 출력 스위치가 파일에 저장된다."""
    runtime_dir = flet_server["runtime_dir"]
    receipt_path = runtime_dir / ".runtime" / "receipt_settings.json"
    _ensure_runtime_idle(page)

    # 영수증 양식 탭으로 전환 후 설정 서랍을 연다.
    page.get_by_role("button", name="영수증 양식", exact=True).first.click()
    _open_settings_drawer(page, "영수증 양식 설정")

    before = (
        json.loads(receipt_path.read_text(encoding="utf-8")).get(
            "qr_scan_auto_print_enabled"
        )
        if receipt_path.exists()
        else None
    )
    _click_named_switch(page, "QR 스캔 시 영수증 자동 출력")
    wait_semantics_text(page, "영수증", "자동 출력 설정 저장 완료", timeout_ms=_TIMEOUT_MS)

    deadline = time.monotonic() + 10
    saved = None
    while time.monotonic() < deadline:
        if receipt_path.exists():
            saved = json.loads(receipt_path.read_text(encoding="utf-8")).get(
                "qr_scan_auto_print_enabled"
            )
            if saved is not None and saved != before:
                break
        time.sleep(0.3)
    assert receipt_path.exists(), "영수증 설정 파일이 생성되지 않았습니다"
    assert saved != before
