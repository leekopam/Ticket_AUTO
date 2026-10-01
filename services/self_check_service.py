"""VSCode(python) 실행과 PyInstaller exe 실행의 기능 동치를 검증하는 셀프 체크.

`--self-check`로 기동되며 GUI를 띄우지 않는다. 각 검사는 실제 서비스 경계를
한 번씩 통과해 모듈 누락·DLL 부재·경로 차이로 기능이 죽는 패키징 회귀를 잡는다.
외부 부작용(네트워크·물리 인쇄·로그인)은 없다.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

logger = logging.getLogger(__name__)

_SELF_CHECK_TOKEN = "selfcheck-7f2c1a"


@dataclass(frozen=True)
class SelfCheckResult:
    name: str
    ok: bool
    detail: str


def _check_imports() -> SelfCheckResult:
    import cv2  # noqa: F401
    import flet  # noqa: F401
    import numpy  # noqa: F401
    import openpyxl  # noqa: F401
    import qrcode  # noqa: F401
    from PIL import Image  # noqa: F401
    from pyzbar.pyzbar import decode  # noqa: F401

    win_deps = []
    try:
        import win32print  # noqa: F401

        win_deps.append("win32print")
    except ImportError:
        pass
    try:
        import playwright  # noqa: F401

        win_deps.append("playwright")
    except ImportError:
        pass
    return SelfCheckResult("imports", True, f"windows-optional={','.join(win_deps) or 'none'}")


def _check_project_paths() -> SelfCheckResult:
    from project_paths import ensure_managed_data_file

    path = ensure_managed_data_file()
    writable = path.parent.is_dir()
    return SelfCheckResult(
        "project_paths",
        path.is_file() and writable,
        f"data_file={path} frozen={getattr(sys, 'frozen', False)}",
    )


def _check_qr_roundtrip() -> SelfCheckResult:
    import numpy as np

    from services.qr_generator_service import generate_qr_image
    from views.scanner_view import ScannerView

    frame = np.asarray(
        generate_qr_image(_SELF_CHECK_TOKEN, output_px=300).convert("RGB")
    )
    decoded = ScannerView._decode_qr(frame)
    return SelfCheckResult(
        "qr_roundtrip",
        decoded == _SELF_CHECK_TOKEN,
        f"decoded={decoded!r}",
    )


def _check_camera_enum() -> SelfCheckResult:
    from services.windows_camera_service import WindowsCameraService

    cameras = WindowsCameraService().list_cameras()
    return SelfCheckResult("camera_enum", isinstance(cameras, list), f"count={len(cameras)}")


def _check_excel_load() -> SelfCheckResult:
    from services.excel_service import ExcelService

    orders = ExcelService().search_orders("자동차")
    return SelfCheckResult("excel_load", isinstance(orders, list), f"orders={len(orders)}")


def _check_printer_enum() -> SelfCheckResult:
    from services.windows_printer_service import WindowsPrinterService

    printers = WindowsPrinterService().list_printers()
    return SelfCheckResult(
        "printer_enum", isinstance(printers, list), f"count={len(printers)}"
    )


def _check_audio_init() -> SelfCheckResult:
    from services.windows_audio_service import WindowsAudioService

    service = WindowsAudioService()
    ok = getattr(service, "_winmm", None) is not None
    return SelfCheckResult("audio_init", ok, "winmm loaded" if ok else "winmm unavailable")


def _check_playwright_runtime() -> SelfCheckResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return SelfCheckResult("playwright_runtime", False, "playwright import failed")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            title = "selfcheck"
            page.set_content(f"<title>{title}</title>")
            ok = page.title() == title
            browser.close()
        return SelfCheckResult("playwright_runtime", ok, "chromium launch")
    except Exception as exc:
        return SelfCheckResult(
            "playwright_runtime", False, f"{type(exc).__name__}: {exc}"
        )


def _check_lan_server() -> SelfCheckResult:
    """LAN API 서버가 실제로 바인드·기동되는지 검증한다.

    windowed exe(sys.stderr=None)에서 uvicorn 기본 log_config가
    크래시했던 회귀를 잡기 위한 항목 — 생성+바인드까지만 확인하고 바로 내린다.
    """
    import socket

    from services.api_v1_server import create_server

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = None
    try:
        server, _pairing, _fp = create_server(port=port)
        server.start()
        ok = server.wait_started(timeout=5.0)
        return SelfCheckResult("lan_server", ok, f"port={port} started={ok}")
    finally:
        if server is not None:
            server.stop()


_SELF_CHECKS: tuple[tuple[str, Callable[[], SelfCheckResult]], ...] = (
    ("imports", _check_imports),
    ("project_paths", _check_project_paths),
    ("qr_roundtrip", _check_qr_roundtrip),
    ("camera_enum", _check_camera_enum),
    ("excel_load", _check_excel_load),
    ("printer_enum", _check_printer_enum),
    ("audio_init", _check_audio_init),
    ("playwright_runtime", _check_playwright_runtime),
    ("lan_server", _check_lan_server),
)


def run_self_checks(
    checks: Sequence[tuple[str, Callable[[], SelfCheckResult]]] | None = None,
) -> list[SelfCheckResult]:
    """각 검사를 독립 실행해 한 항목 실패가 다음 항목을 막지 않게 한다."""
    results: list[SelfCheckResult] = []
    for name, check in checks if checks is not None else _SELF_CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 — 경계 예외를 리포트로 변환
            logger.debug("self-check %s failed", name, exc_info=True)
            results.append(SelfCheckResult(name, False, f"{type(exc).__name__}: {exc}"))
    return results


def build_report(results: Sequence[SelfCheckResult]) -> dict:
    failed = [r.name for r in results if not r.ok]
    return {
        "env": "frozen" if getattr(sys, "frozen", False) else "python",
        "ok": not failed,
        "failed": failed,
        "results": [asdict(r) for r in results],
    }


def write_report(report: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out_path


def run_self_check_cli(argv: Sequence[str] | None = None) -> int:
    """`main.py --self-check [--out PATH]` 진입점. console=False 빌드도 동작."""
    args = list(argv if argv is not None else sys.argv[1:])
    out_path = Path.cwd() / "self_check_report.json"
    if "--out" in args:
        index = args.index("--out")
        if index + 1 < len(args):
            out_path = Path(args[index + 1])

    report = build_report(run_self_checks())
    write_report(report, out_path)
    for line in (
        f"[selfcheck] env={report['env']} ok={report['ok']} out={out_path}",
        *(
            f"[selfcheck] {r['name']}: {'OK' if r['ok'] else 'FAIL'} {r['detail']}"
            for r in report["results"]
        ),
    ):
        print(line)
    return 0 if report["ok"] else 1
