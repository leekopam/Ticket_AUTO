"""Ticket_AUTO ↔ Android LAN API v1 서버.

계약 정본: docs/contracts/api-v1.md
- TLS 필수(자체서명 + 폰 측 지문 핀), /pair 외 Bearer 토큰 인증
- request_id 멱등성, dataset_generation 세대 검증, 주문 단위 락
- XLSX 쓰기는 PC만 수행. /scan은 PC 스캔 런타임에서 수령 완료를 확인한다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated

from services.device_presence import (
    connection_quality,
    display_names,
    parse_seen_at,
    presence_state,
    signal_level,
)
from services.excel_service import ExcelService
from services.pairing_service import PairingService

logger = logging.getLogger(__name__)

SERVER_ID = "ticket-auto-pc"
MAX_BODY_BYTES = 32 * 1024
AUTO_PAUSE_FAILURE_STREAK = 5
# 동시 스캔 스레드 상한 — 폰 버그나 탈취 토큰의 폭주가 PC를 마비시키지 않게 한다
SCAN_IN_FLIGHT_MAX = 4

ACTION_RECEIPT = "receipt"
ACTION_SCAN_RECEIPT = "scan_receipt"
TERMINAL_STATES = {"succeeded", "already_processed", "failed", "needs_reconciliation", "rejected"}
CANCELLED_MARKERS = ("주문취소", "자동주문취소", "취소")
RECONCILE_MARKER = "확인필요"
WORK_LOG_DEFAULT_LIMIT = 200
WORK_LOG_MAX_LIMIT = 500


# ----------------------------------------------------------------------
# 요청 모델 (경계 검증)
# ----------------------------------------------------------------------

_ShortStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=128)]
_DeviceName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]
_DetailStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)]


class PairRequestBody(BaseModel):
    model_config = {"extra": "forbid"}

    join_code: _ShortStr = ""
    pair_ticket: _ShortStr = ""
    device_name: _DeviceName = ""
    # 재페어링 후에도 같은 기기로 인식하기 위한 앱 설치 식별자 (선택)
    device_uid: _ShortStr = ""


class ActionRequestBody(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: _ShortStr
    order_id: _ShortStr
    action: _ShortStr
    dataset_generation: _ShortStr = ""


class ScanRequestBody(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: _ShortStr
    qr_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2048)]


class ActionResultBody(BaseModel):
    model_config = {"extra": "forbid"}

    state: _ShortStr
    detail: _DetailStr = ""


# ----------------------------------------------------------------------
# 데이터 세대 추적
# ----------------------------------------------------------------------


class DatasetTracker:
    """dataset_id(_meta 시트)와 data_version(파일 서명)을 제공한다.

    파일이 교체되면 _meta가 없거나 다른 dataset_id를 갖게 되므로
    세대 식별자가 자연스럽게 바뀐다. 쓰기는 세대를 바꾸지 않는다.
    """

    def __init__(self, excel: ExcelService):
        self._excel = excel
        self._lock = threading.RLock()
        self._last_signature = ""
        self._dataset_id = ""

    def current(self) -> tuple[str, str]:
        """(dataset_generation, data_version) 반환."""
        with self._lock:
            signature = self._excel.data_file_signature()
            if signature != self._last_signature or not self._dataset_id:
                self._dataset_id = self._excel.ensure_dataset_id()
                self._last_signature = self._excel.data_file_signature()
            return self._dataset_id, self._last_signature


# ----------------------------------------------------------------------
# 작업 레지스트리 (request_id 멱등성 + 주문 락 + _operations 시트 영속화)
# ----------------------------------------------------------------------


class ActionRegistry:
    """작업 요청/결과를 기록하고 request_id 멱등성과 주문별 직렬화를 보장한다."""

    def __init__(
        self,
        excel: ExcelService,
        device_name_resolver: Callable[[str], str] | None = None,
    ):
        self._excel = excel
        self._device_name_resolver = device_name_resolver or (lambda _device_id: "")
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._order_locks: dict[str, str] = {}  # order_id -> request_id
        self._inflight_scans: dict[str, str] = {}  # qr_url -> request_id (비종결 스캔)
        self._scan_aliases: dict[str, str] = {}  # alias request_id -> primary request_id
        self._consecutive_failures = 0
        self._restore_from_sheet()

    def _restore_from_sheet(self) -> None:
        for record in self._excel.list_operations():
            request_id = record.get("request_id", "")
            if not request_id:
                continue
            record["result"] = self._decode_result(record.get("result_json", ""))
            self._records[request_id] = record
            state = record.get("state", "")
            if state in ("accepted", "in_progress"):
                # 서버 크래시 잔류분 — 자동 재실행 대신 확인필요로 전이한다.
                record["state"] = "needs_reconciliation"
                record["result"] = {"code": "INTERRUPTED", "message": "서버 재시작으로 중단됨"}
                self._excel.update_operation(
                    request_id, {"state": "needs_reconciliation",
                                 "result_json": json.dumps(record["result"], ensure_ascii=False)}
                )
            if record["state"] not in TERMINAL_STATES:
                order_id = record.get("order_id", "")
                if order_id:
                    self._order_locks[order_id] = request_id

    @staticmethod
    def _decode_result(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw) if raw else {}
        except ValueError:
            value = {}
        return value if isinstance(value, dict) else {}

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._records.get(request_id)

    def has_unresolved_order(self, order_id: str) -> str | None:
        """주문에 확인필요/잠금이 남아있으면 request_id를 반환한다."""
        with self._lock:
            if order_id in self._order_locks:
                return self._order_locks[order_id]
            for record in self._records.values():
                if record.get("order_id") == order_id and record.get("state") == "needs_reconciliation":
                    return record.get("request_id")
            return None

    def register(self, request_id: str, order_id: str, action: str, device_id: str) -> None:
        """새 작업을 접수 상태로 등록하고 주문 락을 잡는다."""
        now = self._now()
        record = {
            "request_id": request_id,
            "order_id": order_id,
            "action": action,
            "device_id": device_id,
            "state": "accepted",
            "result": {},
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._records[request_id] = record
            if order_id:
                self._order_locks[order_id] = request_id
        self._persist_new(record)

    def register_scan(self, request_id: str, qr_url: str, device_id: str) -> str | None:
        """폰 스캔을 접수한다. 동일 qr_url의 비종결 요청이 있으면 귀속(alias)시키고 그 request_id를 반환한다."""
        with self._lock:
            primary = self._inflight_scans.get(qr_url)
            if primary is not None:
                self._scan_aliases[request_id] = primary
            else:
                self._inflight_scans[qr_url] = request_id
        self.register(request_id, "", ACTION_SCAN_RECEIPT, device_id)
        with self._lock:
            record = self._records[request_id]
            record["qr_url"] = qr_url
            if primary is not None:
                record["alias_of"] = primary
        return primary

    def _persist_new(self, record: dict[str, Any]) -> None:
        self._excel.append_operation(
            {
                "request_id": record["request_id"],
                "order_id": record["order_id"],
                "action": record["action"],
                "device_id": record["device_id"],
                "device_name": self._device_name_resolver(record["device_id"]),
                "state": record["state"],
                "result_json": json.dumps(record["result"], ensure_ascii=False),
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
            }
        )

    def transition(self, request_id: str, state: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """상태 전이를 기록하고 종결 시 주문 락을 해제한다."""
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return None
            record["state"] = state
            if result is not None:
                record["result"] = result
                # 폰 스캔은 접수 시 주문번호를 모르므로 종결 결과에서 역기입한다.
                if not record.get("order_id") and result.get("order_id"):
                    record["order_id"] = str(result["order_id"])
            record["updated_at"] = self._now()
            if state in TERMINAL_STATES:
                order_id = record.get("order_id", "")
                if self._order_locks.get(order_id) == request_id:
                    del self._order_locks[order_id]
                if state == "failed":
                    self._consecutive_failures += 1
                elif state == "succeeded":
                    self._consecutive_failures = 0
        updates = {"state": state, "result_json": json.dumps(record["result"], ensure_ascii=False)}
        if record.get("order_id"):
            updates["order_id"] = record["order_id"]
        # 처리 시점 기기 이름 스냅샷 (이후 이름 변경과 무관하게 감사 추적용)
        name = self._device_name_resolver(record.get("device_id", ""))
        if name:
            updates["device_name"] = name
        self._excel.update_operation(request_id, updates)
        if state in TERMINAL_STATES:
            with self._lock:
                qr_url = record.get("qr_url", "")
                if qr_url and self._inflight_scans.get(qr_url) == request_id:
                    del self._inflight_scans[qr_url]
                # 동일 QR 귀속 요청들은 같은 결과를 받는다 (자동 재실행 없이 결과 공유).
                alias_rids = [rid for rid, p in self._scan_aliases.items() if p == request_id]
            for rid in alias_rids:
                self.transition(rid, state, result)
        return record

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def in_progress_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records.values() if r.get("state") in ("accepted", "in_progress"))

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------
# FastAPI 앱
# ----------------------------------------------------------------------


def _error(state: str, code: str, message: str, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        {"state": state, "error": {"code": code, "message": message}},
        status_code=status_code,
    )


def create_api_v1_app(
    excel: ExcelService,
    pairing: PairingService,
    *,
    auth_status_provider: Callable[[], bool] | None = None,
    scan_handler: Callable[[str], dict[str, str]] | None = None,
) -> FastAPI:
    tracker = DatasetTracker(excel)

    def _resolve_device_name(device_id: str) -> str:
        info = pairing.record_for_device_id(device_id) if pairing else None
        return (info.custom_name or info.reported_name) if info else ""

    registry = ActionRegistry(excel, device_name_resolver=_resolve_device_name)
    app = FastAPI(title="Ticket_AUTO LAN API", version="v1", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.paused = False
    scan_dispatch_lock = threading.Lock()
    scan_slots = threading.BoundedSemaphore(SCAN_IN_FLIGHT_MAX)

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
            return _error("rejected", "INVALID_REQUEST", "요청 본문이 너무 큽니다.", status_code=413)
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                return _error("rejected", "INVALID_REQUEST", "요청 본문이 너무 큽니다.", status_code=413)
            chunks.append(chunk)
        request._body = b"".join(chunks)
        return await call_next(request)

    def require_device(request: Request) -> str:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        device_id = pairing.device_id_for_token(token) if token else None
        if device_id is None:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "기기 인증이 필요합니다."},
            )
        # 인증 성공 = 기기 활동 신호 (스캔·조회·하트비트 모두 포함)
        pairing.record_activity(device_id)
        return device_id

    def owned_action(request_id: str, device_id: str) -> dict[str, Any] | None:
        record = registry.get(request_id)
        return record if record is not None and record.get("device_id") == device_id else None

    # ------------------------------ pair ------------------------------

    @app.post("/v1/pair")
    def pair(body: PairRequestBody):
        result = pairing.request_pair(
            join_code=body.join_code,
            pair_ticket=body.pair_ticket,
            device_name=body.device_name,
            device_uid=body.device_uid,
        )
        dataset_generation, _ = tracker.current()
        if result.state == "approved":
            return {
                "state": "approved",
                "device_token": result.device_token,
                "dataset_generation": dataset_generation,
            }
        if result.state == "pending_approval":
            return {"state": "pending_approval", "pair_ticket": result.pair_ticket}
        if result.state == "rejected":
            return {"state": "rejected"}
        return _error("rejected", result.error_code or "EXPIRED_JOIN_CODE", "참가 코드가 만료되었거나 사용되었습니다.")

    # ------------------------------ status ------------------------------

    @app.get("/v1/status")
    def status(request: Request, device: str = Depends(require_device)):
        # 폰이 직전 왕복에서 측정한 RTT를 헤더로 보고 — PC 모니터의 응답속도 지표
        raw_rtt = request.headers.get("x-client-rtt-ms", "")
        rtt = int(raw_rtt) if raw_rtt.isdigit() else None
        pairing.note_heartbeat(device, rtt_ms=rtt)
        dataset_generation, data_version = tracker.current()
        return {
            "state": "ok",
            "server_id": SERVER_ID,
            "dataset_generation": dataset_generation,
            "data_version": data_version,
            "paused": bool(app.state.paused),
            "witchform_authenticated": bool(auth_status_provider and auth_status_provider()),
            "in_progress": registry.in_progress_count(),
        }

    @app.post("/v1/disconnect")
    def disconnect(device: str = Depends(require_device)):
        """폰이 정상적으로 연결을 끊을 때의 명시 통지.

        require_device가 활동 시각을 먼저 갱신하므로 여기서 비우면
        하트비트 타임아웃(45초)을 기다리지 않고 즉시 끊김으로 표시된다.
        멱등 — 토큰은 유효한 채로 남고 다음 인증 활동이 오면 자동 복귀한다.
        """
        pairing.mark_disconnected(device)
        return {"ok": True}

    @app.get("/v1/devices")
    def list_paired_devices(device: str = Depends(require_device)):
        """호출한 기기 자신의 연결 상태·신호를 반환한다 (모바일 상태 표시용).

        타 기기 정보는 폰에 노출하지 않는다 — 전체 기기 모니터링은 PC 네트워크
        관리 탭이 담당한다. 인메모리 레지스트리만 읽으므로 폴링 가능.
        노출은 이름/상태/시각뿐 — device_uid·토큰 해시·주문 정보는 포함하지 않는다.
        """
        now = time.time()
        devices = pairing.list_devices()
        names = display_names(list(devices))
        items = []
        for info in devices:
            if device not in info.device_ids:
                continue
            seen = parse_seen_at(info.last_seen_at)
            presence = presence_state(info, now)
            quality_key, quality_label, quality_lit = connection_quality(
                info.last_rtt_ms,
                info.missed_beats or 0,
                presence,
            )
            items.append(
                {
                    "id": info.record_id,
                    "name": names.get(id(info)) or info.reported_name or "휴대폰",
                    "self": True,
                    "presence": presence,
                    # 차단된 기기만 신호 0 — offline도 45~120초 구간은 "불안정"으로 구분한다
                    "signal_level": 0 if presence == "revoked" else signal_level(info.last_seen_at, now),
                    # 연결 품질 — PC 네트워크 탭과 같은 판정 결과를 그대로 전달한다
                    "quality": {
                        "key": quality_key,
                        "label": quality_label,
                        "lit": quality_lit,
                    },
                    "last_seen_sec": max(0, int(now - seen)) if seen is not None else -1,
                }
            )
        return {"devices": items}

    # ------------------------------ orders ------------------------------

    def _mask_name(value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        if len(text) == 1:
            return "*"
        if len(text) == 2:
            return f"{text[0]}*"
        return f"{text[0]}*{text[-1]}"

    def _mask_phone(value: str) -> str:
        digits = "".join(ch for ch in (value or "") if ch.isdigit())
        if len(digits) == 11:
            return f"{digits[:3]}-****-{digits[-4:]}"
        if len(digits) == 10:
            return f"{digits[:3]}-***-{digits[-4:]}"
        return "****" if digits else ""

    def _order_payload(order) -> dict[str, Any]:
        # 계약 규칙 6: 이름/연락처는 마스킹해서보낸다.
        return {
            "order_number": order.order_number,
            "name": _mask_name(order.name),
            "phone": _mask_phone(order.phone),
            "seat": order.seat,
            "goods": order.goods,
            "received_at": order.received_at,
            "order_status": order.order_status,
            "received": order.is_received,
        }

    @app.get("/v1/orders/search")
    def search_orders(q: str = "", device: str = Depends(require_device)):
        q = (q or "").strip()[:64]
        orders = excel.search_orders_all(q)
        return {"state": "ok", "orders": [_order_payload(o) for o in orders]}

    @app.get("/v1/orders/{order_id}")
    def get_order(order_id: str, device: str = Depends(require_device)):
        order = excel.find_order(order_id)
        if order is None:
            return _error("rejected", "ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")
        _, data_version = tracker.current()
        return {"state": "ok", "data_version": data_version, "order": _order_payload(order)}

    @app.get("/v1/orders")
    def list_orders(since: str = "", device: str = Depends(require_device)):
        dataset_generation, data_version = tracker.current()
        if since and since == data_version:
            return {
                "state": "ok",
                "changed": False,
                "dataset_generation": dataset_generation,
                "data_version": data_version,
            }
        orders = excel.search_orders_all("")
        return {
            "state": "ok",
            "changed": True,
            "dataset_generation": dataset_generation,
            "data_version": data_version,
            "orders": [_order_payload(o) for o in orders],
        }

    # ------------------------------ work log ------------------------------

    def _ops_order_id(record: dict[str, str]) -> str:
        """작업 레코드의 주문번호 — 구형 기록은 result_json에만 남아 있다."""
        order_id = str(record.get("order_id") or "").strip()
        if order_id:
            return order_id.upper()
        try:
            result = json.loads(str(record.get("result_json") or "{}"))
        except (ValueError, TypeError):
            return ""
        return str(result.get("order_id") or "").strip().upper()

    @app.get("/v1/work-log")
    def work_log(
        device: str = Depends(require_device),
        limit: Annotated[int, Query(ge=1, le=WORK_LOG_MAX_LIMIT)] = WORK_LOG_DEFAULT_LIMIT,
        since: str = "",
    ):
        """티켓 업무 목록 — 수령완료/확인필요 주문을 최신순으로 반환한다.

        since=data_version이면 변경 없음으로 즉시 반환한다 (파일 시그니처만 확인).
        개인정보는 orders와 같은 규칙으로 마스킹한다.
        """
        generation, data_version = tracker.current()
        if since and since == data_version:
            return {
                "state": "ok",
                "changed": False,
                "dataset_generation": generation,
                "data_version": data_version,
            }

        ops_index: dict[str, dict[str, str]] = {}
        for record in excel.list_operations(limit=limit):
            order_id = _ops_order_id(record)
            if order_id:
                ops_index[order_id] = record

        items: list[dict[str, Any]] = []
        reconcile_count = 0
        for order in excel.search_orders_all(""):
            status_text = (order.order_status or "").strip()
            needs_reconcile = status_text == RECONCILE_MARKER
            if not order.is_received and not needs_reconcile:
                continue
            oid = (order.order_number or "").strip().upper()
            record = ops_index.get(oid)
            device_label = ""
            if record is not None:
                device_id = str(record.get("device_id") or "").strip()
                if device_id:
                    device_label = _resolve_device_name(device_id)
                if not device_label:
                    device_label = str(record.get("device_name") or "").strip() or "알 수 없는 기기"
            else:
                # _operations에 없는 수령 완료 = PC 본체에서 처리한 건
                device_label = "PC"
            if needs_reconcile:
                reconcile_count += 1
            items.append(
                {
                    "order_number": order.order_number,
                    "name": order.name,  # 페어링된 운영 단말용 — 이름은 원문 제공
                    "phone": _mask_phone(order.phone),
                    "seat": order.seat,
                    "goods": order.goods,
                    "status": RECONCILE_MARKER if needs_reconcile else "수령완료",
                    "processed_at": order.received_at
                    or (str(record.get("updated_at") or "") if record else ""),
                    "device_name": device_label,
                    "last_action_state": str(record.get("state") or "") if record else "",
                }
            )

        items.sort(key=lambda i: i["processed_at"], reverse=True)
        return {
            "state": "ok",
            "changed": True,
            "dataset_generation": generation,
            "data_version": data_version,
            "total": len(items),
            "needs_reconciliation": reconcile_count,
            "items": items[:limit],
        }

    # ------------------------------ actions ------------------------------

    def _action_payload(record: dict[str, Any]) -> dict[str, Any]:
        _, data_version = tracker.current()
        result = record.get("result", {})
        return {
            "state": record.get("state", ""),
            "request_id": record.get("request_id", ""),
            "order_id": record.get("order_id", "") or result.get("order_id", ""),
            "data_version": data_version,
            "result": result,
        }

    @app.post("/v1/scan")
    def scan_receipt(body: ScanRequestBody, device: str = Depends(require_device)):
        """폰의 원본 QR을 PC의 검증된 스캔 흐름으로 전달한다."""
        parsed = urlsplit(body.qr_url)
        if (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.lower()) != (
            "https", "witchform.com", "/qrcode_link.php"
        ):
            return _error("rejected", "INVALID_QR", "올바른 윗치폼 QR이 아닙니다.")
        if scan_handler is None:
            return _error("rejected", "RUNTIME_UNAVAILABLE", "PC 티켓 확인을 시작해주세요.")
        with scan_dispatch_lock:
            existing = registry.get(body.request_id)
            if existing is not None:
                return (
                    _action_payload(existing)
                    if existing.get("device_id") == device
                    else _error("rejected", "INVALID_REQUEST", "알 수 없는 요청입니다.", status_code=404)
                )
            if app.state.paused:
                return _error("rejected", "PAUSED", "처리가 일시정지 상태입니다. PC에서 재개해주세요.")
            if not scan_slots.acquire(blocking=False):
                return _error("rejected", "SERVER_BUSY", "처리 중인 스캔이 많습니다. 잠시 후 다시 시도해주세요.", status_code=429)
            try:
                primary_rid = registry.register_scan(body.request_id, body.qr_url, device)
            except Exception:
                scan_slots.release()
                raise
            if primary_rid is not None:
                # 동일 QR의 진행 중 요청에 귀속 — 별도 처리 없이 그 결과를 공유한다.
                scan_slots.release()
                return _action_payload(registry.get(body.request_id) or {})

        def run_scan() -> None:
            try:
                result = scan_handler(body.qr_url)
                state = result.get("state", "failed")
                if state not in TERMINAL_STATES:
                    raise ValueError("잘못된 스캔 처리 결과")
            except Exception:
                logger.exception("휴대폰 QR 처리 중 예외")
                state = "needs_reconciliation"
                result = {"message": "PC에서 처리 상태를 확인해주세요."}
            finally:
                scan_slots.release()
            registry.transition(body.request_id, state, result)

        threading.Thread(target=run_scan, daemon=True).start()
        return _action_payload(registry.get(body.request_id) or {})

    @app.post("/v1/actions")
    def create_action(body: ActionRequestBody, device: str = Depends(require_device)):
        dataset_generation, _ = tracker.current()
        if body.dataset_generation and body.dataset_generation != dataset_generation:
            return _error("rejected", "STALE_DATASET", "명단 데이터가 변경되었습니다. 다시 동기화해주세요.")

        existing = registry.get(body.request_id)
        if existing is not None:
            return (
                _action_payload(existing)
                if existing.get("device_id") == device
                else _error("rejected", "INVALID_REQUEST", "알 수 없는 요청입니다.", status_code=404)
            )

        if app.state.paused:
            return _error("rejected", "PAUSED", "처리가 일시정지 상태입니다. PC에서 재개해주세요.")

        if body.action != ACTION_RECEIPT:
            return _error("rejected", "INVALID_REQUEST", "지원하지 않는 작업입니다.")

        order = excel.find_order(body.order_id)
        if order is None:
            return _error("rejected", "ORDER_NOT_FOUND", "주문을 찾을 수 없습니다.")

        status_text = order.order_status or ""
        if any(marker in status_text for marker in CANCELLED_MARKERS):
            return _error("rejected", "ORDER_CANCELLED", "취소된 주문입니다.")
        if RECONCILE_MARKER in status_text:
            return _error("rejected", "NEEDS_RECONCILIATION", "확인이 필요한 주문입니다. PC에서 상태를 먼저 대조해주세요.")
        if order.is_received:
            return _error("already_processed", "ALREADY_PROCESSED", "이미 수령된 주문입니다.")

        blocking = registry.has_unresolved_order(body.order_id)
        if blocking and blocking != body.request_id:
            return _error("rejected", "DUPLICATE_IN_PROGRESS", "다른 기기가 이 주문을 처리 중입니다.")

        registry.register(body.request_id, body.order_id, body.action, device)
        return _action_payload(registry.get(body.request_id) or {})

    @app.get("/v1/actions/{request_id}")
    def get_action(request_id: str, device: str = Depends(require_device)):
        record = owned_action(request_id, device)
        if record is None:
            return _error("rejected", "INVALID_REQUEST", "알 수 없는 요청입니다.", status_code=404)
        return _action_payload(record)

    @app.post("/v1/actions/{request_id}/result")
    def report_action_result(request_id: str, body: ActionResultBody, device: str = Depends(require_device)):
        record = owned_action(request_id, device)
        if record is None:
            return _error("rejected", "INVALID_REQUEST", "알 수 없는 요청입니다.", status_code=404)
        if record.get("action") == ACTION_SCAN_RECEIPT:
            return _error("rejected", "INVALID_REQUEST", "PC가 스캔 결과를 기록합니다.")
        if record.get("state") in TERMINAL_STATES:
            return _action_payload(record)

        report_state = body.state
        if report_state not in ("succeeded", "failed", "needs_reconciliation"):
            return _error("rejected", "INVALID_REQUEST", "지원하지 않는 결과 상태입니다.")

        order_id = record.get("order_id", "")
        now = registry._now()
        detail = body.detail or ""

        if report_state == "succeeded":
            # 윗치폼 처리 확인을 폰이 보고했다 — PC가 XLSX 기록을 확정한다.
            if not excel.mark_order_received(order_id, now):
                registry.transition(
                    request_id,
                    "needs_reconciliation",
                    {"code": "WRITE_FAILED", "message": "수령 처리는 됐지만 XLSX 저장에 실패했습니다.", "detail": detail},
                )
                excel.mark_order_status(order_id, RECONCILE_MARKER)
                return _action_payload(registry.get(request_id) or {})
            registry.transition(request_id, "succeeded", {"received_at": now, "detail": detail})
        elif report_state == "failed":
            registry.transition(request_id, "failed", {"message": detail or "처리 실패"})
        else:
            registry.transition(request_id, "needs_reconciliation", {"message": detail or "결과 불명 — 대조 필요"})
            excel.mark_order_status(order_id, RECONCILE_MARKER)

        if registry.consecutive_failures >= AUTO_PAUSE_FAILURE_STREAK:
            app.state.paused = True
            logger.warning("연속 실패 %d건 — 처리를 자동 일시정지합니다.", registry.consecutive_failures)

        return _action_payload(registry.get(request_id) or {})

    return app


# ----------------------------------------------------------------------
# 실행기 (uvicorn + TLS)
# ----------------------------------------------------------------------


class LanApiServer:
    """uvicorn을 데몬 스레드로 실행하는 LAN API 서버."""

    def __init__(self, app: FastAPI, host: str, port: int, cert_path: str, key_path: str):
        import uvicorn

        # log_config=None — windowed PyInstaller(runw)는 sys.stderr가 None이라
        # 기본 dictConfig의 DefaultFormatter 생성이 실패해 서버가 시작되지 않는다.
        # None이면 dictConfig를 건너뛰고 uvicorn 로그가 root 로거(app.log)로 전파된다.
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            ssl_certfile=cert_path,
            ssl_keyfile=key_path,
            log_level="warning",
            access_log=False,
            log_config=None,
        )
        self._server = uvicorn.Server(config)
        self._thread: threading.Thread | None = None
        self.host = host
        self.port = port

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

    def wait_started(self, timeout: float = 5.0) -> bool:
        """바인드 완료까지 기다린다. 포트 점유·인증서 오류 등으로 스레드가 죽으면 False."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(self._server, "started", False):
                return True
            if self._thread is not None and not self._thread.is_alive():
                return False
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)


