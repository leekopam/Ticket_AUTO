"""L2/L3 UI E2E용 런타임 스텁과 제어 서버.

실제 카메라/브라우저 없이 대시보드를 web 모드로 띄우기 위해
`TicketRuntimeManager`의 app_factory에 주입되는 Application 프로토콜 스텁과,
테스트 프로세스가 런타임 이벤트를 주입할 수 있게 하는 최소 HTTP 제어 서버를 제공한다.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any, Callable


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

    def set_camera_status_listener(self, listener: Callable[[str | None], None] | None) -> None:
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
    import time

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def run_control_server(
    app_getter: Callable[[], "FakeDashboardRuntimeApp | None"],
    port: int,
    printer: Any | None = None,
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
            if self.path != "/command":
                self._send_json(404, {"ok": False, "error": "unknown path"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception:
                self._send_json(400, {"ok": False, "error": "invalid json"})
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


def fetch_printer_jobs(control_url: str) -> list:
    """스텁 프린터에 접수된 출력 작업 목록을 반환한다."""
    import urllib.request

    with urllib.request.urlopen(f"{control_url}/printer-jobs", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8")).get("jobs", [])


def semantics_leaf(page, text: str):
    """주어진 텍스트를 포함하는 최하위 flt-semantics 노드를 선택한다.

    Flet은 Text.tooltip을 노드 텍스트에 병합하고 부모 노드가 자식 텍스트를
    합치므로, 상위 병합 노드와의 strict-mode 충돌을 피하기 위해 리프만 고른다.
    """
    return page.locator(
        f'flt-semantics:has-text("{text}"):not(:has(flt-semantics))'
    )


def wait_semantics_text(page, label: str, expected: str, timeout_ms: int = 10000) -> None:
    """라벨을 포함한 리프 semantics 노드 중 하나가 기대 문자열을 담을 때까지 폴링한다.

    같은 라벨을 포함하는 노드가 여럿일 수 있어 모든 후보를 검사한다.
    """
    import time

    deadline = time.monotonic() + timeout_ms / 1000.0
    last: list[str] = []
    while time.monotonic() < deadline:
        try:
            nodes = semantics_leaf(page, label).all()
            last = [node.inner_text(timeout=500) or "" for node in nodes]
        except Exception:
            last = []
        if any(expected in text for text in last):
            return
        time.sleep(0.2)
    raise AssertionError(
        f"[{label}] 텍스트 대기 시간 초과: expected={expected!r} last={last!r}"
    )


def wait_for_button(page, name: str, timeout_ms: int = 15000):
    """지정 이름의 버튼이 semantics 트리에 나타날 때까지 기다린다."""
    button = page.get_by_role("button", name=name, exact=True)
    button.wait_for(state="visible", timeout=timeout_ms)
    return button
