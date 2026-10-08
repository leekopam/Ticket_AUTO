"""release APK 품질 게이트 — 설치·기동 스모크 + ANR/크래시 감시.

`build_apk.bat` 산출물(`app-release.apk`)을 실기기에 설치해 기동한 뒤,
logcat에서 ANR·FATAL EXCEPTION·native crash·프로세스 사망을 감시한다.
UI 플로우 검증은 Maestro/Appium 플로우로 확장하고, 이 스크립트는
설치-기동-안정성 게이트를 담당한다.

사용:
    python scripts/qa/apk_e2e.py --apk <app-release.apk> [--serial R3CX2094YJW]
        [--monitor-seconds 15] [--adb <adb.exe 경로>]
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PACKAGE = "com.leekopam.ticket_auto_android"
DEFAULT_ACTIVITY = ".MainActivity"
DEFAULT_SERIAL = "R3CX2094YJW"
_DEFAULT_ADB = Path(
    os.environ.get("LOCALAPPDATA", "")
) / "Android" / "Sdk" / "platform-tools" / "adb.exe"

# logcat 한 줄에서 이상 징후를 잡는 패턴. (kind, 정규식)
# ANR은 ActivityManager 태그를 요구한다 — shell 명령 echo 등의 오탐 방지.
_ANOMALY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("anr", r"ActivityManager\s*:.*ANR in {pkg}"),
    ("anr", r"am_anr.*{pkg}"),
    ("fatal", r"FATAL EXCEPTION"),
    ("native_crash", r"Fatal signal \d+"),
    ("process_death", r"Process {pkg} \(pid \d+\) has died"),
    # AOSP/OneUI의 프로세스 사망 로그 — force-stop 등에서 실제로 찍힌다.
    ("process_death", r"Got obituary of \d+:{pkg}"),
    ("process_death", r"Killing \d+:{pkg}"),
    ("process_death", r"Force finishing activity.*{pkg}"),
)


@dataclass(frozen=True)
class AnomalyEvent:
    """logcat에서 감지한 이상 이벤트."""

    kind: str
    line: str
    timestamp: float


def match_anomaly(line: str, package: str) -> str | None:
    """logcat 한 줄이 ANR/크래시 이상이면 kind를, 아니면 None을 반환한다."""
    for kind, pattern in _ANOMALY_PATTERNS:
        if re.search(pattern.format(pkg=re.escape(package)), line):
            return kind
    return None


def resolve_adb(override: str) -> str:
    """adb 경로를 결정한다. --adb > PATH > 기본 SDK 경로."""
    if override:
        return override
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    if _DEFAULT_ADB.is_file():
        return str(_DEFAULT_ADB)
    raise FileNotFoundError("adb를 찾지 못했습니다. --adb 또는 PATH를 지정하세요")


class Adb:
    """adb 서브프로세스 호출 헬퍼."""

    def __init__(self, adb_path: str, serial: str) -> None:
        self._base = [adb_path]
        if serial:
            self._base += ["-s", serial]

    def run(self, *args: str, timeout: float = 60) -> str:
        result = subprocess.run(
            [*self._base, *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"adb {' '.join(args)} 실패({result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout

    def popen(self, *args: str) -> subprocess.Popen:
        return subprocess.Popen(
            [*self._base, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )


class LogcatWatcher:
    """logcat을 백그라운드에서 읽어 ANR/크래시 이벤트를 수집한다."""

    def __init__(self, adb: Adb, package: str) -> None:
        self._adb = adb
        self._package = package
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.events: list[AnomalyEvent] = []
        self._tail: list[str] = []

    def start(self) -> None:
        # 이전 세션의 누적 로그가 섞이지 않게 버퍼를 비운다.
        self._adb.run("logcat", "-c", timeout=15)
        self._proc = self._adb.popen("logcat", "-v", "threadtime")
        self._thread = threading.Thread(
            target=self._read_loop, name="logcat-watcher", daemon=True
        )
        self._thread.start()

    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.rstrip()
            self._tail.append(line)
            if len(self._tail) > 2000:
                del self._tail[:1000]
            kind = match_anomaly(line, self._package)
            if kind:
                self.events.append(
                    AnomalyEvent(kind=kind, line=line, timestamp=time.monotonic())
                )

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def tail_text(self, lines: int = 60) -> str:
        return "\n".join(self._tail[-lines:])

    def summary_lines(self) -> list[str]:
        counts: dict[str, int] = {}
        for event in self.events:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        head = ", ".join(f"{k}={v}" for k, v in counts.items()) or "이상 없음"
        return [f"anomaly events: {head}"] + [f"  {e.kind}: {e.line[:160]}" for e in self.events[:20]]


def wait_for_foreground(adb: Adb, package: str, timeout_sec: float) -> bool:
    """앱이 최상단 resumed activity가 될 때까지 대기한다.

    권한 다이얼로그가 input focus를 가져가도 topResumedActivity는 우리 앱을
    유지하므로 mCurrentFocus보다 이 지표가 정확하다.
    """
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            out = adb.run("shell", "dumpsys", "activity", "activities", timeout=15)
        except RuntimeError:
            time.sleep(0.5)
            continue
        for line in out.splitlines():
            if ("topResumedActivity" in line or "ResumedActivity" in line) and package in line:
                return True
        time.sleep(0.5)
    return False


def run_link_flow(
    adb: Adb,
    serial: str,
    package: str,
    artifact_dir: Path,
    pair_timeout: float = 90.0,
    transition_timeout: float = 45.0,
) -> tuple[bool, list[str]]:
    """실제 페어링→Wi-Fi 끊김/복구 표시까지 블랙박스 검증한다.

    PC에 테스트 API 서버를 띄우고 페어링 QR을 화면에 표시한다.
    release APK에는 QR 주입 경로가 없으므로 폰이 카메라로 화면의 QR을
    실제로 스캔하는 실제 사용자 플로우 그대로 진행된다 — 실행 중
    폰 카메라를 화면의 QR에 맞춰야 한다(물리 상호작용 필요).
    """
    import json

    try:
        import uiautomator2 as u2
    except ImportError as exc:
        raise RuntimeError(
            "--link-flow 는 uiautomator2가 필요합니다: pip install uiautomator2"
        ) from exc

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from services.api_v1_server import build_pairing_qr_payload, create_server
    from services.cert_service import detect_lan_ips
    from services.network_path_service import order_serving_ips
    from services.excel_service import ExcelService
    from services.qr_generator_service import generate_qr_image

    report: list[str] = []
    work_dir = artifact_dir / "link-flow"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 테스트 API 서버 + 자동 승인 (link_e2e.py와 동일 패턴)
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    server, pairing, fingerprint = create_server(
        excel=ExcelService(str(work_dir / "data.xlsx")),
        port=port,
        cert_dir=str(work_dir / "certs"),
    )
    server.start()
    if not server.wait_started(timeout=5.0):
        return False, ["테스트 API 서버 기동 실패 (포트 점유/방화벽 확인)"]
    report.append(f"테스트 API 서버 기동: {port}번 포트")

    stop = threading.Event()

    def _auto_approve() -> None:
        while not stop.is_set():
            for pending in pairing.pending_approvals():
                pairing.approve(pending.pair_ticket)
                report.append(f"페어링 자동 승인: {pending.device_name}")
            time.sleep(0.5)

    threading.Thread(target=_auto_approve, daemon=True).start()

    # ips[0] 임의 선택은 WSL·가상 어댑터를 QR에 실을 수 있다 — 서빙 주소 정렬로
    # 기본 주소와 alt 후보를 만들어 폰이 도달 가능한 주소로 페어링하게 한다.
    ordered_ips = order_serving_ips(detect_lan_ips())
    if not ordered_ips:
        stop.set()
        server.stop()
        return False, [*report, "LAN 주소를 찾지 못했습니다. 네트워크 연결을 확인하세요"]
    addr = f"https://{ordered_ips[0]}:{port}"
    payload = build_pairing_qr_payload(
        addr, fingerprint, pairing.issue_join_code(), "",
        alt_addrs=[f"https://{ip}:{port}" for ip in ordered_ips[1:]],
    )
    qr_path = work_dir / "pairing-qr.png"
    generate_qr_image(
        json.dumps(payload, ensure_ascii=False), output_px=720
    ).save(qr_path)
    # 폰 카메라가 읽기 쉽게 전체 화면 HTML로 연다.
    html = work_dir / "pairing-qr.html"
    html.write_text(
        '<!doctype html><meta charset="utf-8"><style>'
        "body{margin:0;background:#fff;display:flex;align-items:center;"
        "justify-content:center;height:100vh}"
        "img{width:95vmin;height:95vmin}</style>"
        f'<img src="{qr_path.name}">',
        encoding="utf-8",
    )
    os.startfile(str(html))  # noqa: S606 - 로컬 정적 파일을 기본 뷰어로 여는 용도
    report.append("화면의 QR을 폰 카메라로 스캔하세요 (페어링은 자동 승인됨)")

    wifi_down = False
    device = u2.connect(serial)
    device.implicitly_wait(10.0)
    try:
        # 신규 상태에서 앱 기동 → 카메라 권한 → 폰이 QR을 실제로 스캔
        adb.run("shell", "pm", "clear", package)
        launch_app(adb, package, ".MainActivity")
        grant = device(text="앱 사용 중에만 허용")
        if grant.wait(timeout=8.0):
            grant.click()
            report.append("카메라 권한 승인")

        if not device(description="티켓 QR 코드 스캔 영역").wait(
            timeout=pair_timeout
        ):
            device.screenshot(str(work_dir / "pair-timeout.png"))
            report.append(
                f"페어링/스캔 화면 미도달 ({pair_timeout:.0f}초) — "
                "폰 카메라를 화면의 QR에 맞췄는지 확인"
            )
            return False, report
        report.append("페어링 완료 — 스캔 화면 확인")

        # Wi-Fi 차단 → '서버 연결 끊김' 배너(재시도 버튼) 표시 확인.
        # 폰의 하트비트 주기가 15초라 여유 있게 기다린다.
        adb.run("shell", "svc", "wifi", "disable")
        wifi_down = True
        report.append("Wi-Fi 차단 — 끊김 표시 대기")
        retry = device(description="재시도")
        if not retry.wait(timeout=transition_timeout):
            device.screenshot(str(work_dir / "wifi-down.png"))
            report.append("끊김 표시(재시도 배너) 미감지 — 회귀 의심")
            return False, report
        device.screenshot(str(work_dir / "wifi-down.png"))
        report.append("끊김 표시 확인 (재시도 배너)")

        # Wi-Fi 복구 → 배너 소실 + 스캔 화면 유지 확인
        adb.run("shell", "svc", "wifi", "enable")
        wifi_down = False
        report.append("Wi-Fi 복구 — 복구 표시 대기")
        deadline = time.monotonic() + transition_timeout
        recovered = False
        while time.monotonic() < deadline:
            if not retry.exists and device(
                description="티켓 QR 코드 스캔 영역"
            ).exists:
                recovered = True
                break
            time.sleep(1.0)
        if not recovered:
            device.screenshot(str(work_dir / "wifi-up.png"))
            report.append("복구 표시 미감지 — 배너가 사라지지 않음")
            return False, report
        report.append("복구 표시 확인 — 스캔 화면 정상")
        return True, report
    finally:
        if wifi_down:
            adb.run("shell", "svc", "wifi", "enable")  # 실패해도 폰 Wi-Fi는 복구
        stop.set()
        server.stop()


def run_ui_flow(
    adb: Adb,
    serial: str,
    package: str,
    screenshot_path: Path | None = None,
) -> tuple[bool, list[str]]:
    """uiautomator2로 release APK 첫 화면을 블랙박스 검증한다.

    신규 설치 기준 카메라 권한 다이얼로그→페어링 화면→테마 메뉴 상호작용까지
    확인해 '기동은 되는데 화면이 깨진' 회귀를 잡는다.
    """
    try:
        import uiautomator2 as u2
    except ImportError as exc:
        raise RuntimeError(
            "--ui 는 uiautomator2가 필요합니다: pip install uiautomator2 "
            "(초기 1회 python -m uiautomator2 init)"
        ) from exc

    report: list[str] = []
    device = u2.connect(serial)
    device.implicitly_wait(10.0)

    # 신규 설치 시 카메라 권한 다이얼로그를 실제 사용자처럼 승인한다.
    grant = device(text="앱 사용 중에만 허용")
    if grant.wait(timeout=8.0):
        grant.click()
        report.append("카메라 권한 다이얼로그 승인")

    # 페어링 화면 앵커: 앱바 타이틀/스캔 영역/안내 문구 (Flutter Semantics→content-desc)
    checks = [
        ("PC 연결", "description"),
        ("PC 연결 QR 코드 스캔 영역", "description"),
        ("PC 연결 QR을 스캔해주세요.", "description"),
    ]
    for name, by in checks:
        node = device(description=name) if by == "description" else device(text=name)
        if not node.wait(timeout=15.0):
            if screenshot_path is not None:
                device.screenshot(str(screenshot_path))
            report.append(f"화면 앵커 미노출: {name}")
            return False, report
        report.append(f"화면 앵커 확인: {name}")

    # 테마 메뉴 열기→뒤로가기로 상호작용 응답을 확인한다.
    theme_menu = device(description="테마 설정")
    if theme_menu.wait(timeout=5.0):
        theme_menu.click()
        time.sleep(0.8)
        device.press("back")
        time.sleep(0.5)
        report.append("테마 메뉴 상호작용 확인")

    if screenshot_path is not None:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        device.screenshot(str(screenshot_path))
        report.append(f"스크린샷: {screenshot_path}")

    # 상호작용 후에도 앱이 살아 있는지 확인한다.
    if not device(description="PC 연결 QR 코드 스캔 영역").wait(timeout=10.0):
        report.append("상호작용 후 스캔 영역 소실 — 응답없음/크래시 의심")
        return False, report
    return True, report


def install_apk(adb: Adb, apk: Path) -> None:
    out = adb.run("install", "-r", str(apk), timeout=300)
    if "Success" not in out:
        raise RuntimeError(f"APK 설치 실패: {out.strip()}")


def launch_app(adb: Adb, package: str, activity: str) -> None:
    adb.run(
        "shell", "am", "start", "-n", f"{package}/{activity}",
        "-a", "android.intent.action.MAIN",
        "-c", "android.intent.category.LAUNCHER",
        timeout=30,
    )


def run_apk_gate(
    adb: Adb,
    apk: Path,
    package: str,
    activity: str,
    monitor_seconds: float,
    *,
    serial: str = "",
    ui: bool = False,
    monkey_events: int = 0,
    screenshot_path: Path | None = None,
    link_flow: bool = False,
    artifact_dir: Path | None = None,
) -> tuple[bool, list[str]]:
    """설치→기동→포그라운드 확인→(선택)UI/링크 플로우/스트레스→ANR 감시."""
    report: list[str] = []
    ok = True
    install_apk(adb, apk)
    report.append(f"설치 성공: {apk.name}")

    watcher = LogcatWatcher(adb, package)
    watcher.start()
    try:
        if link_flow:
            try:
                lf_ok, lf_report = run_link_flow(
                    adb, serial, package,
                    artifact_dir or Path("artifacts") / "apk-gate",
                )
            except RuntimeError as exc:
                lf_ok, lf_report = False, [str(exc)]
            report.extend(lf_report)
            ok = ok and lf_ok
        else:
            launch_app(adb, package, activity)
            if not wait_for_foreground(adb, package, timeout_sec=30.0):
                report.append("포그라운드 진입 실패")
                report.append(watcher.tail_text())
                return False, report
            report.append(f"포그라운드 진입 확인: {package}")

        if ui and not link_flow:
            try:
                ui_ok, ui_report = run_ui_flow(
                    adb, serial, package, screenshot_path=screenshot_path
                )
            except RuntimeError as exc:
                ui_ok, ui_report = False, [str(exc)]
            report.extend(ui_report)
            ok = ok and ui_ok

        if monkey_events > 0:
            adb.run(
                "shell", "monkey", "-p", package,
                "--pct-syskeys", "0", "-v", str(monkey_events),
                timeout=monkey_events / 10 + 60,
            )
            report.append(f"monkey 스트레스 {monkey_events} 이벤트 완료")

        deadline = time.monotonic() + monitor_seconds
        while time.monotonic() < deadline:
            time.sleep(0.5)
            if any(e.kind in ("anr", "fatal", "native_crash") for e in watcher.events):
                break
    finally:
        watcher.stop()

    report.extend(watcher.summary_lines())
    if watcher.events:
        report.append("-- logcat tail --")
        report.append(watcher.tail_text())
        return False, report
    return ok, report

    report.extend(watcher.summary_lines())
    if watcher.events:
        report.append("-- logcat tail --")
        report.append(watcher.tail_text())
        return False, report
    return True, report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="release APK 설치/기동/ANR 게이트")
    parser.add_argument("--apk", required=True, type=Path)
    parser.add_argument("--serial", default=DEFAULT_SERIAL, help="adb 기기 시리얼")
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--activity", default=DEFAULT_ACTIVITY)
    parser.add_argument("--monitor-seconds", type=float, default=15.0)
    parser.add_argument("--adb", default="", help="adb.exe 경로(미지정 시 PATH/SDK 기본값)")
    parser.add_argument(
        "--ui",
        action="store_true",
        help="uiautomator2로 첫 화면 블랙박스 검증까지 수행",
    )
    parser.add_argument(
        "--monkey-events",
        type=int,
        default=0,
        help="무작위 입력 스트레스 이벤트 수(0이면 생략)",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        default=None,
        help="UI 검증 스크린샷 저장 경로",
    )
    parser.add_argument(
        "--link-flow",
        action="store_true",
        help="페어링 QR 화면 표시→실제 스캔→Wi-Fi 끊김/복구 표시 검증 "
             "(폰 카메라를 화면에 맞추는 물리 상호작용 필요)",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="링크 플로우 산출물(QR/스크린샷/서버 인증서) 저장 경로",
    )
    return parser.parse_args()


def main() -> int:
    # cp949 콘솔에서 '—' 같은 문자로 print가 죽지 않게 한다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = _parse_args()
    if not args.apk.is_file():
        print(f"APK 없음: {args.apk}", flush=True)
        return 1

    try:
        adb = Adb(resolve_adb(args.adb), args.serial)
        adb.run("devices", timeout=15)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"adb 확인 실패: {exc}", flush=True)
        return 1

    try:
        ok, report = run_apk_gate(
            adb,
            args.apk,
            args.package,
            args.activity,
            args.monitor_seconds,
            serial=args.serial,
            ui=args.ui,
            monkey_events=args.monkey_events,
            screenshot_path=args.screenshot,
            link_flow=args.link_flow,
            artifact_dir=args.artifact_dir,
        )
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"게이트 실패: {exc}", flush=True)
        return 1

    for line in report:
        print(line, flush=True)
    print("APK 게이트: PASS" if ok else "APK 게이트: FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