def build_pairing_qr_payload(
    addr: str,
    cert_sha256: str,
    join_code: str,
    dataset_generation: str,
    alt_addrs: list[str] | None = None,
) -> dict[str, object]:
    """연결 QR 페이로드 (계약 §2).

    `alt_addrs`는 기본 주소 실패 시 폰이 순서대로 시도할 후보 — 선택 필드라
    구형 앱(v:1)은 무시한다. addr 자체는 alt_addrs에 포함하지 않는다.
    """
    payload: dict[str, object] = {
        "v": 1,
        "addr": addr,
        "cert_sha256": cert_sha256,
        "join_code": join_code,
        "server_id": SERVER_ID,
        "dataset_generation": dataset_generation,
    }
    candidates = [a for a in (alt_addrs or []) if a and a != addr]
    if candidates:
        payload["alt_addrs"] = candidates
    return payload


def create_server(
    excel: ExcelService | None = None,
    *,
    host: str = "0.0.0.0",
    port: int = 8765,
    pairing: PairingService | None = None,
    auth_status_provider: Callable[[], bool] | None = None,
    scan_handler: Callable[[str], dict[str, str]] | None = None,
    cert_dir: str | None = None,
) -> tuple[LanApiServer, PairingService, str]:
    """서버+페어링 서비스+인증서 지문을 준비한다 (Flet 앱/단독 실행 공용)."""
    from services.cert_service import ensure_server_cert

    excel = excel or ExcelService()
    pairing = pairing or PairingService()
    cert = ensure_server_cert(cert_dir=cert_dir)
    app = create_api_v1_app(excel, pairing, auth_status_provider=auth_status_provider, scan_handler=scan_handler)
    server = LanApiServer(app, host, port, cert.cert_path, cert.key_path)
    return server, pairing, cert.sha256_fingerprint


if __name__ == "__main__":
    # 수동 검증용 단독 실행: python -m services.api_v1_server
    server, pairing, fingerprint = create_server()
    join_code = pairing.issue_join_code()
    payload = build_pairing_qr_payload(
        f"https://127.0.0.1:{server.port}", fingerprint, join_code, ""
    )
    print("PAIRING_PAYLOAD", json.dumps(payload, ensure_ascii=False))
    server.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
