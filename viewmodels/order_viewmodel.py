"""
주문 정보 표시 및 수령 완료 처리를 관리
"""
from models.order_model import Order
from services.excel_service import ExcelService
from services.browser_service import BrowserService


class OrderViewModel:
    """주문 정보 상태 관리"""
    
    def __init__(self, excel_service: ExcelService, browser_service: BrowserService):
        self._excel_service = excel_service
        self._browser_service = browser_service
        self._current_order: Order | None = None
    
    @property
    def current_order(self) -> Order | None:
        """현재 주문 정보"""
        return self._current_order
    
    def load_order(self, order_number: str, url: str) -> Order | None:
        order = self._excel_service.find_order(order_number)
        
        if order:
            order.url = url
            self._current_order = order
            
            # 브라우저에서 페이지 열기
            self._browser_service.open_page(url)
            
            print(order.goods)
            return order
        
        return None
    
    def complete_receipt(self) -> None:
        """수령 완료 처리"""
        self._browser_service.click_receipt_button()
