"""주문 정보 표시와 수령 완료 처리를 관리하는 ViewModel."""
from __future__ import annotations

from datetime import datetime

from models.order_model import Order
from services.browser_service import BrowserService, ReceiptClickResult
from services.excel_service import ExcelService


class OrderViewModel:
    """주문 조회 상태와 브라우저 연동을 관리한다."""

    def __init__(self, excel_service: ExcelService, browser_service: BrowserService):
        self._excel_service = excel_service
        self._browser_service = browser_service
        self._current_order: Order | None = None

    @property
    def current_order(self) -> Order | None:
        """현재 로드된 주문 정보를 반환한다."""
        return self._current_order

    def load_order(self, order_number: str, url: str, *, open_page: bool = True) -> Order | None:
        """주문번호로 주문을 조회하고 상세 페이지를 연다."""
        order = self._excel_service.find_order(order_number)
        if not order:
            return None

        order.url = url
        self._current_order = order
        if open_page:
            self._browser_service.open_page(url)
        return order

    def open_current_order_page(self) -> bool:
        """현재 주문 상세 페이지를 브라우저에 연다."""
        if not self._current_order or not self._current_order.url:
            return False
        self._browser_service.open_page(self._current_order.url)
        return True

    def complete_receipt(self) -> ReceiptClickResult:
        """현재 열린 주문 페이지에서 수령 완료를 시도한다."""
        return self._browser_service.click_receipt_button()

    def mark_current_order_received(self, timestamp_str: str | None = None) -> bool:
        """현재 주문의 수령 완료 시각을 엑셀에 저장한다."""
        if not self._current_order:
            return False

        value = timestamp_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self._excel_service.mark_order_received(self._current_order.order_number, value):
            return False

        self._current_order.received_at = value
        return True

    def rollback_current_order_received(self, previous_value: str) -> bool:
        """현재 주문의 수령 완료 값을 이전 상태로 되돌린다."""
        if not self._current_order:
            return False

        ok = self._excel_service.rollback_order_received(
            self._current_order.order_number,
            previous_value,
        )
        if ok:
            self._current_order.received_at = previous_value
        return ok
