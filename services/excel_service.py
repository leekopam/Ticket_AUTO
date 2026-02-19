"""
엑셀 파일에서 주문 정보를 읽음
"""
import re
from openpyxl import load_workbook
from models.order_model import Order


class ExcelService:

    def __init__(self, file_path: str = "data.xlsx"):
        self._file_path = file_path
    
    def find_order(self, order_number: str) -> Order | None:
        """주문번호로 주문 정보 조회"""
        wb = load_workbook(self._file_path)
        ws = wb.active
        
        # 헤더 파싱
        headers = {cell.value: idx for idx, cell in enumerate(ws[1], 1)}
        
        if "주문번호" not in headers:
            return None
        
        order_col = headers["주문번호"]
        name_col = headers.get("주문자명")
        phone_col = headers.get("주문자연락처")
        seat_col = headers.get("좌석번호")
        
        goods_col = {}
        for name, idx in headers.items():
            if name and re.match(r'\[상품\d+\]', name):
                goods_col[name] = idx
        
        # 주문 검색
        for row in ws.iter_rows(min_row=2, values_only=False):
            if row[order_col - 1].value == order_number:
                # 굿즈 정보 수집
                goods_list = []
                for header_name in sorted(goods_col.keys()):
                    col_idx = goods_col[header_name]
                    quantity = row[col_idx - 1].value
                    if quantity and int(quantity) > 0:
                        clean_name = re.sub(r'\[상품\d+\]\s*', '', header_name)
                        goods_list.append(f"{clean_name} x{quantity}")
                
                return Order(
                    order_number=order_number,
                    name=row[name_col - 1].value if name_col else "",
                    phone=row[phone_col - 1].value if phone_col else "",
                    seat=row[seat_col - 1].value if seat_col else "",
                    goods=goods_list
                )
        
        return None
