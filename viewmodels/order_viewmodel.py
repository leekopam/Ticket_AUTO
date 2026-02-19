"""주문 정보 표시와 수령 완료 처리를 관리하는 ViewModel."""
from __future__ import annotations

from models.order_model import Order
from services.browser_service import BrowserService
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

    def load_order(self, order_number: str, url: str) -> Order | None:
        """주문번호로 주문을 조회하고 상세 페이지를 연다."""
        order = self._excel_service.find_order(order_number)
        if not order:
            return None

        order.url = url
        self._current_order = order
        self._browser_service.open_page(url)
        return order

    def complete_receipt(self) -> None:
        """현재 열린 주문 페이지에서 수령 완료를 시도한다."""
        self._browser_service.click_receipt_button()
