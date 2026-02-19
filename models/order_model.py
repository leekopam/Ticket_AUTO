"""주문 정보 데이터 모델."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Order:
    """엑셀/QR 처리 결과로 사용하는 주문 엔터티."""

    order_number: str = ""
    name: str = ""
    phone: str = ""
    seat: str = ""
    goods: list[str] = field(default_factory=list)
    url: str = ""

    @property
    def goods_display(self) -> str:
        """상품 목록을 화면 출력용 줄바꿈 문자열로 반환한다."""
        return "\n".join(self.goods)

    @property
    def is_valid(self) -> bool:
        """주문 데이터의 최소 유효성을 확인한다."""
        return bool(self.order_number and self.name)
