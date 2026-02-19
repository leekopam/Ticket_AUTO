"""
주문 정보 표시 창
"""
import tkinter as tk
from tkinter import font
from typing import Callable
from models.order_model import Order
from viewmodels.order_viewmodel import OrderViewModel


class OrderView:
    """주문 정보 표시 UI"""
    
    def __init__(self, viewmodel: OrderViewModel):
        self._viewmodel = viewmodel
        self._root = None
        
        # Label 인스턴스 변수 (데이터 갱신용)
        self._name_label = None
        self._phone_label = None
        self._seat_label = None
        self._goods_label = None
        
        # 수령완료 처리 완료 후 콜백
        self._on_receipt_complete: Callable[[], None] | None = None
    
    def set_on_receipt_complete(self, callback: Callable[[], None]) -> None:
        """수령완료 처리 완료 시 호출할 콜백 설정"""
        self._on_receipt_complete = callback
    
    def show_or_update(self, order: Order) -> None:
        """
        주문 정보 표시:
        - 창이 없으면 새로 생성
        - 창이 있으면 데이터만 갱신
        """
        if self._root is None or not self._is_window_alive():
            self._create_window(order)
        else:
            self._update_data(order)
        
        # 자동으로 수령완료 처리 시작
        self._viewmodel.complete_receipt()
    
    def _is_window_alive(self) -> bool:
        """창이 살아있는지 확인"""
        try:
            return self._root.winfo_exists()
        except:
            return False
    
    def _create_window(self, order: Order) -> None:
        """새 창 생성"""
        self._root = tk.Tk()
        self._root.title("주문 정보")
        self._root.geometry("600x600")
        
        large_font = font.Font(size=20, weight="bold")
        medium_font = font.Font(size=15, weight="bold")
        
        # Label 생성 및 인스턴스 변수에 저장
        self._name_label = tk.Label(self._root, text=f"주문자명: {order.name}", font=large_font)
        self._name_label.pack(pady=20)
        
        self._phone_label = tk.Label(self._root, text=f"주문자연락처: {order.phone}", font=large_font)
        self._phone_label.pack(pady=20)
        
        self._seat_label = tk.Label(self._root, text=f"좌석번호: {order.seat}", font=large_font)
        self._seat_label.pack(pady=20)
        
        self._goods_label = tk.Label(self._root, text=f"굿즈: \n{order.goods_display}", font=medium_font)
        self._goods_label.pack(pady=20)
        
        self._root.protocol("WM_DELETE_WINDOW", self._close)
        self._root.update()
    
    def _update_data(self, order: Order) -> None:
        """기존 창의 데이터만 갱신"""
        self._name_label.config(text=f"주문자명: {order.name}")
        self._phone_label.config(text=f"주문자연락처: {order.phone}")
        self._seat_label.config(text=f"좌석번호: {order.seat}")
        self._goods_label.config(text=f"굿즈: \n{order.goods_display}")
        
        # 창을 앞으로 가져오기
        self._root.lift()
        self._root.update()
    
    def on_receipt_complete_callback(self) -> None:
        """수령완료 처리가 완료되었을 때 호출 (BrowserService에서 호출)"""
        if self._on_receipt_complete:
            self._on_receipt_complete()
    
    def _close(self) -> None:
        """창 닫기"""
        if self._root:
            self._root.destroy()
            self._root = None
