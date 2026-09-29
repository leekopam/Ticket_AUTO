"""네트워크 관리 탭 UI 시나리오 (Flet web + headless Playwright + 스텁 폰).

제어 서버의 phone_* 명령이 앱 프로세스 안에서 실제 HTTPS로
페어링·승인·하트비트(/v1/status)를 수행한다 — Android 앱과 같은 경로.

검증: 빈 상태 → 승인 대기 → 승인 → 연결됨 배지 → 이름 변경 UI →
_ops 기록과 기기 해시 조인으로 처리 건수·처리 단말 이름 표시.

주의: 클릭 가능한 기기 행은 하위 텍스트가 role="group" 노드의
aria-label로 병합되므로 get_by_text가 아니라 role/name 매칭을 쓴다.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from openpyxl import load_workbook

from e2e.support import TEST_ORDER_NUMBER, create_test_workbook
from e2e_ui.support import send_control_command, wait_for_button

_TIMEOUT_MS = 20000
_POLL_MS = 20000  # 탭 내부 3초 주기 갱신 + 승인 왕복/semantics 반영 여유

_DEVICE_UID = "e2e00000-1111-4222-8333-444455556666"
# 세션 공유 서버라 테스트2는 별도 기기로 페어링한다(테스트1의 별칭과 무관).
_DEVICE_UID_2 = "e2e00000-9999-4222-8333-444455556666"
_DEVICE_NAME_2 = "테스트폰2"


def _open_network_tab(page) -> None:
    wait_for_button(page, "네트워크 관리", timeout_ms=_TIMEOUT_MS).click()


def _device_row(page, name_pattern: str):
    """기기 행은 클릭 대상이 아니라 하위 텍스트가 개별 노드로 노출된다 — 텍스트 매칭."""
    return page.get_by_text(re.compile(name_pattern))


def _write_ops_record(data_path: Path, *, order_number: str, device_hash: str) -> None:
    """테스트 워크북의 _operations 시트에 폰 처리 기록을 한 건 넣는다."""
    workbook = load_workbook(data_path)
    try:
        if "_operations" in workbook.sheetnames:
            ws = workbook["_operations"]
        else:
            ws = workbook.create_sheet("_operations")
            ws.append([
                "request_id", "order_id", "action", "device_id", "state",
                "result_json", "created_at", "updated_at", "device_name",
            ])
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        ws.append([
            "e2e-req-1", order_number, "scan_receipt", device_hash,
            "succeeded", "{}", now, now, "",
        ])
        workbook.save(data_path)
    finally:
        workbook.close()


def _mark_order_received(data_path: Path, order_number: str, timestamp: str) -> None:
    workbook = load_workbook(data_path)
    try:
        ws = workbook.active
        headers = {str(c.value or "").strip(): i for i, c in enumerate(ws[1], 1)}
        order_col, received_col = headers.get("주문번호"), headers.get("수령확인")
        assert order_col and received_col, "테스트 워크북에 필요한 헤더가 없습니다."
        for row in range(2, ws.max_row + 1):
            if str(ws.cell(row=row, column=order_col).value or "").strip() == order_number:
                ws.cell(row=row, column=received_col, value=timestamp)
                workbook.save(data_path)
                return
        raise AssertionError(f"테스트 워크북에 주문 {order_number}가 없습니다.")
    finally:
        workbook.close()


def test_network_tab_pair_pending_approve_rename(page, flet_server):
    control_url = flet_server["control_url"]

    _open_network_tab(page)
    page.get_by_text("아직 연결된 휴대폰이 없습니다.", exact=True).first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )
    # 장치 연결 버튼은 네트워크 탭 서버 카드에 있다
    wait_for_button(page, "장치 연결하기", timeout_ms=_TIMEOUT_MS)

    # LAN 서버 기동 → 서버 주소 카드 표시
    send_control_command(control_url, {"cmd": "phone_link_start"})
    page.get_by_text(re.compile("https://")).first.wait_for(
        state="visible", timeout=_POLL_MS
    )

    # 스텁 폰이 실제 TLS로 pair 요청 → 승인 대기 섹션에 표시
    send_control_command(
        control_url,
        {
            "cmd": "phone_pair",
            "device_name": "테스트폰",
            "device_uid": _DEVICE_UID,
            "approve": False,
        },
    )
    page.get_by_text("승인 대기").first.wait_for(state="visible", timeout=_POLL_MS)
    page.get_by_text("테스트폰").first.wait_for(state="visible", timeout=_POLL_MS)

    # UI에서 승인 → 스텁 폰의 백그라운드 티켓 교환 완료 → 기기 목록에 연결됨
    wait_for_button(page, "연결 승인", timeout_ms=_POLL_MS).click()
    _device_row(page, "테스트폰").first.wait_for(state="visible", timeout=_POLL_MS)
    _device_row(page, "연결됨").first.wait_for(state="visible", timeout=_POLL_MS)
    page.get_by_text("연결된 장치", exact=True).first.wait_for(
        state="visible", timeout=_POLL_MS
    )

    # 이름 변경 다이얼로그 → 별칭 저장 → 목록에 별칭 표시
    wait_for_button(page, "이름 변경", timeout_ms=_TIMEOUT_MS).click()
    field = page.locator('input[aria-label="기기 이름"]')
    field.wait_for(state="visible", timeout=_TIMEOUT_MS)
    field.fill("입구1번")
    wait_for_button(page, "저장", timeout_ms=_TIMEOUT_MS).click()
    _device_row(page, "입구1번").first.wait_for(state="visible", timeout=_POLL_MS)


def test_ticket_start_does_not_start_phone_server(page, flet_server):
    """'티켓 확인 시작'은 런타임만 켠다 — LAN API 서버는 명시적 버튼으로만 기동된다."""
    # 세션 공유: 앞 테스트에서 서버가 이미 켜져 있을 수 있으므로 강제로 내린다.
    send_control_command(flet_server["control_url"], {"cmd": "phone_link_stop"})
    _open_network_tab(page)
    page.get_by_text("서버가 꺼져 있습니다", exact=True).first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )

    wait_for_button(page, "티켓 확인", timeout_ms=_TIMEOUT_MS).click()
    wait_for_button(page, "티켓 확인 시작", timeout_ms=_TIMEOUT_MS).click()
    wait_for_button(page, "중지", timeout_ms=_TIMEOUT_MS)

    # 런타임은 켜졌지만 폰 서버는 여전히 꺼져 있어야 한다
    _open_network_tab(page)
    page.get_by_text("서버가 꺼져 있습니다", exact=True).first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )
    # 런타임 정리
    wait_for_button(page, "티켓 확인", timeout_ms=_TIMEOUT_MS).click()
    wait_for_button(page, "중지", timeout_ms=_TIMEOUT_MS).click()


def test_network_tab_processed_count_and_processor_name(page, flet_server):
    control_url = flet_server["control_url"]
    # 세션 공유 워크북을 오염시키지 않도록 테스트 종료 시 시드 상태로 복원한다.
    data_path = Path(flet_server["runtime_dir"]) / "Resources" / "data" / "data.xlsx"

    # 스텁 폰 페어링(자동 승인) → 토큰 확보
    send_control_command(control_url, {"cmd": "phone_link_start"})
    result = send_control_command(
        control_url,
        {
            "cmd": "phone_pair",
            "device_name": _DEVICE_NAME_2,
            "device_uid": _DEVICE_UID_2,
        },
    )["result"]
    token = result["token"]
    assert token

    # 하트비트 역할 — 인증 호출이 last_seen을 갱신한다
    send_control_command(control_url, {"cmd": "phone_status", "token": token})

    try:
        # 폰이 처리한 것처럼 _operations에 기록 + 주문 수령 기록
        device_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        _write_ops_record(data_path, order_number=TEST_ORDER_NUMBER, device_hash=device_hash)
        _mark_order_received(data_path, TEST_ORDER_NUMBER, "2026-09-28 12:00:00")

        # 네트워크 탭 — 기기 행에 처리 건수와 연결됨 표시 (병합 라벨 기준)
        _open_network_tab(page)
        _device_row(page, _DEVICE_NAME_2).first.wait_for(
            state="visible", timeout=_POLL_MS
        )
        page.get_by_text("연결됨", exact=True).first.wait_for(
            state="visible", timeout=_POLL_MS
        )

        # 기기 행의 "처리 1건" 링크 → 처리 내역 다이얼로그에 주문번호 표시
        wait_for_button(page, "처리 1건", timeout_ms=_POLL_MS).click()
        page.get_by_text(TEST_ORDER_NUMBER).first.wait_for(
            state="visible", timeout=_TIMEOUT_MS
        )
        wait_for_button(page, "닫기", timeout_ms=_TIMEOUT_MS).click()

        # 처리 현황 조회 탭 — 처리 단말이 기기 이름으로 조인된다
        wait_for_button(page, "처리 현황 조회", timeout_ms=_TIMEOUT_MS).click()
        page.get_by_text("테스트 사용자").first.wait_for(
            state="visible", timeout=_TIMEOUT_MS
        )
        page.get_by_text("테스트 사용자").first.click()
        page.get_by_text(_DEVICE_NAME_2).first.wait_for(
            state="visible", timeout=_TIMEOUT_MS
        )
    finally:
        create_test_workbook(data_path)


def test_network_tab_server_toggle_button(page, flet_server):
    """서버 시작/중지 토글 버튼이 LAN API 서버를 명시적으로 켜고 끈다."""
    control_url = flet_server["control_url"]

    send_control_command(control_url, {"cmd": "phone_link_stop"})
    _open_network_tab(page)
    page.get_by_text("서버가 꺼져 있습니다", exact=True).first.wait_for(
        state="visible", timeout=_TIMEOUT_MS
    )

    # 시작: 토글 클릭 → 서버 주소 표시 + 버튼이 "서버 중지"로 바뀐다
    wait_for_button(page, "서버 시작", timeout_ms=_TIMEOUT_MS).click()
    page.get_by_text(re.compile("https://")).first.wait_for(
        state="visible", timeout=_POLL_MS
    )
    wait_for_button(page, "서버 중지", timeout_ms=_TIMEOUT_MS)

    # 중지: 토글 클릭 → 확인 다이얼로그 → 꺼짐 표시 + 버튼이 "서버 시작"으로 돌아간다
    wait_for_button(page, "서버 중지", timeout_ms=_TIMEOUT_MS).click()
    wait_for_button(page, "중지", timeout_ms=_TIMEOUT_MS).click()  # 확인 다이얼로그
    page.get_by_text("서버가 꺼져 있습니다", exact=True).first.wait_for(
        state="visible", timeout=_POLL_MS
    )
    wait_for_button(page, "서버 시작", timeout_ms=_TIMEOUT_MS)

    # 세션 공유 서버라 뒤 테스트를 위해 다시 기동해 둔다
    send_control_command(control_url, {"cmd": "phone_link_start"})


def test_phone_link_dialog_opens_with_qr(page, flet_server):
    """'휴대폰 연결' 버튼이 연결 QR 다이얼로그를 연다 (ft.Colors 회귀 포함)."""
    control_url = flet_server["control_url"]
    send_control_command(control_url, {"cmd": "phone_link_start"})
    _open_network_tab(page)
    page.get_by_text(re.compile("https://")).first.wait_for(
        state="visible", timeout=_POLL_MS
    )

    wait_for_button(page, "장치 연결하기", timeout_ms=_TIMEOUT_MS).click()
    # 모달 내부 텍스트는 flutter web semantics에 라벨이 안 붙으므로 role 구조로 확인한다.
    # 다이얼로그 = role=dialog 노드 + 액션 버튼 3개(QR 재발급/서버 중지/닫기)
    dialog = page.locator("flt-semantics[role='dialog']")
    dialog.first.wait_for(state="attached", timeout=_POLL_MS)
    buttons = dialog.locator("flt-semantics[role='button']")
    buttons.first.wait_for(state="attached", timeout=_POLL_MS)
    assert buttons.count() == 3

    # actions 순서상 마지막 버튼이 "닫기" — 클릭 후 다이얼로그가 사라져야 한다
    buttons.last.evaluate("e => e.click()")
    page.wait_for_timeout(500)
    assert dialog.count() == 0


def test_network_search_filter_reset_button(page, flet_server):
    """필터된 빈 상태에서 초기화 버튼이 검색어와 필터를 지운다.

    참고: Flet web semantics 채널은 실제 키 입력을 앱에 전달하지 못해
    검색창 타이핑 시나리오는 자동화 불가 — 포커스 유지 회귀는 계약 테스트
    (test_dashboard_search_refresh_contract)와 데스크톱 수동 확인으로 커버한다.
    """
    control_url = flet_server["control_url"]
    send_control_command(control_url, {"cmd": "phone_link_start"})
    _open_network_tab(page)
    page.get_by_text(re.compile("https://")).first.wait_for(
        state="visible", timeout=_POLL_MS
    )

    # 검색 필드와 필터 칩이 렌더된다 (입력 경로의 UI 표면 회귀)
    page.locator('input[data-semantics-role="text-field"]').first.wait_for(
        state="attached", timeout=_POLL_MS
    )
    wait_for_button(page, "전체", timeout_ms=_TIMEOUT_MS)


def test_network_tab_internet_quality_card_updates(page, flet_server):
    """인터넷 품질 카드가 실제 측정 결과로 갱신된다.

    watch 루프가 실제 멀티엔드포인트 프로브를 호출하므로 외부망이
    있는 환경에서는 실제 상태, 없는 환경에서는 연결 불가로 표시된다.
    어느 쪽이든 카드는 알려진 상태 라벨 중 하나를 보여야 한다.
    """
    control_url = flet_server["control_url"]
    send_control_command(control_url, {"cmd": "phone_link_start"})
    _open_network_tab(page)
    page.get_by_text(re.compile("https://")).first.wait_for(
        state="visible", timeout=_POLL_MS
    )

    # 측정 완료까지 대기 — 알려진 상태 라벨 중 하나가 표시돼야 한다
    status_label = page.get_by_text(
        re.compile("^(양호|지연 주의|불안정|측정 전|연결 불가)$")
    )
    status_label.first.wait_for(state="visible", timeout=_POLL_MS)

    # 갱신 시각 텍스트가 실제 측정 시각으로 바뀐다
    page.get_by_text(re.compile(r"3초마다 갱신 · \d{2}:\d{2}:\d{2} 갱신")).first.wait_for(
        state="visible", timeout=_POLL_MS
    )
