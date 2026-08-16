"""엑셀 수령확인 상태 관리 테스트."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from services.excel_service import ExcelService, ORDER_STATUS_HEADER, RECEIPT_HEADER


def _create_workbook(path: Path, *, header_order_label: str = "주문번호") -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "번호",
            header_order_label,
            "주문자명",
            "주문자연락처",
            "좌석번호",
            "[상품1] 티켓",
        ]
    )
    ws.append([1, "ORDER-001", "홍길동", "010-0000-0000", "A-1", 2])
    wb.save(path)
    wb.close()


class ExcelReceiptStateTest(unittest.TestCase):
    def test_mark_order_status_persists_trade_closed_for_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "data.xlsx"
            _create_workbook(file_path)
            service = ExcelService(str(file_path))

            self.assertTrue(service.mark_order_status("ORDER-001", "거래종료"))

            loaded = load_workbook(file_path, read_only=True, data_only=True)
            ws = loaded.active
            headers = [cell.value for cell in ws[1]]
            status_col = headers.index(ORDER_STATUS_HEADER) + 1
            self.assertEqual(ws.cell(row=2, column=status_col).value, "거래종료")
            loaded.close()

            from views import dashboard_flet_view as dashboard

            view_state = dashboard.build_order_search_view_state(
                "",
                "전체",
                service.search_orders(),
                [],
                None,
            )
            self.assertEqual(view_state.row_states[0].order_status_text, "거래종료")

    def test_mark_order_received_creates_header_and_persists_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "data.xlsx"
            _create_workbook(file_path)
            service = ExcelService(str(file_path))

            ok = service.mark_order_received("ORDER-001", "2026-02-23 11:22:33")
            self.assertTrue(ok)

            loaded = load_workbook(file_path)
            ws = loaded.active
            headers = [cell.value for cell in ws[1]]
            self.assertIn(RECEIPT_HEADER, headers)
            receipt_col = headers.index(RECEIPT_HEADER) + 1
            self.assertEqual(ws.cell(row=2, column=receipt_col).value, "2026-02-23 11:22:33")
            loaded.close()

            order = service.find_order("ORDER-001")
            self.assertIsNotNone(order)
            assert order is not None
            self.assertEqual(order.received_at, "2026-02-23 11:22:33")
            self.assertTrue(order.is_received)

    def test_rollback_order_received_restores_previous_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "data.xlsx"
            _create_workbook(file_path)
            service = ExcelService(str(file_path))

            self.assertTrue(service.mark_order_received("ORDER-001", "2026-02-23 09:00:00"))
            self.assertTrue(service.rollback_order_received("ORDER-001", ""))

            order = service.find_order("ORDER-001")
            self.assertIsNotNone(order)
            assert order is not None
            self.assertEqual(order.received_at, "")
            self.assertFalse(order.is_received)

    def test_get_received_status_map_returns_empty_for_missing_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "missing.xlsx"
            service = ExcelService(str(missing_path))

            self.assertEqual(service.get_received_status_map(), {})

    def test_get_received_status_map_includes_only_received_orders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "data.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.append(["주문번호", RECEIPT_HEADER])
            ws.append(["ORDER-001", "2026-02-23 09:00:00"])
            ws.append(["ORDER-002", ""])
            ws.append(["", "2026-02-23 10:00:00"])
            ws.append(["ORDER-003", "2026-02-23 11:00:00"])
            wb.save(file_path)
            wb.close()
            service = ExcelService(str(file_path))

            self.assertEqual(
                service.get_received_status_map(),
                {
                    "ORDER-001": "2026-02-23 09:00:00",
                    "ORDER-003": "2026-02-23 11:00:00",
                },
            )

    def test_header_spacing_variation_still_matches_order_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "data.xlsx"
            _create_workbook(file_path, header_order_label=" 주문번호 ")
            service = ExcelService(str(file_path))

            self.assertTrue(service.mark_order_received("ORDER-001", "2026-02-23 12:00:00"))
            order = service.find_order("ORDER-001")
            self.assertIsNotNone(order)
            assert order is not None
            self.assertEqual(order.received_at, "2026-02-23 12:00:00")


if __name__ == "__main__":
    unittest.main()
