"""
Load order information from Excel.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime

from openpyxl import load_workbook

from models.order_model import Order
from project_paths import ensure_managed_data_file, resolve_project_path


PRODUCT_HEADER_RE = re.compile(r"^\[상품(\d+)\]")
RECEIPT_HEADER = "수령확인"
SEAT_HEADER = "좌석번호"
ORDER_STATUS_HEADER = "주문상태"
SOURCE_PROGRESS_STATUS_HEADER = "진행상태"
PROCESSING_TIME_HEADER = "처리시간"
META_SHEET = "_meta"
OPERATIONS_SHEET = "_operations"
OPERATION_HEADERS = (
    "request_id",
    "order_id",
    "action",
    "device_id",
    "state",
    "result_json",
    "created_at",
    "updated_at",
    # 기존 파일과의 열 정렬을 위해 새 열은 항상 맨 뒤에 추가한다
    "device_name",
)
META_DATASET_ID_KEY = "dataset_id"
META_CREATED_AT_KEY = "created_at"
_WRITE_RETRY_COUNT = 3
_WRITE_RETRY_DELAY_SEC = 0.2
_WORKBOOK_WRITE_LOCK = threading.RLock()  # ponytail: 전역 직렬화; 파일별 동시 쓰기가 필요해지면 경로별 락으로 교체


def _synchronized(fn):
    """쓰기 메서드를 서비스 단위 락으로 직렬화한다."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._write_lock:
            return fn(self, *args, **kwargs)

    return wrapper


