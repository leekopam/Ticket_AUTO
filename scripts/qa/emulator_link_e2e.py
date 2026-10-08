"""에뮬레이터 E2E — 실기기 없이 페어링→스캔→결과 표시 전체 경로를 자동 검증한다.

실제 APK(릴리스) + 실제 TLS 서버(LanApiServer) + 실제 네트워크를 사용한다.
카메라 디코딩만 우회한다 — MainActivity의 `pairing_payload` 인텐트로 QR 페이로드를
주입하면 앱은 `handleScanned`의 동일 경로로 처리한다.

단일 기기 흐름:
  1) PC에 실 TLS 테스트 서버 기동 + 페어링 자동 승인 스레드
  2) `adb shell am start --es pairing_payload '<json>'`으로 연결 QR 페이로드 주입
  3) 서버측: 기기 등록 확인 / 앱측: '연결되었습니다' 표시 확인
  4) 티켓 QR URL 주입 → 서버측 핸들러 1회 실행 + 앱측 '수령 처리가 완료되었습니다' 확인
  5) 동일 QR 재주입 → already_processed 분기 확인

다기기(--serials 2대) 흐름:
  두 기기를 모두 페어링한 뒤 동일 티켓 QR을 거의 동시에 주입한다.
  서버의 요청 귀속(coalescing)으로 핸들러가 1회만 실행되고
  두 앱 모두 같은 종결 결과를 표시하는지 검증한다.

사용:
    python scripts/qa/emulator_link_e2e.py --apk <app-release.apk>
        [--serials emulator-5554] [--serials emulator-5554,emulator-5556]
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook  # noqa: E402

from services.api_v1_server import (  # noqa: E402
    LanApiServer,
    build_pairing_qr_payload,
    create_api_v1_app,
)
from services.cert_service import ensure_server_cert  # noqa: E402
from services.excel_service import ExcelService  # noqa: E402
from services.pairing_service import PairingService  # noqa: E402
from scripts.qa.apk_e2e import resolve_adb  # noqa: E402

PACKAGE = "com.leekopam.ticket_auto_android"
ACTIVITY = f"{PACKAGE}/.MainActivity"

TEST_ORDER_ID = "AAAA1111_BBBB2222"
TEST_QR_URL = "https://witchform.com/qrcode_link.php?opaque=emu-e2e"
# 에뮬레이터에서 호스트(PC) 루프백에 도달하는 고정 주소
EMU_HOST = "10.0.2.2"


def _adb(adb: str, serial: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [adb, "-s", serial, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _inject_qr(adb: str, serial: str, payload: str, force_stop: bool = True) -> None:
    """카메라 없이 QR 페이로드를 앱에 주입한다 (handleScanned와 동일 경로)."""
    # adb shell은 인자를 디바이스 셸에서 재파싱한다 — JSON의 큰따옴표가 깨지지
    # 않도록 페이로드를 작은따옴표로 감싼다 (페이로드에 ' 는 포함되지 않는다).
    args = ["shell", "am", "start"]
    if force_stop:
        # -S 재기동은 세션 복원과 경주한다 — 실행 중 앱에는 singleTop+onNewIntent로
        # 바로 전달되도록 첫 주입에만 사용한다.
        args.append("-S")
    args += ["-n", ACTIVITY, "--es", "pairing_payload", "'" + payload + "'"]
    result = _adb(adb, serial, *args)
    if result.returncode != 0:
        raise RuntimeError(f"인텐트 주입 실패: {result.stderr.strip()}")


def _ui_text_present(adb: str, serial: str, needle: str, timeout_sec: float) -> bool:
    """화면(UI dump)에 문구가 나타날 때까지 기다린다."""
    dump_path = f"/sdcard/emu_e2e_ui_{threading.get_ident()}.xml"
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        result = _adb(adb, serial, "shell", "uiautomator", "dump", dump_path)
        if result.returncode == 0:
            content = _adb(adb, serial, "shell", "cat", dump_path)
            if content.stdout and needle in content.stdout:
                _adb(adb, serial, "shell", "rm", dump_path)
                return True
        time.sleep(0.7)
    return False


class EmuE2EFailure(RuntimeError):
    pass


def _check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}" + (f" - {detail}" if detail else ""), flush=True)
    if not ok:
        raise EmuE2EFailure(label)


def _prepare_device(adb: str, serial: str, apk: str | None) -> None:
    """미페어링 상태 + 카메라 권한 부여 상태로 기기를 초기화한다."""
    if apk:
        result = _adb(adb, serial, "install", "-r", apk, timeout=120)
        _check(f"[{serial}] APK 설치", result.returncode == 0 and "Success" in result.stdout,
               result.stderr.strip() or result.stdout.strip()[-80:])
    # 이전 실행의 페어링 세션이 남아 있으면 연결 QR 주입이 무시된다 —
    # secure storage까지 비워 항상 미페어링 상태에서 시작한다.
    _adb(adb, serial, "shell", "pm", "clear", PACKAGE)
    # pm clear는 런타임 권한도 초기화한다 — 카메라 권한 다이얼로그가
    # 페어링 흐름을 막지 않게 미리 부여한다.
    _adb(adb, serial, "shell", "pm", "grant", PACKAGE, "android.permission.CAMERA")


def _pair_device(adb: str, serial: str, pairing, fingerprint: str, port: int) -> None:
    join_code = pairing.issue_join_code()
    payload = build_pairing_qr_payload(
        f"https://{EMU_HOST}:{port}", fingerprint, join_code, ""
    )
    _inject_qr(adb, serial, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _make_orders_xlsx(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "주문목록"
    sheet.append(["주문번호", "주문자명", "주문자연락처", "좌석번호", "주문상태", "[상품1]티켓"])
    sheet.append([TEST_ORDER_ID, "홍길동", "010-1234-5678", "A-1", "결제완료", 1])
    workbook.save(path)


def _start_test_server(scan_handler):
    """실 TLS 서버 + 자동 승인 스레드를 기동하고 (server, pairing, stop_fn)을 반환한다."""
    work = Path(tempfile.mkdtemp(prefix="emu_e2e_"))
    data_path = work / "orders.xlsx"
    _make_orders_xlsx(data_path)

    cert = ensure_server_cert(cert_dir=str(work / "cert"))
    pairing = PairingService(str(work / "devices.json"))
    excel = ExcelService(str(data_path))
    with socket.socket() as probe:
        probe.bind(("0.0.0.0", 0))
        port = probe.getsockname()[1]

    server = LanApiServer(
        create_api_v1_app(excel, pairing, scan_handler=scan_handler),
        "0.0.0.0", port, cert.cert_path, cert.key_path,
    )
    server.start()

    stop_approver = threading.Event()

    def auto_approve() -> None:
        # PC 승인 UI를 대신하는 자동 승인 — 앱의 pair_ticket 폴링을 그대로 통과시킨다.
        while not stop_approver.is_set():
            for pending in pairing.pending_approvals():
                pairing.approve(pending.pair_ticket)
            time.sleep(0.2)

    threading.Thread(target=auto_approve, daemon=True).start()
    print(f"테스트 서버 기동: https://{EMU_HOST}:{port} (자동 승인)", flush=True)

    def stop() -> None:
        stop_approver.set()
        server.stop()

    return server, pairing, cert, port, stop


def run(serials: list[str], apk: str | None, adb: str) -> None:
    scan_calls: list[str] = []
    call_index = {"n": 0}
    entered = threading.Event()
    gate = threading.Event()

    def handle_scan(qr_url: str) -> dict:
        scan_calls.append(qr_url)
        entered.set()
        # 다기기 동시 스캔 귀속을 검증할 수 있게 첫 처리를 잠시 붙잡아 둔다.
        gate.wait(timeout=10)
        call_index["n"] += 1
        if call_index["n"] == 1:
            return {"state": "succeeded", "order_id": TEST_ORDER_ID, "message": "수령 완료"}
        return {"state": "already_processed", "order_id": TEST_ORDER_ID,
                "message": "이미 수령된 주문입니다."}

    server, pairing, cert, port, stop = _start_test_server(handle_scan)
    try:
        # --- 1) 기기 준비 + 페어링 -----------------------------------------
        for serial in serials:
            _prepare_device(adb, serial, apk)
            _pair_device(adb, serial, pairing, cert.sha256_fingerprint, port)

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and len(pairing.list_devices()) < len(serials):
            time.sleep(0.5)
        devices = pairing.list_devices()
        _check("기기 페어링 등록(서버측)", len(devices) == len(serials),
               f"devices={len(devices)} / 기대={len(serials)}")
        for serial in serials:
            _check(f"[{serial}] 앱 화면 '연결되었습니다'",
                   _ui_text_present(adb, serial, "연결되었습니다", 20))

        if len(serials) == 1:
            _run_single(adb, serials[0], scan_calls, entered, gate)
        else:
            _run_multi(adb, serials, scan_calls, entered, gate)

        print("\n에뮬레이터 E2E: PASS", flush=True)
    finally:
        gate.set()
        stop()


def _wait_calls(scan_calls: list[str], count: int, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if len(scan_calls) >= count:
            return True
        time.sleep(0.3)
    return False


def _run_single(adb: str, serial: str, scan_calls: list[str], entered, gate) -> None:
    # --- 티켓 QR 주입 → 수령 처리 ------------------------------------------
    gate.set()  # 단일 기기는 대기 없이 즉시 처리
    _inject_qr(adb, serial, TEST_QR_URL, force_stop=False)
    _check("서버측 스캔 실행", _wait_calls(scan_calls, 1, 30), f"calls={scan_calls}")
    _check("앱 화면 '수령 처리가 완료'",
           _ui_text_present(adb, serial, "수령 처리가 완료", 20))

    # --- 동일 QR 재스캔 → already_processed --------------------------------
    # 성공 표시 직후 앱은 _refreshOrderQuietly 동안 _ticketScanActive로
    # 후속 스캔을 무시한다 — 실사용자 재스캔처럼 짧은 재시도로 흡수한다.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and len(scan_calls) < 2:
        _inject_qr(adb, serial, TEST_QR_URL, force_stop=False)
        for _ in range(10):
            if len(scan_calls) >= 2:
                break
            time.sleep(0.3)
    _check("재스캔 서버측 도달", len(scan_calls) == 2, f"calls={scan_calls}")
    _check("앱 화면 '이미 수령된 주문'",
           _ui_text_present(adb, serial, "이미 수령된 주문", 20))


def _run_multi(adb: str, serials: list[str], scan_calls: list[str], entered, gate) -> None:
    """두 기기의 동일 QR 동시 스캔이 서버에서 1회 실행으로 귀속되는지 검증한다."""
    first, second = serials[0], serials[1]
    # A가 핸들러에 진입해 처리 중일 때 B를 주입 — B는 A의 진행 중 요청에 귀속돼야 한다.
    _inject_qr(adb, first, TEST_QR_URL, force_stop=False)
    _check("A 스캔 서버 진입", entered.wait(timeout=30), f"calls={scan_calls}")
    _inject_qr(adb, second, TEST_QR_URL, force_stop=False)
    time.sleep(2)  # 귀속 요청이 서버에 등록될 시간
    gate.set()

    for serial in serials:
        _check(f"[{serial}] 앱 화면 '수령 처리가 완료'",
               _ui_text_present(adb, serial, "수령 처리가 완료", 30))
    _check("동일 QR 동시 스캔 핸들러 1회 실행(귀속)", scan_calls == [TEST_QR_URL],
           f"calls={scan_calls}")


def main() -> int:
    parser = argparse.ArgumentParser(description="에뮬레이터 페어링→스캔 E2E")
    parser.add_argument(
        "--serials",
        default="emulator-5554",
        help="에뮬레이터 시리얼 — 콤마로 2대까지 (예: emulator-5554,emulator-5556)",
    )
    parser.add_argument("--apk", default=None, help="설치할 APK (생략 시 이미 설치된 앱 사용)")
    parser.add_argument("--adb", default="", help="adb 경로")
    args = parser.parse_args()

    serials = [s.strip() for s in args.serials.split(",") if s.strip()]
    adb = resolve_adb(args.adb)
    for serial in serials:
        found = _adb(adb, serial, "get-state")
        if found.returncode != 0 or "device" not in found.stdout:
            print(f"에뮬레이터 {serial} 가 연결되어 있지 않습니다: {found.stderr.strip()}")
            return 2
    try:
        run(serials, args.apk, adb)
    except EmuE2EFailure:
        print("\n에뮬레이터 E2E: FAIL", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
