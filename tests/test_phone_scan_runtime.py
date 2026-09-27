"""휴대폰 QR은 PC 스캔 루프에서만 성공으로 확정한다."""
from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

from main import AppState, Application


class _Scanner:
    running = True

    def is_running(self) -> bool:
        return self.running

    def get_next_qr(self, timeout_sec: float = 0.1) -> None:
        time.sleep(0.01)
        return None

    def set_scanning_enabled(self, value: bool) -> None:
        pass

    def set_status_message(self, message: str) -> None:
        pass

    def set_auth_ready(self, value: bool) -> None:
        pass


def test_phone_qr_uses_pc_loop_and_excel_result():
    app = Application.__new__(Application)
    app._state = AppState.READY
    app._stop_requested = False
    app._relogin_requested = False
    app._control_lock = threading.Lock()
    app._phone_scans = queue.Queue()
    app._active_phone_scan = None
    app._last_scan_order = None
    app._phone_was_received = False
    app._phone_web_already_received = False
    app._last_status_message = ""
    app._status_listener = None
    app._order_listener = None
    app._scanner_view = _Scanner()
    app._excel_service = SimpleNamespace(find_order=lambda number: SimpleNamespace(is_received=True))
    app._load_ticket_debug_settings = lambda: SimpleNamespace(offline_scan_mode=False)

    def process(qr_url: str, allow_auth_retry: bool) -> None:
        assert qr_url == "https://witchform.com/qrcode_link.php?ticket=1"
        app._emit_order(SimpleNamespace(order_number="AAAA_BBBB", is_received=False))
        app._enter_ready("수령 완료")
        app._scanner_view.running = False

    app._process_qr = process
    worker = threading.Thread(target=app._main_loop)
    worker.start()
    result = app.process_phone_qr("https://witchform.com/qrcode_link.php?ticket=1")
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert result == {"state": "succeeded", "order_id": "AAAA_BBBB", "message": "수령 완료"}
