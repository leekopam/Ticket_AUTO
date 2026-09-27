"""OrderViewModel 주문 스코프 수령 처리 테스트."""
from __future__ import annotations

from services.browser_service import ReceiptClickResult
from viewmodels.order_viewmodel import OrderViewModel


class _FakeBrowser:
    def __init__(self):
        self.calls: list[str] = []

    def process_order_receipt(self, url: str, timeout_sec: int = 60) -> ReceiptClickResult:
        self.calls.append(url)
        return ReceiptClickResult(success=True, verified=True)


class _FakeExcel:
    pass


def test_process_receipt_for_is_order_scoped():
    browser = _FakeBrowser()
    vm = OrderViewModel(_FakeExcel(), browser)

    result = vm.process_receipt_for("AAAA_BBBB", "https://witchform.com/w/order/1")
    assert result.success and result.verified
    assert browser.calls == ["https://witchform.com/w/order/1"]
    # 공유 상태(_current_order)를 건드리지 않는다
    assert vm.current_order is None


def test_process_receipt_for_validates_input():
    vm = OrderViewModel(_FakeExcel(), _FakeBrowser())
    result = vm.process_receipt_for("", "https://x")
    assert not result.success
    assert result.error_code == "INVALID_REQUEST"
