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
import webbrowser
from pathlib import Path

# web 모드 기동 시 실제 브라우저 창이 열리지 않게 한다.
webbrowser.open = lambda *args, **kwargs: True  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
TESTS_DIR = PROJECT_ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import project_paths  # noqa: E402

from e2e_ui.support import FakeDashboardRuntimeApp, run_control_server  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="대시보드 web E2E 기동 엔트리")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--control-port", type=int, required=True)
    parser.add_argument("--runtime-dir", type=str, required=True)
    parser.add_argument("--data-file", type=str, default="")
    args = parser.parse_args()

    runtime_dir = Path(args.runtime_dir).resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)

    # 쓰기 경로(.runtime/, Resources/data/)를 테스트 폴더로 격리한다.
    project_paths.PROJECT_ROOT = runtime_dir

    # 테스트 data 파일을 격리된 managed data 경로에 배치한다.
    if args.data_file:
        data_target = runtime_dir / "Resources" / "data" / "data.xlsx"
        data_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.data_file, data_target)

    from services.ticket_runtime_manager import TicketRuntimeManager
    from views.dashboard_flet_view import DashboardFletView

    # 프린터 IO 경계를 스텁으로 교체해 실제 스풀러 출력을 막고 작업을 기록한다.
    import services.receipt_print_pipeline as receipt_pipeline
    from e2e.support import FakePrinterBackend

    fake_printer = FakePrinterBackend()
    receipt_pipeline.WindowsPrinterService = lambda: fake_printer  # type: ignore[assignment]

    # 런타임 재시작마다 새 스텁 앱을 만들고, 제어 서버는 최신 인스턴스로 라우팅한다.
    current_app: dict[str, FakeDashboardRuntimeApp | None] = {"value": None}

    def _app_factory() -> FakeDashboardRuntimeApp:
        current_app["value"] = FakeDashboardRuntimeApp()
        return current_app["value"]

    runtime_manager = TicketRuntimeManager(app_factory=_app_factory)
    run_control_server(
        lambda: current_app["value"], args.control_port, printer=fake_printer
    )

    DashboardFletView(runtime_manager=runtime_manager).run(web_port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
