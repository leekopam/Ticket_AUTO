"""LAN API 개발·E2E 검증용 단독 서버.

- 임시 XLSX(테스트 주문 3건)로 서버를 띄우고 페어링 QR 페이로드를 출력한다.
- 승인 대기 티켓을 자동 승인한다(개발 전용 — 운영 경로에는 없는 동작).
- 사용: python scripts/dev_lan_server.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook

from services.api_v1_server import (
    DatasetTracker,
    build_pairing_qr_payload,
    create_server,
)
from services.cert_service import detect_lan_ips


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

    tmp = Path(tempfile.mkdtemp(prefix="ticket_auto_dev_"))
    _make_orders_xlsx(tmp / "data.xlsx")
    excel = ExcelService(str(tmp / "data.xlsx"))

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
    server, pairing, fingerprint = create_server(excel=excel, port=port)
    join_code = pairing.issue_join_code()
    generation, _ = DatasetTracker(excel).current()

    lan_ip = detect_lan_ips()[0]
    payload = build_pairing_qr_payload(
        f"https://{lan_ip}:{server.port}", fingerprint, join_code, generation
    )

    stop = threading.Event()
    threading.Thread(target=_auto_approve, args=(pairing, stop), daemon=True).start()

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
