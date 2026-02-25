"""
Load order information from Excel.
"""
from __future__ import annotations

import re
import time

from openpyxl import load_workbook

from models.order_model import Order


PRODUCT_HEADER_RE = re.compile(r"^\[상품(\d+)\]")
RECEIPT_HEADER = "수령확인"
_WRITE_RETRY_COUNT = 3
_WRITE_RETRY_DELAY_SEC = 0.2


class ExcelService:
    def __init__(self, file_path: str = "data.xlsx"):
        self._file_path = file_path

    def search_orders(self, keyword: str = "") -> list[Order]:
        """주문번호/이름/연락처로 부분 일치 검색. 빈 키워드면 전체 반환(최대 200건)."""
        keyword = keyword.strip()
        results: list[Order] = []
        max_results = 200

        workbook = load_workbook(self._file_path, read_only=True, data_only=True)
        try:
            ws = workbook.active
            headers = self._read_headers(ws)

            order_col = self._find_col(headers, ("주문번호",))
            if not order_col:
                return results

            name_col = self._find_col(headers, ("주문자명", "수령자명"))
            phone_col = self._find_col(headers, ("주문자연락처", "수령자연락처"))
            seat_col = self._find_col(headers, ("좌석번호",))
            received_col = self._find_col(headers, (RECEIPT_HEADER,))
            goods_cols = self._parse_goods_cols(headers)

            for row in ws.iter_rows(min_row=2, values_only=True):
                order_number_val = str(self._cell(row, order_col)).strip()
                if not order_number_val:
                    continue

                name_val = str(self._cell(row, name_col)).strip() if name_col else ""
                phone_val = str(self._cell(row, phone_col)).strip() if phone_col else ""

                if keyword and not any(
                    keyword in field for field in (order_number_val, name_val, phone_val)
                ):
                    continue

                goods_list = self._build_goods_list(row, goods_cols)
                results.append(Order(
                    order_number=order_number_val,
                    name=name_val,
                    phone=phone_val,
                    seat=str(self._cell(row, seat_col)).strip() if seat_col else "",
                    goods=goods_list,
                    received_at=str(self._cell(row, received_col)).strip() if received_col else "",
                ))
                if len(results) >= max_results:
                    break

            return results
        finally:
            workbook.close()

    def find_order(self, order_number: str) -> Order | None:
        """Find order by order_number."""
        workbook = load_workbook(self._file_path, read_only=True, data_only=True)
        try:
            ws = workbook.active
            headers = self._read_headers(ws)

            order_col = self._find_col(headers, ("주문번호",))
            if not order_col:
                return None

            name_col = self._find_col(headers, ("주문자명", "수령자명"))
            phone_col = self._find_col(headers, ("주문자연락처", "수령자연락처"))
            seat_col = self._find_col(headers, ("좌석번호",))
            received_col = self._find_col(headers, (RECEIPT_HEADER,))

            goods_cols = self._parse_goods_cols(headers)

            for row in ws.iter_rows(min_row=2, values_only=True):
                current_order = self._cell(row, order_col)
                if str(current_order).strip() != order_number:
                    continue

                return Order(
                    order_number=order_number,
                    name=str(self._cell(row, name_col)).strip() if name_col else "",
                    phone=str(self._cell(row, phone_col)).strip() if phone_col else "",
                    seat=str(self._cell(row, seat_col)).strip() if seat_col else "",
                    goods=self._build_goods_list(row, goods_cols),
                    received_at=str(self._cell(row, received_col)).strip() if received_col else "",
                )

            return None
        finally:
            workbook.close()

    def mark_order_received(self, order_number: str, timestamp_str: str) -> bool:
        """Mark order as received and persist timestamp."""
        return self._update_order_received(order_number, timestamp_str)

    def rollback_order_received(self, order_number: str, previous_value: str) -> bool:
        """Restore previous receipt value when print step fails."""
        return self._update_order_received(order_number, previous_value)

    def _update_order_received(self, order_number: str, value: str) -> bool:
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                ws = workbook.active

                headers = self._read_headers(ws)
                order_col = self._find_col(headers, ("주문번호",))
                if not order_col:
                    return False

                receipt_col = self._find_col(headers, (RECEIPT_HEADER,))
                if not receipt_col:
                    receipt_col = ws.max_column + 1
                    ws.cell(row=1, column=receipt_col, value=RECEIPT_HEADER)

                target_row = self._find_row_by_order(ws, order_col, order_number)
                if not target_row:
                    return False

                ws.cell(row=target_row, column=receipt_col, value=(value or "").strip())
                workbook.save(self._file_path)
                return True
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    return False
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    workbook.close()
        return False

    @staticmethod
    def _parse_goods_cols(headers: dict[str, int]) -> list[tuple[int, int, str]]:
        """상품 헤더를 파싱해 (상품번호, 컬럼인덱스, 상품명) 리스트를 반환한다."""
        goods_cols: list[tuple[int, int, str]] = []
        for header_name, col_idx in headers.items():
            match = PRODUCT_HEADER_RE.match(header_name)
            if not match:
                continue
            product_index = int(match.group(1))
            clean_name = PRODUCT_HEADER_RE.sub("", header_name).strip()
            goods_cols.append((product_index, col_idx, clean_name))
        goods_cols.sort(key=lambda item: item[0])
        return goods_cols

    @staticmethod
    def _build_goods_list(row: tuple, goods_cols: list[tuple[int, int, str]]) -> list[str]:
        """행에서 상품 목록 문자열 리스트를 생성한다."""
        goods_list: list[str] = []
        for product_index, col_idx, goods_name in goods_cols:
            quantity = ExcelService._to_int(ExcelService._cell(row, col_idx))
            if quantity > 0:
                label = goods_name or f"상품{product_index}"
                goods_list.append(f"{label} x{quantity}")
        return goods_list

    @staticmethod
    def _read_headers(ws) -> dict[str, int]:
        header_row = [cell.value for cell in ws[1]]
        return {
            str(value).strip(): idx
            for idx, value in enumerate(header_row, 1)
            if value is not None and str(value).strip()
        }

    @staticmethod
    def _find_row_by_order(ws, order_col: int, order_number: str) -> int | None:
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            current_order = ExcelService._cell(row, order_col)
            if str(current_order).strip() == order_number:
                return row_idx
        return None

    @staticmethod
    def _find_col(headers: dict[str, int], candidates: tuple[str, ...]) -> int | None:
        for candidate in candidates:
            if candidate in headers:
                return headers[candidate]

        normalized = {key.replace(" ", ""): idx for key, idx in headers.items()}
        for candidate in candidates:
            key = candidate.replace(" ", "")
            if key in normalized:
                return normalized[key]
        return None

    @staticmethod
    def _cell(row: tuple, col_idx: int | None):
        if not col_idx:
            return ""
        idx = col_idx - 1
        if idx < 0 or idx >= len(row):
            return ""
        value = row[idx]
        return "" if value is None else value

    @staticmethod
    def _to_int(value) -> int:
        if value is None or value == "":
            return 0
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return 0
