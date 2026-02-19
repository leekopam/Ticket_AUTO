"""
주문 정보 데이터만 정의
"""
from dataclasses import dataclass, field


@dataclass
class Order:
    """주문 정보 데이터 모델"""
    
    # 주문 식별
    order_number: str = ""
    
    # 주문자 정보
    name: str = ""
    phone: str = ""
    
    # 좌석 정보
    seat: str = ""
    
    # 굿즈 정보 (예: ["테스트 티켓 x1", "대파 카리비너 x2"])
    goods: list[str] = field(default_factory=list)
    
    # 주문 URL
    url: str = ""
    
    @property
    def goods_display(self) -> str:
        """굿즈 정보를 줄바꿈으로 연결한 표시용 문자열"""
        return "\n".join(self.goods)
    
    @property
    def is_valid(self) -> bool:
        """주문 정보가 유효한지 확인"""
        return bool(self.order_number and self.name)
