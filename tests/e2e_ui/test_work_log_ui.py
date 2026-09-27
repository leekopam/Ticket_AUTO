"""U05 티켓 업무 탭 UI 시나리오 (Flet web + headless Playwright).

워크북에 수령확인을 직접 기록해 mtime watcher 자동 갱신까지 검증한다:
탭 진입 → 빈 상태 → 수령 기록 → 목록 표시 → 행 선택 → 상세 표시.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from e2e.support import TEST_ORDER_NUMBER
from e2e_ui.support import wait_for_button

_TIMEOUT_MS = 20000


def _mark_order_received(data_path: Path, order_number: str, timestamp: str) -> None:
    """테스트 워크북의 수령확인 셀에 타임스탬프를 기록한다."""
    workbook = load_workbook(data_path)
    try:
        ws = workbook.active
        headers = {str(cell.value or "").strip(): idx for idx, cell in enumerate(ws[1], 1)}
        order_col = headers.get("주문번호")
        received_col = headers.get("수령확인")
        assert order_col and received_col, "테스트 워크북에 필요한 헤더가 없습니다."
        for row in range(2, ws.max_row + 1):
            if str(ws.cell(row=row, column=order_col).value or "").strip() == order_number:
                ws.cell(row=row, column=received_col, value=timestamp)
                workbook.save(data_path)
                return
        raise AssertionError(f"테스트 워크북에 주문 {order_number}가 없습니다.")
    finally:
        workbook.close()


def _open_work_log_tab(page) -> None:
    wait_for_button(page, "티켓 업무", timeout_ms=_TIMEOUT_MS).click()


def test_work_log_tab_empty_then_row_and_detail(page, flet_server):
    _open_work_log_tab(page)
    page.get_by_text("아직 처리된 주문이 없습니다.", exact=True).first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )

    data_path = Path(flet_server["runtime_dir"]) / "Resources" / "data" / "data.xlsx"
    _mark_order_received(data_path, TEST_ORDER_NUMBER, "2026-09-27 12:00:00")

    # mtime watcher(0.5s)가 파일 변경을 감지해 목록을 자동 갱신한다.
    # 행은 클릭 가능한 컨테이너라 텍스트가 부모 semantics 노드로 병합되므로 부분 매칭을 사용한다.
    page.get_by_text("테스트 사용자").first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )
    page.get_by_text("처리 1건").first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )
    page.get_by_text("수령완료").first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )

    # 행 선택 → 우측 상세에 주문 정보와 전달 상품이 표시된다.
    row = page.get_by_text("테스트 사용자").first
    row.click()
    page.get_by_text("처리 상세").first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )
    page.get_by_text("테스트 상품 x1").first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )
    page.get_by_text("010-0000-0000").first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )
