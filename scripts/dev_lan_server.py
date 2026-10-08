"""LAN API 개발·E2E 검증용 단독 서버.

- 임시 XLSX(테스트 주문 3건) 또는 --data로 지정한 XLSX 사본으로 서버를 띄운다.
- 승인 대기 티켓을 자동 승인한다(개발 전용 — 운영 경로에는 없는 동작).
- 사용: python scripts/dev_lan_server.py [port] [--data path.xlsx]
- PAIRING_PAYLOAD(JSON)를 출력하고 payloads.json도 함께 쓴다.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook

from services.api_v1_server import (
    DatasetTracker,
    build_pairing_qr_payload,
    create_server,
)
from services.cert_service import detect_lan_ips
from services.network_path_service import order_serving_ips


def _make_orders_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "주문목록"
    ws.append(["주문번호", "주문자명", "주문자연락처", "좌석번호", "주문상태", "[상품1]티켓"])
    ws.append(["AAAA1111_BBBB2222", "홍길동", "010-1234-5678", "A-1", "결제완료", 1])
    ws.append(["CCCC3333_DDDD4444", "김철수", "010-9999-8888", "A-2", "주문취소", 2])
    ws.append(["EEEE5555_FFFF6666", "이영희", "010-5555-4444", "A-3", "결제완료", 1])
    wb.save(path)


def _auto_approve(pairing, stop: threading.Event) -> None:
    while not stop.is_set():
        for pending in pairing.pending_approvals():
            pairing.approve(pending.pair_ticket)
            print(f"[dev] 승인됨: {pending.pair_ticket}", flush=True)
        time.sleep(0.5)


def main() -> None:
    from services.excel_service import ExcelService

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    data_arg = next(
        (sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--data" and i + 1 < len(sys.argv)),
        None,
    )
    tmp = Path(tempfile.mkdtemp(prefix="ticket_auto_dev_"))
    if data_arg:
        # 운영 파일을 직접 쓰지 않고 사본으로 서비스한다
        shutil.copy(data_arg, tmp / "data.xlsx")
        print(f"[dev] 데이터 사본 사용: {data_arg}", flush=True)
    else:
        _make_orders_xlsx(tmp / "data.xlsx")
    excel = ExcelService(str(tmp / "data.xlsx"))

    port = int(args[0]) if args else 18765
    # E2E 전용: 임시 XLSX에서만 수령을 흉내 낸다. 운영 서버에는 이 콜백을 전달하지 않는다.
    def simulate_receipt(qr_url: str) -> dict[str, str]:
        order_id = parse_qs(urlsplit(qr_url).query).get("r", [""])[0]
        order = excel.find_order(order_id)
        if order is None:
            return {"state": "rejected", "message": "테스트 주문을 찾을 수 없습니다."}
        if order.is_received:
            return {"state": "already_processed", "order_id": order_id, "message": "이미 수령된 주문입니다."}
        if "취소" in (order.order_status or ""):
            return {"state": "rejected", "order_id": order_id, "message": "취소된 주문입니다."}
        if not excel.mark_order_received(order_id, time.strftime("%Y-%m-%d %H:%M:%S")):
            return {"state": "needs_reconciliation", "order_id": order_id, "message": "테스트 기록 실패"}
        excel.mark_order_status(order_id, "거래종료")
        return {"state": "succeeded", "order_id": order_id, "message": "테스트 수령 완료"}

    scan_handler = simulate_receipt if "--e2e-simulate-receipt" in sys.argv else None
    server, pairing, fingerprint = create_server(excel=excel, port=port, scan_handler=scan_handler)
    join_code = pairing.issue_join_code()
    generation, _ = DatasetTracker(excel).current()

    ordered_ips = order_serving_ips(detect_lan_ips())
    lan_ip = ordered_ips[0]
    payload = build_pairing_qr_payload(
        f"https://{lan_ip}:{server.port}", fingerprint, join_code, generation,
        alt_addrs=[f"https://{ip}:{server.port}" for ip in ordered_ips[1:]],
    )

    stop = threading.Event()
    threading.Thread(target=_auto_approve, args=(pairing, stop), daemon=True).start()

    (tmp / "pairing_payload.json").write_text(json.dumps(payload, ensure_ascii=False))

    server.start()
    print(f"[dev] 서버: https://{lan_ip}:{server.port} (데이터: {tmp})", flush=True)
    print("PAIRING_PAYLOAD " + json.dumps(payload, ensure_ascii=False), flush=True)

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        stop.set()
        server.stop()


if __name__ == "__main__":
    main()
