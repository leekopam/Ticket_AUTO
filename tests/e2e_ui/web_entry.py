"""L2 UI E2E용 대시보드 web 기동 엔트리.

pytest fixture가 서브프로세스로 실행한다:
    python tests/e2e_ui/web_entry.py --port <flet> --control-port <cmd>
        --runtime-dir <격리 런타임 폴더> --data-file <테스트 워크북 xlsx>

- project_paths.PROJECT_ROOT를 --runtime-dir로 리다이렉트해
  .runtime/*, Resources/data/* 쓰기를 테스트 폴더로 격리한다.
- 실제 카메라/브라우저 대신 FakeDashboardRuntimeApp을 런타임에 주입한다.
- 제어 HTTP 서버로 테스트 프로세스가 런타임 이벤트를 주입한다.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import threading
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TESTS_DIR = PROJECT_ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import project_paths  # noqa: E402

from e2e_ui.support import (  # noqa: E402
    FakeDashboardRuntimeApp,
    find_free_port,
    run_control_server,
)


def _demo_console(control_url: str) -> None:
    """데모 콘솔: stdin 명령을 제어 서버로 전달해 런타임 이벤트를 발생시킨다."""
    import base64
    import io

    from e2e.support import TEST_ORDER_NUMBER, TEST_QR_URL
    from e2e_ui.support import send_control_command
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


def main() -> int:
    parser = argparse.ArgumentParser(description="대시보드 web E2E 기동 엔트리")
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
    args = parser.parse_args()

    if args.demo:
        import tempfile

        from e2e.support import create_test_workbook

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
    from e2e.support import FakePrinterBackend

    fake_printer = FakePrinterBackend()
    receipt_pipeline.WindowsPrinterService = lambda: fake_printer  # type: ignore[assignment]
    # 설정 뷰가 직접 임포트한 경로도 동일 스텁으로 교체한다.
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

    if args.demo:
        print(f"[demo] 대시보드: http://127.0.0.1:{port} (브라우저 자동 열림)", flush=True)
        print("[demo] 콘솔 명령: order(주문) / frame(QR 프레임) / relogin(재로그인)", flush=True)
        print("[demo] 종료: 이 창을 닫거나 Ctrl+C", flush=True)
        control_url = f"http://127.0.0.1:{control_port}"
        threading.Thread(
            target=_demo_console, args=(control_url,), daemon=True
        ).start()

    view = DashboardFletView(
        runtime_manager=runtime_manager,
        window_title=args.title or None,
        phone_link_service=phone_link,
    )
    if args.native:
        view.run()
    else:
        view.run(web_port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
