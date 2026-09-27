"""실기기 카메라 스캔 테스트용 QR 시트 생성기 (개발 전용).

- dev_lan_server.py가 출력한 pairing_payload.json + XLSX의 주문번호로 QR PNG와
  브라우저용 index.html을 만든다.
- 사용: python scripts/dev_qr_sheet.py --payload <pairing_payload.json>
        --data Resources/data/data.xlsx --out artifacts/test_qr [--count 6]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import qrcode

WITCHFORM_PREFIX = "https://witchform.com/qrcode_link.php?r="


def _order_numbers(xlsx_path: str) -> list[str]:
    from services.excel_service import ExcelService

    excel = ExcelService(xlsx_path)
    return [o.order_number for o in excel.search_orders_all("") if o.order_number]


def _qr_png(text: str, out: Path) -> None:
    img = qrcode.make(text)
    img.save(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", help="dev_lan_server가 만든 pairing_payload.json")
    parser.add_argument("--data", required=True, help="주문번호를 읽을 XLSX")
    parser.add_argument("--out", default="artifacts/test_qr")
    parser.add_argument("--count", type=int, default=6)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cards: list[tuple[str, str]] = []  # (label, filename)

    if args.payload:
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        compact = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        _qr_png(compact, out / "pairing.png")
        cards.append(("PC 연결 QR (페어링)", "pairing.png"))

    for i, order_no in enumerate(_order_numbers(args.data)[: args.count]):
        name = f"order_{i + 1}.png"
        _qr_png(WITCHFORM_PREFIX + order_no, out / name)
        cards.append((f"주문 {order_no}", name))

    items = "\n".join(
        f'<div class="card"><img src="{fn}"><div>{label}</div></div>' for label, fn in cards
    )
    (out / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Ticket_AUTO 테스트 QR</title>"
        "<style>body{font-family:sans-serif;background:#111;color:#eee;text-align:center}"
        ".card{display:inline-block;margin:16px;background:#fff;padding:12px;border-radius:8px}"
        ".card div{color:#000;font-size:14px;margin-top:8px}img{width:280px;height:280px}</style>"
        f"<h2>Ticket_AUTO 실기기 스캔 테스트</h2>{items}",
        encoding="utf-8",
    )
    print(f"QR 시트: {out / 'index.html'} ({len(cards)}개)")


if __name__ == "__main__":
    main()
