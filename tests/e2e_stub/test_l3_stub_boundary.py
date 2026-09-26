"""L3 외부 IO 경계 스텁 테스트.

실제 Witchform/프린터/카메라 대신 fixture·스텁으로 경계를 검증한다:
- 수령완료 클릭 흐름: 실제 BrowserService + 로컬 HTML fixture를 _current_page에 주입
- 프린터: FakePrinterQueue의 적재/성공/실패 상태 전이
- 카메라: StillCameraStub의 스틸 프레임을 실제 OpenCV 디코더로 판독
"""
from __future__ import annotations

import os

import pytest

from e2e.support import (
    TEST_QR_URL,
    FakePrinterBackend,
    FakePrinterQueue,
    StillCameraStub,
)
from services.browser_service import BrowserService

# 수령완료 버튼 → 확인 팝업 → 수령완료 표시 + 거래종료 버튼 흐름의 로컬 fixture.
WITCHFORM_FIXTURE_HTML = """<!doctype html>
<html><body>
<div id="order-title">주문 상세</div>
<button id="receiveBtn" onclick="onReceive()">수령 완료 처리</button>
<script>
function onReceive() {
  document.getElementById('receiveBtn').remove();
  const modal = document.createElement('div');
  modal.innerHTML = '<button id="confirmBtn" onclick="onConfirm()">확인</button>';
  document.body.appendChild(modal);
}
function onConfirm() {
  document.getElementById('confirmBtn').remove();
  const status = document.createElement('div');
  status.textContent = '수령완료 되었습니다';
  document.body.appendChild(status);
  const deal = document.createElement('button');
  deal.id = 'closeDeal';
  deal.textContent = '거래종료';
  deal.onclick = function () { deal.remove(); };
  document.body.appendChild(deal);
}
</script>
</body></html>
"""

# 이미 수령완료된 주문 페이지 fixture(수령 완료 처리 버튼 없음).
WITCHFORM_ALREADY_RECEIVED_HTML = """<!doctype html>
<html><body>
<div id="order-title">주문 상세</div>
<div id="status">수령완료 되었습니다</div>
</body></html>
"""

# 수령 버튼도 완료 표시도 없는 비정상 페이지 fixture.
WITCHFORM_BROKEN_HTML = """<!doctype html>
<html><body><div>알 수 없는 페이지</div></body></html>
"""


@pytest.fixture
def pw_page():
    """headless Chromium 페이지를 제공한다. E2E_HEADED=1이면 화면에 표시."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=os.environ.get("E2E_HEADED") != "1"
        )
        page = browser.new_page()
        yield page
        try:
            browser.close()
        except Exception:
            pass


def _service_with_page(pw_page, html: str) -> tuple[BrowserService, list[str]]:
    """BrowserService에 fixture 페이지를 주입하고 완료 콜백을 계측한다."""
    pw_page.set_content(html)
    service = BrowserService()
    service._current_page = pw_page
    events: list[str] = []
    service.set_on_receipt_complete(lambda: events.append("receipt_complete"))
    return service, events


def test_l3_receipt_click_success_path(pw_page):
    """수령완료 버튼→확인 팝업→거래종료까지 실제 클릭 흐름이 성공한다."""
    service, events = _service_with_page(pw_page, WITCHFORM_FIXTURE_HTML)
    result = service._handle_click_receipt()

    assert result.success, result.error_message
    assert events == ["receipt_complete"]
    assert service._current_page is None  # 완료 후 페이지가 닫힌다


def test_l3_receipt_click_already_received(pw_page):
    """이미 수령완료된 페이지는 ALREADY_RECEIVED로 판정한다."""
    service, events = _service_with_page(pw_page, WITCHFORM_ALREADY_RECEIVED_HTML)
    result = service._handle_click_receipt()

    assert not result.success
    assert result.error_code == "ALREADY_RECEIVED"
    assert events == ["receipt_complete"]
    assert service._current_page is None


def test_l3_receipt_click_missing_button_fails(pw_page):
    """버튼도 완료 표시도 없는 페이지는 PRIMARY_CLICK_FAIL로 실패한다."""
    service, events = _service_with_page(pw_page, WITCHFORM_BROKEN_HTML)
    result = service._handle_click_receipt()

    assert not result.success
    assert result.error_code == "PRIMARY_CLICK_FAIL"
    assert events == ["receipt_complete"]


def test_l3_fake_printer_queue_transitions():
    """프린터 큐 스텁의 대기→완료/실패 상태 전이를 검증한다.

    Boogle은 동일 내용 artifact의 중복 제출을 거부하므로 이미지 내용을 구분한다.
    """
    from PIL import Image

    image_a = Image.new("RGB", (64, 64), "white")
    image_b = Image.new("RGB", (64, 64), "black")
    queue = FakePrinterQueue()
    queue.submit(image_a, job_name="Receipt_A")
    queue.submit(image_b, job_name="Receipt_B")
    assert len(queue.pending) == 2
    assert queue.run_all() == 2
    assert [j.job_name for j in queue.completed] == ["Receipt_A", "Receipt_B"]
    assert not queue.failed

    failing = FakePrinterQueue(failure=RuntimeError("스풀러 오류"))
    failing.submit(image_a, job_name="Receipt_C")
    assert failing.run_next() is False
    assert [j.job_name for j in failing.failed] == ["Receipt_C"]
    assert not failing.pending


def test_l3_still_camera_decodes_real_qr():
    """스틸 카메라 프레임이 실제 OpenCV 디코더로 테스트 QR payload를 복원한다."""
    camera = StillCameraStub()
    payload = camera.decode_next()
    assert payload == TEST_QR_URL
    assert camera.frames_read == 1


def test_l3_fake_printer_backend_records_job():
    """FakePrinterBackend가 작업을 기록하고 실패를 재현한다."""
    from PIL import Image

    image = Image.new("RGB", (32, 32), "blue")
    backend = FakePrinterBackend()
    backend.print_image(image, printer_name="테스트프린터", job_name="Receipt_X")
    assert backend.jobs[0].job_name == "Receipt_X"
    assert backend.jobs[0].printer_name == "테스트프린터"