class ExcelService:
    def __init__(self, file_path: str | None = None):
        if file_path is None:
            self._file_path = str(ensure_managed_data_file())
        else:
            self._file_path = str(resolve_project_path(file_path))
        self._write_lock = _WORKBOOK_WRITE_LOCK

    def search_orders(self, keyword: str = "") -> list[Order]:
        """주문번호/이름/연락처로 부분 일치 검색. 빈 키워드면 전체 반환(최대 200건)."""
        return self._search_orders(keyword, max_results=200)

    def search_orders_all(self, keyword: str = "") -> list[Order]:
        """API용 전체 조회 — 200건 제한 없음. 읽기 실패 시 예외가 전파된다."""
        return self._search_orders(keyword, max_results=None)

    def _search_orders(self, keyword: str, max_results: int | None) -> list[Order]:
        keyword = keyword.strip()
        results: list[Order] = []

        workbook = load_workbook(self._file_path, read_only=True, data_only=True)
        try:
            ws = self._data_sheet(workbook)
            headers = self._read_headers(ws)

            order_col = self._find_col(headers, ("주문번호",))
            if not order_col:
                return results

            name_col = self._find_col(headers, ("주문자명", "수령자명"))
            phone_col = self._find_col(headers, ("주문자연락처", "수령자연락처"))
            seat_col = self._find_col(headers, ("좌석번호",))
            received_col = self._find_col(headers, (RECEIPT_HEADER,))
            processing_time_col = self._find_col(headers, (PROCESSING_TIME_HEADER,))
            status_col = self._find_col(headers, (ORDER_STATUS_HEADER,))
            progress_status_col = self._find_col(headers, (SOURCE_PROGRESS_STATUS_HEADER,))
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
                    processing_time=str(self._cell(row, processing_time_col)).strip() if processing_time_col else "",
                    order_status=self._resolve_order_status(row, status_col, progress_status_col),
                ))
                if max_results is not None and len(results) >= max_results:
                    break

            return results
        finally:
            workbook.close()

    def has_order_column(self) -> bool:
        """가져올 파일이 주문 검색에 필요한 주문번호 헤더를 갖췄는지 확인한다."""
        workbook = load_workbook(self._file_path, read_only=True, data_only=True)
        try:
            return bool(self._find_col(self._read_headers(self._data_sheet(workbook)), ("주문번호",)))
        finally:
            workbook.close()

    def find_order(self, order_number: str) -> Order | None:
        """Find order by order_number."""
        workbook = load_workbook(self._file_path, read_only=True, data_only=True)
        try:
            ws = self._data_sheet(workbook)
            headers = self._read_headers(ws)

            order_col = self._find_col(headers, ("주문번호",))
            if not order_col:
                return None

            name_col = self._find_col(headers, ("주문자명", "수령자명"))
            phone_col = self._find_col(headers, ("주문자연락처", "수령자연락처"))
            seat_col = self._find_col(headers, ("좌석번호",))
            received_col = self._find_col(headers, (RECEIPT_HEADER,))
            processing_time_col = self._find_col(headers, (PROCESSING_TIME_HEADER,))
            status_col = self._find_col(headers, (ORDER_STATUS_HEADER,))
            progress_status_col = self._find_col(headers, (SOURCE_PROGRESS_STATUS_HEADER,))

            goods_cols = self._parse_goods_cols(headers)

            for row in ws.iter_rows(min_row=2, values_only=True):
                current_order = self._cell(row, order_col)
                if str(current_order).strip().upper() != order_number.upper():
                    continue

                return Order(
                    order_number=order_number,
                    name=str(self._cell(row, name_col)).strip() if name_col else "",
                    phone=str(self._cell(row, phone_col)).strip() if phone_col else "",
                    seat=str(self._cell(row, seat_col)).strip() if seat_col else "",
                    goods=self._build_goods_list(row, goods_cols),
                    received_at=str(self._cell(row, received_col)).strip() if received_col else "",
                    processing_time=str(self._cell(row, processing_time_col)).strip() if processing_time_col else "",
                    order_status=self._resolve_order_status(row, status_col, progress_status_col),
                )

            return None
        finally:
            workbook.close()

    def find_orders_by_customer(self, name: str = "", phone: str = "") -> list[Order]:
        """Find orders by exact customer name/phone match."""
        normalized_name = (name or "").strip()
        normalized_phone = self._normalize_phone(phone)
        if not normalized_name and not normalized_phone:
            return []

        workbook = load_workbook(self._file_path, read_only=True, data_only=True)
        try:
            ws = self._data_sheet(workbook)
            headers = self._read_headers(ws)

            order_col = self._find_col(headers, ("주문번호",))
            if not order_col:
                return []

            name_col = self._find_col(headers, ("주문자명",))
            phone_col = self._find_col(headers, ("주문자연락처",))
            recv_name_col = self._find_col(headers, ("수령자명",))
            recv_phone_col = self._find_col(headers, ("수령자연락처",))
            seat_col = self._find_col(headers, ("좌석번호",))
            received_col = self._find_col(headers, (RECEIPT_HEADER,))
            processing_time_col = self._find_col(headers, (PROCESSING_TIME_HEADER,))
            status_col = self._find_col(headers, (ORDER_STATUS_HEADER,))
            progress_status_col = self._find_col(headers, (SOURCE_PROGRESS_STATUS_HEADER,))
            goods_cols = self._parse_goods_cols(headers)

            matches: list[Order] = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                order_number = str(self._cell(row, order_col)).strip()
                if not order_number:
                    continue

                names = {
                    str(self._cell(row, name_col)).strip() if name_col else "",
                    str(self._cell(row, recv_name_col)).strip() if recv_name_col else "",
                }
                phones = {
                    self._normalize_phone(self._cell(row, phone_col)) if phone_col else "",
                    self._normalize_phone(self._cell(row, recv_phone_col)) if recv_phone_col else "",
                }

                name_matches = not normalized_name or normalized_name in names
                phone_matches = not normalized_phone or normalized_phone in phones
                if not name_matches or not phone_matches:
                    continue

                matches.append(Order(
                    order_number=order_number,
                    name=str(self._cell(row, name_col)).strip() if name_col else "",
                    phone=str(self._cell(row, phone_col)).strip() if phone_col else "",
                    seat=str(self._cell(row, seat_col)).strip() if seat_col else "",
                    goods=self._build_goods_list(row, goods_cols),
                    received_at=str(self._cell(row, received_col)).strip() if received_col else "",
                    processing_time=str(self._cell(row, processing_time_col)).strip() if processing_time_col else "",
                    order_status=self._resolve_order_status(row, status_col, progress_status_col),
                ))

            return matches
        finally:
            workbook.close()

    def find_unique_order_by_customer(self, name: str = "", phone: str = "") -> Order | None:
        matches = self.find_orders_by_customer(name=name, phone=phone)
        if len(matches) != 1:
            return None
        return matches[0]

    @_synchronized
    def _ensure_column(self, header_name: str) -> None:
        """지정한 헤더 컬럼이 없으면 자동 추가한다."""
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                ws = self._data_sheet(workbook)
                headers = self._read_headers(ws)
                if self._find_col(headers, (header_name,)):
                    return
                new_col = ws.max_column + 1
                ws.cell(row=1, column=new_col, value=header_name)
                self._save_atomic(workbook)
                return
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    return
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    workbook.close()

    def ensure_seat_column(self) -> None:
        """data.xlsx에 좌석번호 컬럼이 없으면 자동 추가한다."""
        self._ensure_column(SEAT_HEADER)

    def ensure_receipt_column(self) -> None:
        """data.xlsx에 수령확인 컬럼이 없으면 자동 추가한다."""
        self._ensure_column(RECEIPT_HEADER)

    def ensure_order_status_column(self) -> None:
        """data.xlsx에 주문상태 컬럼이 없으면 자동 추가한다."""
        self._ensure_column(ORDER_STATUS_HEADER)

    @_synchronized
    def ensure_processing_time_column(self) -> bool:
        """처리시간 헤더를 하나만 유지하고 마지막 열로 정규화한다."""
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                ws = self._data_sheet(workbook)
                if self._ensure_final_processing_time_column(ws):
                    self._save_atomic(workbook)
                return True
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    return False
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    workbook.close()
        return False

    @_synchronized
    def mark_order_status(self, order_number: str, status: str) -> bool:
        """주문의 주문상태 값을 엑셀에 저장한다."""
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                ws = self._data_sheet(workbook)
                headers = self._read_headers(ws)
                order_col = self._find_col(headers, ("주문번호",))
                if not order_col:
                    return False
                status_col = self._find_col(headers, (ORDER_STATUS_HEADER,))
                if not status_col:
                    status_col = ws.max_column + 1
                    ws.cell(row=1, column=status_col, value=ORDER_STATUS_HEADER)
                target_row = self._find_row_by_order(ws, order_col, order_number)
                if not target_row:
                    return False
                ws.cell(row=target_row, column=status_col, value=(status or "").strip())
                self._save_atomic(workbook)
                return True
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    return False
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    workbook.close()
        return False

    def get_received_status_map(self) -> dict[str, str]:
        """현재 파일에서 수령확인이 기록된 주문번호 → 타임스탬프 맵을 반환한다."""
        workbook = None
        try:
            workbook = load_workbook(self._file_path, read_only=True, data_only=True)
            ws = self._data_sheet(workbook)
            headers = self._read_headers(ws)
            order_col = self._find_col(headers, ("주문번호",))
            received_col = self._find_col(headers, (RECEIPT_HEADER,))
            if not order_col or not received_col:
                return {}
            result: dict[str, str] = {}
            for row in ws.iter_rows(min_row=2, values_only=True):
                order_number = str(self._cell(row, order_col)).strip()
                received = str(self._cell(row, received_col)).strip()
                if order_number and received:
                    result[order_number] = received
            return result
        except Exception:
            return {}
        finally:
            if workbook is not None:
                workbook.close()

    @_synchronized
    def bulk_restore_received_status(self, received_map: dict[str, str]) -> int:
        """파일 교체 후 수령확인 상태를 일괄 복원한다. 반환값: 복원된 건수."""
        if not received_map:
            return 0
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                ws = self._data_sheet(workbook)
                headers = self._read_headers(ws)
                order_col = self._find_col(headers, ("주문번호",))
                if not order_col:
                    return 0
                receipt_col = self._find_col(headers, (RECEIPT_HEADER,))
                if not receipt_col:
                    receipt_col = ws.max_column + 1
                    ws.cell(row=1, column=receipt_col, value=RECEIPT_HEADER)
                count = 0
                for row_idx in range(2, ws.max_row + 1):
                    order_number = str(ws.cell(row=row_idx, column=order_col).value or "").strip()
                    if order_number in received_map:
                        ws.cell(row=row_idx, column=receipt_col, value=received_map[order_number])
                        count += 1
                self._save_atomic(workbook)
                return count
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    return 0
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    workbook.close()
        return 0

    @_synchronized
    def reset_processing_state(self) -> dict[str, int]:
        """수령 처리 표시를 전부 초기화한다 (리허설/재운영용).

        - 수령확인·처리시간: 모든 행에서 지운다
        - 주문상태: 앱이 쓴 값(거래종료/확인필요)만 지워 원본 진행상태로 복귀한다
        - _operations 시트: 폰 처리 기록을 전부 삭제한다
        반환: {"receipts": 비워진 주문 수, "operations": 삭제된 작업 기록 수}
        """
        app_statuses = {"거래종료", "확인필요"}
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                ws = self._data_sheet(workbook)
                headers = self._read_headers(ws)
                received_col = self._find_col(headers, (RECEIPT_HEADER,))
                processing_col = self._find_col(headers, (PROCESSING_TIME_HEADER,))
                status_col = self._find_col(headers, (ORDER_STATUS_HEADER,))
                cleared = 0
                for row_idx in range(2, ws.max_row + 1):
                    received = str(
                        ws.cell(row=row_idx, column=received_col).value or ""
                    ).strip() if received_col else ""
                    status = str(
                        ws.cell(row=row_idx, column=status_col).value or ""
                    ).strip() if status_col else ""
                    if not received and status not in app_statuses:
                        continue
                    # ws.cell(value=None)은 할당을 건너뛰므로 .value로 비운다
                    if received_col:
                        ws.cell(row=row_idx, column=received_col).value = None
                    if processing_col:
                        ws.cell(row=row_idx, column=processing_col).value = None
                    if status_col and status in app_statuses:
                        ws.cell(row=row_idx, column=status_col).value = None
                    cleared += 1
                ops_cleared = 0
                if OPERATIONS_SHEET in workbook.sheetnames:
                    ops_ws = workbook[OPERATIONS_SHEET]
                    ops_cleared = max(ops_ws.max_row - 1, 0)
                    if ops_cleared:
                        ops_ws.delete_rows(2, ops_cleared)
                self._save_atomic(workbook)
                return {"receipts": cleared, "operations": ops_cleared}
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    raise
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    workbook.close()
        return {"receipts": 0, "operations": 0}

    def get_product_names(self) -> list[str]:
        """상품 컬럼명 리스트를 반환한다 (티켓 분류 UI용)."""
        workbook = load_workbook(self._file_path, read_only=True, data_only=True)
        try:
            ws = self._data_sheet(workbook)
            headers = self._read_headers(ws)
            goods_cols = self._parse_goods_cols(headers)
            return [name or f"상품{idx}" for idx, _col, name in goods_cols]
        finally:
            workbook.close()

    def mark_order_received(self, order_number: str, timestamp_str: str) -> bool:
        """Mark order as received and persist timestamp."""
        return self._update_order_received(order_number, timestamp_str)

    def rollback_order_received(self, order_number: str, previous_value: str) -> bool:
        """Restore previous receipt value when print step fails."""
        return self._update_order_received(order_number, previous_value)

    @_synchronized
    def mark_order_processing_time(self, order_number: str, timestamp_str: str) -> bool:
        """최종 성공한 주문의 처리시간을 마지막 열에 기록한다."""
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                ws = self._data_sheet(workbook)
                self._ensure_final_processing_time_column(ws)
                headers = self._read_headers(ws)
                order_col = self._find_col(headers, ("주문번호",))
                processing_time_col = self._find_col(headers, (PROCESSING_TIME_HEADER,))
                if not order_col or not processing_time_col:
                    return False
                target_row = self._find_row_by_order(ws, order_col, order_number)
                if not target_row:
                    return False
                ws.cell(row=target_row, column=processing_time_col, value=(timestamp_str or "").strip())
                self._save_atomic(workbook)
                return True
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    return False
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    workbook.close()
        return False

    @_synchronized
    def _update_order_received(self, order_number: str, value: str) -> bool:
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                ws = self._data_sheet(workbook)

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
                self._save_atomic(workbook)
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
    def _resolve_order_status(row: tuple, order_status_col: int | None, progress_status_col: int | None) -> str:
        order_status = str(ExcelService._cell(row, order_status_col)).strip()
        return order_status or str(ExcelService._cell(row, progress_status_col)).strip()

    @staticmethod
    def _ensure_final_processing_time_column(ws) -> bool:
        processing_time_cols = [
            column
            for column, cell in enumerate(ws[1], start=1)
            if str(cell.value or "").strip() == PROCESSING_TIME_HEADER
        ]
        if processing_time_cols == [ws.max_column]:
            return False

        processing_time_values = {
            row: next(
                (
                    ws.cell(row=row, column=column).value
                    for column in processing_time_cols
                    if str(ws.cell(row=row, column=column).value or "").strip()
                ),
                "",
            )
            for row in range(2, ws.max_row + 1)
        }
        for column in reversed(processing_time_cols):
            ws.delete_cols(column)
        processing_time_col = ws.max_column + 1
        ws.cell(row=1, column=processing_time_col, value=PROCESSING_TIME_HEADER)
        for row, value in processing_time_values.items():
            ws.cell(row=row, column=processing_time_col, value=value)
        return True

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
            if str(current_order).strip().upper() == order_number.upper():
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

    @staticmethod
    def _normalize_phone(value) -> str:
        raw = str(value).strip() if value is not None else ""
        return re.sub(r"\D+", "", raw)

    # ------------------------------------------------------------------
    # 내부 시트/원자적 저장 (API 서버 연동용)
    # ------------------------------------------------------------------

    def _data_sheet(self, workbook):
        """주문번호 헤더를 가진 데이터 시트를 고른다. 없으면 active 폴백."""
        for ws in workbook.worksheets:
            if ws.title in (META_SHEET, OPERATIONS_SHEET):
                continue
            if self._find_col(self._read_headers(ws), ("주문번호",)):
                return ws
        return workbook.active

    def _save_atomic(self, workbook) -> None:
        """임시 파일 저장 → 재오픈 검증 → 백업 → os.replace로 교체한다."""
        target = self._file_path
        tmp_path = f"{target}.tmp.xlsx"
        backup_path = f"{target}.bak"

        workbook.save(tmp_path)
        try:
            workbook.close()
        except Exception:
            pass

        check = None
        try:
            check = load_workbook(tmp_path, read_only=True)
            check.close()
        except Exception:
            check = None
        finally:
            if check is not None:
                try:
                    check.close()
                except Exception:
                    pass
        if check is None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError("저장된 파일 검증에 실패했습니다.")

        if os.path.exists(target):
            try:
                shutil.copy2(target, backup_path)
            except OSError:
                raise RuntimeError("백업 파일을 생성하지 못했습니다.")

        os.replace(tmp_path, target)

    @_synchronized
    def get_meta(self) -> dict[str, str]:
        """_meta 시트의 key=value 정보를 읽는다. 시트가 없으면 빈 dict."""
        workbook = None
        try:
            workbook = load_workbook(self._file_path, read_only=True, data_only=True)
            if META_SHEET not in workbook.sheetnames:
                return {}
            ws = workbook[META_SHEET]
            result: dict[str, str] = {}
            for row in ws.iter_rows(min_row=1, values_only=True):
                if not row or row[0] is None:
                    continue
                key = str(row[0]).strip()
                value = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
                if key:
                    result[key] = value
            return result
        except (PermissionError, OSError, RuntimeError):
            return {}
        finally:
            if workbook is not None:
                workbook.close()

    @_synchronized
    def ensure_dataset_id(self) -> str:
        """운영 파일의 dataset_id를 보장하고 반환한다. 없으면 새로 발급한다."""
        meta = self.get_meta()
        dataset_id = meta.get(META_DATASET_ID_KEY, "").strip()
        if dataset_id:
            return dataset_id
        dataset_id = uuid.uuid4().hex
        self._write_meta({META_DATASET_ID_KEY: dataset_id, META_CREATED_AT_KEY: self._now_str()})
        return dataset_id

    @_synchronized
    def write_meta(self, updates: dict[str, str]) -> bool:
        """_meta 시트의 키를 갱신한다."""
        return self._write_meta(updates)

    def _write_meta(self, updates: dict[str, str]) -> bool:
        if not updates:
            return True
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                if META_SHEET in workbook.sheetnames:
                    ws = workbook[META_SHEET]
                else:
                    ws = workbook.create_sheet(META_SHEET)
                    ws.sheet_state = "hidden"
                existing = {
                    str(ws.cell(row=row, column=1).value or "").strip(): row
                    for row in range(1, ws.max_row + 1)
                    if str(ws.cell(row=row, column=1).value or "").strip()
                }
                for key, value in updates.items():
                    key = str(key).strip()
                    if not key:
                        continue
                    target_row = existing.get(key)
                    if target_row is None:
                        target_row = ws.max_row + 1
                    ws.cell(row=target_row, column=1, value=key)
                    ws.cell(row=target_row, column=2, value=str(value).strip())
                self._save_atomic(workbook)
                return True
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    return False
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    try:
                        workbook.close()
                    except Exception:
                        pass
        return False

    @_synchronized
    def append_operation(self, record: dict[str, str]) -> bool:
        """_operations 시트에 작업 이력을 한 건 추가한다."""
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                ws = self._ensure_operations_sheet(workbook)
                # 헤더명 기준으로 열을 맞춘다 — 스키마 확장 시 기존 파일과 어긋나지 않게
                headers = self._read_headers(ws)
                for header in OPERATION_HEADERS:
                    if header not in headers:
                        headers[header] = (max(headers.values()) if headers else 0) + 1
                        ws.cell(row=1, column=headers[header], value=header)
                row_idx = ws.max_row + 1
                for header, col_idx in headers.items():
                    if header in OPERATION_HEADERS:
                        ws.cell(row=row_idx, column=col_idx, value=str(record.get(header) or ""))
                self._save_atomic(workbook)
                return True
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    return False
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    try:
                        workbook.close()
                    except Exception:
                        pass
        return False

    @_synchronized
    def get_operation(self, request_id: str) -> dict[str, str] | None:
        """request_id로 작업 이력 한 건을 조회한다."""
        request_id = str(request_id or "").strip()
        if not request_id:
            return None
        workbook = None
        try:
            workbook = load_workbook(self._file_path, read_only=True, data_only=True)
            if OPERATIONS_SHEET not in workbook.sheetnames:
                return None
            ws = workbook[OPERATIONS_SHEET]
            headers = self._read_headers(ws)
            for row in ws.iter_rows(min_row=2, values_only=True):
                current = str(self._cell(row, headers.get("request_id"))).strip()
                if current != request_id:
                    continue
                return {
                    name: str(self._cell(row, idx)).strip()
                    for name, idx in headers.items()
                    if name in OPERATION_HEADERS
                }
            return None
        except (PermissionError, OSError, RuntimeError):
            return None
        finally:
            if workbook is not None:
                workbook.close()

    @_synchronized
    def update_operation(self, request_id: str, updates: dict[str, str]) -> bool:
        """request_id 작업의 필드를 갱신한다."""
        request_id = str(request_id or "").strip()
        if not request_id:
            return False
        for attempt in range(_WRITE_RETRY_COUNT):
            workbook = None
            try:
                workbook = load_workbook(self._file_path)
                if OPERATIONS_SHEET not in workbook.sheetnames:
                    return False
                ws = workbook[OPERATIONS_SHEET]
                headers = self._read_headers(ws)
                request_col = headers.get("request_id")
                if not request_col:
                    return False
                target_row = self._find_row_by_order(ws, request_col, request_id)
                if not target_row:
                    return False
                for key, value in updates.items():
                    col = headers.get(str(key))
                    if not col:
                        continue
                    ws.cell(row=target_row, column=col, value=str(value or ""))
                updated_col = headers.get("updated_at")
                if updated_col:
                    ws.cell(row=target_row, column=updated_col, value=self._now_str())
                self._save_atomic(workbook)
                return True
            except (PermissionError, OSError):
                if attempt == _WRITE_RETRY_COUNT - 1:
                    return False
                time.sleep(_WRITE_RETRY_DELAY_SEC)
            finally:
                if workbook is not None:
                    try:
                        workbook.close()
                    except Exception:
                        pass
        return False

    def list_operations(self, limit: int = 500) -> list[dict[str, str]]:
        """최근 작업 이력을 오래된 순으로 반환한다."""
        workbook = None
        try:
            workbook = load_workbook(self._file_path, read_only=True, data_only=True)
            if OPERATIONS_SHEET not in workbook.sheetnames:
                return []
            ws = workbook[OPERATIONS_SHEET]
            headers = self._read_headers(ws)
            rows: list[dict[str, str]] = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                record = {
                    name: str(self._cell(row, idx)).strip()
                    for name, idx in headers.items()
                    if name in OPERATION_HEADERS
                }
                if record.get("request_id"):
                    rows.append(record)
            if limit and len(rows) > limit:
                rows = rows[-limit:]
            return rows
        except (PermissionError, OSError, RuntimeError):
            return []
        finally:
            if workbook is not None:
                workbook.close()

    def _ensure_operations_sheet(self, workbook):
        if OPERATIONS_SHEET in workbook.sheetnames:
            return workbook[OPERATIONS_SHEET]
        ws = workbook.create_sheet(OPERATIONS_SHEET)
        ws.sheet_state = "hidden"
        for col_idx, header in enumerate(OPERATION_HEADERS, start=1):
            ws.cell(row=1, column=col_idx, value=header)
        return ws

    def data_file_signature(self) -> str:
        """data_version 용 파일 서명(mtime+size)을 반환한다."""
        try:
            stat = os.stat(self._file_path)
        except OSError:
            return ""
        return f"{stat.st_mtime_ns:x}.{stat.st_size:x}"

    @staticmethod
    def _now_str() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
