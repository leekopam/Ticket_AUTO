from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from services.excel_service import ExcelService


def _create_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "번호",
            "주문자명",
            "주문자연락처",
            "수령자명",
            "수령자연락처",
            "주문번호",
            "좌석번호",
            "수령확인",
        ]
    )
    ws.append([1, "홍영기", "010-1234-5678", "", "", "WFLM7QSDTC_69D53CU23685", "A-1", ""])
    ws.append([2, "홍길동", "01012345678", "", "", "WFLM7QSDTC_69D5EXMK3A2E", "A-2", ""])
    ws.append([3, "홍길동", "010-9999-9999", "", "", "WFLM7QSDTC_69D5AMVE3D39", "A-3", ""])
    wb.save(path)
    wb.close()


class ExcelCustomerLookupTest(unittest.TestCase):
    def test_find_orders_by_customer_matches_normalized_phone(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "data.xlsx"
            _create_workbook(file_path)

            service = ExcelService(str(file_path))
            matches = service.find_orders_by_customer(name="홍길동", phone="010-1234-5678")

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].order_number, "WFLM7QSDTC_69D5EXMK3A2E")

    def test_find_unique_order_by_customer_returns_none_for_ambiguous_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "data.xlsx"
            _create_workbook(file_path)

            service = ExcelService(str(file_path))
            match = service.find_unique_order_by_customer(name="홍길동", phone="")

            self.assertIsNone(match)


if __name__ == "__main__":
    unittest.main()
