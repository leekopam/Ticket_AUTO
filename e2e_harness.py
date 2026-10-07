"""E2E 하니스 — 소스 실행과 패키징 exe가 공유하는 테스트 기동 표면.

`main.py --e2e ...` 또는 `tests/e2e_ui/web_entry.py`로 기동한다.
실제 카메라/브라우저 없이 대시보드를 띄우기 위한 스텁 런타임, 스텁 프린터,
테스트 프로세스가 런타임 이벤트를 주입하는 최소 HTTP 제어 서버를 제공한다.

tests/e2e_ui/support.py와 tests/e2e/support.py는 이 모듈의 심볼을 재수출해
기존 테스트 import 경로를 유지한다.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import queue
import shutil
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from collections import Counter
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import project_paths


TEST_ORDER_NUMBER = "WFLM7QSDTC_69D53CU23685"
TEST_QR_URL = (
    "https://witchform.com/qrcode_link.php"
    f"?test_order={TEST_ORDER_NUMBER}"
)


# --- 실행 evidence(Boogle) 기록: BOOGLE_* 환경변수가 있을 때만 동작한다 ---


def _boogle_artifacts_dir() -> Path | None:
    raw = os.environ.get("BOOGLE_ARTIFACTS_DIR")
    return Path(raw) if raw else None


def _sanitize_artifact_name(name: str) -> str:
    """artifact 파일명을 Boogle 확장자/이름 규칙에 맞게 정규화한다."""
    import re

    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem) or "artifact"
    ext = Path(name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        ext = ".bin"
    return f"{stem}{ext}"


def save_artifact_bytes(name: str, data: bytes) -> Path | None:
    """BOOGLE_ARTIFACTS_DIR가 있으면 파일을 기록하고 경로를 반환한다."""
    target_dir = _boogle_artifacts_dir()
    if target_dir is None:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _sanitize_artifact_name(name)
    target.write_bytes(data)
    return target


def save_artifact_image(name: str, image: Any) -> Path | None:
    """PIL 이미지를 PNG artifact로 제출한다."""
    target_dir = _boogle_artifacts_dir()
    if target_dir is None:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _sanitize_artifact_name(name)
    image.save(target, format="PNG")
    return target


# 세션 전체에서 누적되는 계측 카운터.
_METRIC_COUNTERS: Counter[str] = Counter()


def record_metric(name: str, amount: int = 1) -> None:
    """E2E 계측 카운터를 누적한다."""
    _METRIC_COUNTERS[name] += amount


def metric_snapshot() -> dict[str, int]:
    """현재까지 누적된 계측 카운터 사본을 반환한다."""
    return dict(_METRIC_COUNTERS)


def reset_metrics() -> None:
    """계측 카운터를 초기화한다(세션 시작 시 1회)."""
    _METRIC_COUNTERS.clear()


def create_test_workbook(path: Path) -> None:
    """테스트 주문 1건이 들어간 최소 data 워크북을 생성한다."""
    from openpyxl import Workbook

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(
        [
            "주문번호",
            "주문자명",
            "주문자연락처",
            "좌석번호",
            "수령확인",
            "주문상태",
            "처리시간",
            "[상품1] 테스트 상품",
        ]
    )
    worksheet.append(
        [TEST_ORDER_NUMBER, "테스트 사용자", "010-0000-0000", "A-001", "", "거래중", "", 1]
    )
    workbook.save(path)
    workbook.close()


@dataclass(frozen=True)
class CapturedPrintJob:
    """프린터 대역에 기록된 인쇄 작업."""

    image: Any
    printer_name: str | None
    job_name: str


class FakePrinterBackend:
    """영수증 이미지를 메모리에 보관하고 선택적으로 실패를 재현한다."""

    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.jobs: list[CapturedPrintJob] = []

    def print_image(
        self,
        image: Any,
        printer_name: str | None,
        job_name: str,
    ) -> None:
        self.jobs.append(
            CapturedPrintJob(
                image=image.copy(),
                printer_name=printer_name,
                job_name=job_name,
            )
        )
        record_metric("print_jobs")
        save_artifact_image(f"receipt-{job_name}.png", image)
        if self.failure is not None:
            record_metric("print_failures")
            raise self.failure


class FakeDashboardRuntimeApp:
    """`main.Application` 프로토콜을 재현하는 대시보드용 스텁.

    `run()`은 제어 큐를 폴링하면서 명령을 디스패치하고,
    리스너를 통해 런타임 상태/주문/카메라 이벤트를 발생시킨다.
    """

    def __init__(self) -> None:
        self._status_listener: Callable[[str, str], None] | None = None
        self._order_listener: Callable[[Any], None] | None = None
        self._camera_frame_listener: Callable[[str], None] | None = None
        self._camera_status_listener: Callable[[str | None], None] | None = None
        self._commands: queue.Queue[dict] = queue.Queue()
        self._stop = threading.Event()
        self.calls: list[tuple[str, Any]] = []

    # --- Application 리스너 등록 ---
    def set_status_listener(self, listener: Callable[[str, str], None]) -> None:
        self._status_listener = listener

    def set_order_listener(self, listener: Callable[[Any], None] | None) -> None:
        self._order_listener = listener

    def set_camera_frame_listener(self, listener: Callable[[str], None]) -> None:
        self._camera_frame_listener = listener

    def set_camera_status_listener(self, listener: Callable[[str | None], None]) -> None:
        self._camera_status_listener = listener

    # --- 대시보드가 호출하는 동작 ---
    def change_camera(self, new_index: int) -> None:
        self.calls.append(("change_camera", new_index))

    def apply_scanner_focus_settings(
        self,
        focus_mode: str,
        manual_focus_value: float | None,
    ) -> str:
        self.calls.append(("apply_scanner_focus_settings", (focus_mode, manual_focus_value)))
        return "카메라 초점 설정 저장 완료"

    def get_scanner_focus_capability(self):
        return SimpleNamespace(manual_focus_supported=True)

    def open_scanner_camera_settings(self) -> bool:
        self.calls.append(("open_scanner_camera_settings", None))
        return True

    def open_witchform_login_page(self) -> bool:
        self.calls.append(("open_witchform_login_page", None))
        return True

    def request_relogin(self) -> None:
        self.calls.append(("request_relogin", None))
        self._emit_status("AUTH_WAIT", "재로그인 대기 중")
        self._emit_status("READY", "재로그인 완료")

    def request_stop(self) -> None:
        self.calls.append(("request_stop", None))
        self._stop.set()

    # --- 테스트 주입 명령 ---
    def push_command(self, command: dict) -> None:
        self._commands.put(command)

    def _emit_status(self, state: str, message: str) -> None:
        if self._status_listener is not None:
            self._status_listener(state, message)

    def _dispatch(self, command: dict) -> None:
        kind = command.get("cmd")
        if kind == "emit_status":
            self._emit_status(
                str(command.get("state", "READY")),
                str(command.get("message", "")),
            )
        elif kind == "emit_order" and self._order_listener is not None:
            from models.order_model import Order

            payload = dict(command.get("order") or {})
            self._order_listener(Order(**payload))
        elif kind == "emit_frame" and self._camera_frame_listener is not None:
            self._camera_frame_listener(str(command.get("png_b64", "")))
        elif kind == "emit_camera_status" and self._camera_status_listener is not None:
            label = command.get("label")
            self._camera_status_listener(None if label is None else str(label))
        elif kind == "relogin":
            self.request_relogin()

    def run(self) -> int:
        """런타임 루프: 중지 요청 전까지 제어 명령을 처리한다."""
        self._emit_status("STARTING", "스텁 런타임 시작")
        self._emit_status("READY", "스텁 런타임 준비 완료")
        while not self._stop.is_set():
            try:
                command = self._commands.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self._dispatch(command)
            except Exception:
                continue
        self._emit_status("STOPPED", "스텁 런타임 종료")
        return 0


def find_free_port() -> int:
    """빈 로컬 포트를 하나 반환한다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, timeout_sec: float = 15.0) -> bool:
    """127.0.0.1:port가 TCP 연결을 받을 때까지 대기한다."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _descendant_pids(root_pid: int) -> list[int]:
    """Toolhelp32 스냅샷으로 자식 프로세스(손자 포함) pid를 모은다.

    Flet 네이티브 창은 Python 프로세스가 띄운 자식 호스트 프로세스가 소유하므로
    '응답 없음' 재현은 자식 프로세스까지 정지시켜야 한다.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        procs: list[tuple[int, int]] = []
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            procs.append((entry.th32ProcessID, entry.th32ParentProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)

    result: list[int] = []
    frontier = {root_pid}
    while True:
        children = {pid for pid, ppid in procs if ppid in frontier} - frontier
        if not children:
            return result
        result.extend(children)
        frontier |= children


def _descendant_tids(pids: set[int]) -> set[int]:
    """주어진 pid 집합이 소유한 모든 스레드 id를 반환한다."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.DWORD),
            ("tpDeltaPri", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
    if not snap or snap == ctypes.c_void_p(-1).value:
        return set()
    try:
        entry = THREADENTRY32()
        entry.dwSize = ctypes.sizeof(entry)
        tids: set[int] = set()
        ok = kernel32.Thread32First(snap, ctypes.byref(entry))
        while ok:
            if entry.th32OwnerProcessID in pids:
                tids.add(entry.th32ThreadID)
            ok = kernel32.Thread32Next(snap, ctypes.byref(entry))
        return tids
    finally:
        kernel32.CloseHandle(snap)


def _hang_ui_thread(seconds: float) -> None:
    """E2E 전용: 창 소유 프로세스 트리를 seconds 동안 정지시켜 '응답 없음'을 재현한다."""
    if sys.platform != "win32":
        return
    import ctypes

    time.sleep(0.3)  # HTTP 응답이 먼저 나가게 한다
    kernel32 = ctypes.windll.kernel32
    own_pid = os.getpid()
    child_pids = set(_descendant_pids(own_pid))
    # Flet 데스크톱 창은 자식 호스트 프로세스가 소유한다 — 자식 스레드 전부 정지.
    tids = _descendant_tids(child_pids)
    # 같은 프로세스가 창을 소유한 경우(web 모드 창 없음 등) 대비해 메인 스레드도 정지.
    tids.add(threading.main_thread().native_id)
    tids.discard(threading.get_native_id())  # 이 핸들러 스레드 자신은 제외

    THREAD_SUSPEND_RESUME = 0x0002
    handles = []
    for tid in tids:
        handle = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, tid)
        if handle:
            handles.append(handle)
    try:
        for handle in handles:
            kernel32.SuspendThread(handle)
        time.sleep(max(0.5, seconds))
    finally:
        for handle in handles:
            kernel32.ResumeThread(handle)
            kernel32.CloseHandle(handle)


def run_control_server(
    app_getter: Callable[[], "FakeDashboardRuntimeApp | None"],
    port: int,
    printer: Any | None = None,
    phone_link: Any | None = None,
) -> ThreadingHTTPServer:
    """테스트 프로세스→앱 프로세스 명령 주입용 최소 HTTP 서버를 기동한다.

    런타임 재시작마다 새 스텁 앱이 생성되므로, 요청 시점마다
    `app_getter()`로 현재 인스턴스를 조회한다.

    - POST /command  body: {"cmd": "emit_status", ...} → 현재 앱 push_command
    - GET /ping      → {"ok": true}
    - GET /calls     → 최근 앱 calls 스냅샷(JSON 직렬화 가능한 값만)
    - GET /printer-jobs → 스텁 프린터에 접수된 작업 목록
    """

    class _Handler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib 핸들러 규약
            if self.path == "/ping":
                self._send_json(200, {"ok": True})
            elif self.path == "/calls":
                app = app_getter()
                self._send_json(200, {"calls": list(app.calls) if app else []})
            elif self.path == "/printer-jobs":
                jobs = getattr(printer, "jobs", []) if printer is not None else []
                self._send_json(
                    200,
                    {
                        "jobs": [
                            {"printer_name": j.printer_name, "job_name": j.job_name}
                            for j in jobs
                        ]
                    },
                )
            else:
                self._send_json(404, {"ok": False, "error": "unknown path"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib 핸들러 규약
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception:
                self._send_json(400, {"ok": False, "error": "invalid json"})
                return
            if self.path == "/hang":
                # watchdog 음성 경로 검증용 — UI 스레드를 일시 정지시킨다.
                seconds = float(payload.get("seconds", 5.0))
                threading.Thread(
                    target=_hang_ui_thread, args=(seconds,), daemon=True
                ).start()
                self._send_json(200, {"ok": True, "seconds": seconds})
                return
            if self.path != "/command":
                self._send_json(404, {"ok": False, "error": "unknown path"})
                return
            if str(payload.get("cmd", "")).startswith("phone_"):
                if phone_link is None:
                    self._send_json(503, {"ok": False, "error": "phone link not wired"})
                    return
                try:
                    result = _handle_phone_command(phone_link, payload)
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})
                    return
                self._send_json(200, {"ok": True, "result": result})
                return
            app = app_getter()
            if app is None:
                self._send_json(503, {"ok": False, "error": "runtime not started"})
                return
            app.push_command(payload)
            self._send_json(200, {"ok": True})

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib 규약
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server


# --- 스텁 폰: 제어 서버 안에서 실제 TLS 페어링/하트비트를 수행한다 ---


def _pinned_request(
    addr: str,
    fingerprint: str,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    token: str = "",
) -> tuple[int, dict]:
    """인증서 지문을 확인한 뒤 LAN API로 JSON 요청을 보낸다 (실제 폰과 동일 경로)."""
    import hashlib
    import http.client
    import ssl
    from urllib.parse import urlparse

    parsed = urlparse(addr)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port, context=context, timeout=5
    )
    try:
        connection.connect()
        certificate = connection.sock.getpeercert(binary_form=True)
        actual = hashlib.sha256(certificate).hexdigest().upper()
        if actual != fingerprint.replace(":", "").upper():
            raise ValueError("서버 인증서 지문 불일치")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(
            method, path,
            body=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def _handle_phone_command(phone_link, payload: dict) -> dict:
    """스텁 폰 명령을 실행한다. 앱 프로세스 안에서 실제 HTTPS 왕복을 만든다.

    - phone_link_start: LAN API 서버 기동 → {"addr", "fingerprint"}
    - phone_pair: join_code로 /v1/pair → (approve 시) 티켓 완료 → token 반환
    - phone_status: Bearer 토큰으로 /v1/status (하트비트 역할, last_seen 갱신)
    - phone_scan: /v1/scan → 종결까지 폴링해 결과 반환
    - phone_revoke_self: revoke는 PC UI가 하므로 없음
    """
    cmd = str(payload.get("cmd"))
    if cmd == "phone_link_start":
        link_payload = phone_link.start()
        return {
            "addr": link_payload["addr"],
            "fingerprint": link_payload["cert_sha256"],
        }

    if cmd == "phone_pair":
        state = _phone_state(phone_link)
        addr, fp = state["addr"], state["fingerprint"]
        join_code = phone_link.pairing.issue_join_code()
        _, pending = _pinned_request(
            addr, fp, "POST", "/v1/pair",
            body={
                "join_code": join_code,
                "device_name": str(payload.get("device_name", "stub-phone")),
                "device_uid": str(payload.get("device_uid", "")),
            },
        )
        ticket = str(pending.get("pair_ticket", ""))
        if not payload.get("approve", True):
            # 실제 폰처럼 승인 여부를 백그라운드에서 폴링한다.
            # UI의 승인/거절 버튼이 결과를 결정하도록 즉시 반환한다.
            _pair_waiter(phone_link, addr, fp, ticket)
            return {"token": "", "pair_ticket": ticket, "state": pending.get("state", "")}
        phone_link.approve(ticket)
        status, approved = _pinned_request(
            addr, fp, "POST", "/v1/pair", body={"pair_ticket": ticket}
        )
        if status != 200:
            raise RuntimeError(f"pair 승인 완료 실패: {approved}")
        return {
            "token": approved.get("device_token", ""),
            "state": approved.get("state", ""),
        }

    if cmd == "phone_link_stop":
        phone_link.stop()
        return {"stopped": True}

    if cmd == "phone_status":
        state = _phone_state(phone_link)
        status, body = _pinned_request(
            state["addr"], state["fingerprint"], "GET", "/v1/status",
            token=str(payload.get("token", "")),
        )
        return {"http": status, "body": body}

    if cmd == "phone_scan":
        state = _phone_state(phone_link)
        token = str(payload.get("token", ""))
        request_id = str(payload.get("request_id", "e2e-scan-1"))
        qr_url = str(payload.get("qr_url", ""))
        _pinned_request(
            state["addr"], state["fingerprint"], "POST", "/v1/scan",
            body={"request_id": request_id, "qr_url": qr_url}, token=token,
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            status, action = _pinned_request(
                state["addr"], state["fingerprint"], "GET",
                f"/v1/actions/{request_id}", token=token,
            )
            if status == 200 and action.get("state") not in ("accepted", "in_progress"):
                return action
            time.sleep(0.3)
        raise RuntimeError("phone_scan 결과 대기 시간 초과")

    raise ValueError(f"알 수 없는 폰 명령: {cmd}")


def _pair_waiter(phone_link, addr: str, fingerprint: str, pair_ticket: str) -> None:
    """승인 대기 중 티켓 교환을 백그라운드에서 마무리한다 (폰의 승인 폴링과 동일)."""

    def _wait() -> None:
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            time.sleep(1.0)
            try:
                _, body = _pinned_request(
                    addr, fingerprint, "POST", "/v1/pair",
                    body={"pair_ticket": pair_ticket},
                )
            except Exception:
                continue
            if body.get("state") != "pending_approval":
                return

    threading.Thread(target=_wait, daemon=True).start()


def _phone_state(phone_link) -> dict:
    """기동 중인 LAN 서버의 addr/fingerprint를 꺼낸다. 꺼져 있으면 기동한다."""
    link_payload = phone_link.payload or phone_link.start()
    return {
        "addr": link_payload["addr"],
        "fingerprint": link_payload["cert_sha256"],
    }


# --- 테스트 프로세스 측 제어/탐색 헬퍼 ---


def send_control_command(control_url: str, payload: dict) -> dict:
    """제어 서버에 런타임 이벤트 명령을 보낸다."""
    import urllib.request

    req = urllib.request.Request(
        f"{control_url}/command",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_runtime_calls(control_url: str) -> list:
    """스텁 런타임 앱이 받은 호출 목록을 반환한다."""
    import urllib.request

    with urllib.request.urlopen(f"{control_url}/calls", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8")).get("calls", [])


def request_hang(control_url: str, seconds: float = 8.0) -> dict:
    """제어 서버에 UI 스레드 정지를 요청한다 ('응답 없음' 재현용)."""
    import urllib.request

    req = urllib.request.Request(
        f"{control_url}/hang",
        data=json.dumps({"seconds": seconds}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_printer_jobs(control_url: str) -> list:
    """스텁 프린터에 접수된 출력 작업 목록을 반환한다."""
    import urllib.request

    with urllib.request.urlopen(f"{control_url}/printer-jobs", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8")).get("jobs", [])


# --- 앱 프로세스 기동 엔트리 (main.py --e2e / web_entry.py 공용) ---


def _demo_console(control_url: str) -> None:
    """데모 콘솔: stdin 명령을 제어 서버로 전달해 런타임 이벤트를 발생시킨다."""
    import base64

    from services.qr_generator_service import generate_qr_image

    order = {
        "order_number": TEST_ORDER_NUMBER,
        "name": "테스트 사용자",
        "phone": "010-0000-0000",
        "seat": "A-001",
        "goods": ["테스트 상품"],
        "order_status": "결제완료",
    }

    def _qr_frame() -> dict:
        buf = io.BytesIO()
        generate_qr_image(TEST_QR_URL, output_px=480).save(buf, format="PNG")
        return {"cmd": "emit_frame", "png_b64": base64.b64encode(buf.getvalue()).decode()}

    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd == "order":
            payloads = [{"cmd": "emit_order", "order": order}]
        elif cmd == "frame":
            payloads = [
                {"cmd": "emit_camera_status", "label": "데모 가상 카메라"},
                _qr_frame(),
            ]
        elif cmd == "relogin":
            payloads = [{"cmd": "relogin"}]
        else:
            print("[demo] 명령: order(주문) / frame(QR 프레임) / relogin(재로그인)")
            continue
        for payload in payloads:
            try:
                send_control_command(control_url, payload)
                print(f"[demo] {cmd} 이벤트 주입 완료")
            except Exception as exc:
                print(f"[demo] {cmd} 실패: {exc} — 대시보드에서 '티켓 확인 시작' 후 재시도")


def run_e2e_app(
    *,
    port: int,
    control_port: int,
    runtime_dir: Path,
    data_file: str,
    native: bool,
    title: str,
    demo: bool,
) -> int:
    """하니스 대시보드를 기동한다. project_paths 쓰기 경로를 runtime_dir로 격리한다."""
    runtime_dir.mkdir(parents=True, exist_ok=True)

    # 쓰기 경로(.runtime/, Resources/data/)를 테스트 폴더로 격리한다.
    project_paths.PROJECT_ROOT = runtime_dir

    # 테스트 data 파일을 격리된 managed data 경로에 배치한다.
    if data_file:
        data_target = runtime_dir / "Resources" / "data" / "data.xlsx"
        data_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(data_file, data_target)

    from services.ticket_runtime_manager import TicketRuntimeManager
    from views.dashboard_flet_view import DashboardFletView

    # 프린터 IO 경계를 스텁으로 교체해 실제 스풀러 출력을 막고 작업을 기록한다.
    import services.receipt_print_pipeline as receipt_pipeline
    import views.settings_flet_view as settings_view

    fake_printer = FakePrinterBackend()
    receipt_pipeline.WindowsPrinterService = lambda: fake_printer  # type: ignore[assignment]
    settings_view.WindowsPrinterService = lambda: fake_printer  # type: ignore[assignment]

    # 런타임 재시작마다 새 스텁 앱을 만들고, 제어 서버는 최신 인스턴스로 라우팅한다.
    current_app: dict[str, FakeDashboardRuntimeApp | None] = {"value": None}

    def _app_factory() -> FakeDashboardRuntimeApp:
        current_app["value"] = FakeDashboardRuntimeApp()
        return current_app["value"]

    runtime_manager = TicketRuntimeManager(app_factory=_app_factory)

    # 네트워크 관리 E2E: 실제 LAN API 서버를 테스트 전용 포트/저장소로 띄운다.
    # 제어 서버의 phone_* 명령이 이 서비스에 스텁 폰 역할로 연결한다.
    from services.phone_link_service import PhoneLinkService

    phone_link = PhoneLinkService(
        port=find_free_port(),
        scan_handler=runtime_manager.process_phone_qr,
        token_store_path=str(runtime_dir / ".runtime" / "api_devices.json"),
        cert_dir=str(runtime_dir / ".runtime" / "api_cert"),
    )
    run_control_server(
        lambda: current_app["value"], control_port, printer=fake_printer,
        phone_link=phone_link,
    )

    if demo:
        print(f"[demo] 대시보드: http://127.0.0.1:{port} (브라우저 자동 열림)", flush=True)
        print("[demo] 콘솔 명령: order(주문) / frame(QR 프레임) / relogin(재로그인)", flush=True)
        print("[demo] 종료: 이 창을 닫거나 Ctrl+C", flush=True)
        control_url = f"http://127.0.0.1:{control_port}"
        threading.Thread(
            target=_demo_console, args=(control_url,), daemon=True
        ).start()

    view = DashboardFletView(
        runtime_manager=runtime_manager,
        window_title=title or None,
        phone_link_service=phone_link,
    )
    if native:
        view.run()
    else:
        view.run(web_port=port)
    return 0


def main(argv: list[str]) -> int:
    """E2E 기동 엔트리. sys.argv[1:]를 받으며 '--e2e'는 무시한다."""
    parser = argparse.ArgumentParser(description="대시보드 E2E 기동 엔트리")
    parser.add_argument("--e2e", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--control-port", type=int, default=0)
    parser.add_argument("--runtime-dir", type=str, default="")
    parser.add_argument("--data-file", type=str, default="")
    parser.add_argument(
        "--native",
        action="store_true",
        help="web 서버 대신 Windows 네이티브 창으로 기동한다(UIA E2E용)",
    )
    parser.add_argument(
        "--title", type=str, default="", help="네이티브 창 제목 오버라이드"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="수동 조작 모드: 브라우저 자동 열기 + 콘솔 명령으로 이벤트 주입",
    )
    args = parser.parse_args(argv)

    if args.demo:
        port = args.port or find_free_port()
        control_port = args.control_port or find_free_port()
        runtime_dir = Path(
            args.runtime_dir or tempfile.mkdtemp(prefix="ticket_auto_demo_")
        ).resolve()
        data_file = args.data_file
        if not data_file:
            seed_file = runtime_dir / "seed" / "data.xlsx"
            seed_file.parent.mkdir(parents=True, exist_ok=True)
            create_test_workbook(seed_file)
            data_file = str(seed_file)
    else:
        # 테스트 기동 시에는 브라우저 창이 열리지 않게 한다.
        webbrowser.open = lambda *a, **k: True  # type: ignore[assignment]
        port = args.port
        control_port = args.control_port
        runtime_dir = Path(args.runtime_dir).resolve()
        data_file = args.data_file

    return run_e2e_app(
        port=port,
        control_port=control_port,
        runtime_dir=runtime_dir,
        data_file=data_file,
        native=args.native,
        title=args.title,
        demo=args.demo,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
