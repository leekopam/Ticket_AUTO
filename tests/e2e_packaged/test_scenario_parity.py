"""소스 실행과 패키징 exe의 시나리오 동치 E2E — UIA 미사용 제어 경로만 사용.

같은 --e2e 하니스를 `--packaged-src`(main.py)와 exe로 각각 기동해,
동일한 제어 명령 시퀀스의 관측 결과가 일치함을 검증한다.
셀프체크가 '경계 존재'만 확인하는 것과 달리 실제 기능 경로
(런타임 수명주기·TLS 페어링·스캔·영수증 렌더→출력)를 구동한다.

영수증 렌더 픽셀 해시는 artifacts/test-results/parity/에 모드별로 남고,
양쪽 결과가 모두 있으면 P04가 동치를 비교한다 — 번들된 템플릿·폰트·
렌더 경로가 소스와 다르면 여기서 잡힌다.
verify_release.ps1 -Release는 이 디렉토리를 정리한 뒤 양쪽을 연속 실행한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows 전용")

from e2e_harness import (  # noqa: E402
    TEST_QR_URL,
    create_test_workbook,
    fetch_printer_jobs,
    fetch_runtime_calls,
    find_free_port,
    send_control_command,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PARITY_DIR = _REPO_ROOT / "artifacts" / "test-results" / "parity"


def _wait_ping(control_url: str, timeout_sec: float = 90.0) -> bool:
    """제어 서버가 응답할 때까지 기다린다(방금 빌드된 exe는 스캔 지연이 있다)."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{control_url}/ping", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _ensure_runtime_running(control_url: str) -> None:
    """런타임이 RUNNING 상태가 되도록 한다(이미 실행 중이면 그대로 둔다)."""
    started = send_control_command(control_url, {"cmd": "runtime_start"})
    assert started["result"]["state"] == "RUNNING", started["result"]


@pytest.fixture(scope="module")
def control_target(request, tmp_path_factory):
    """소스(--packaged-src) 또는 패키징 exe를 --e2e 하니스로 기동한다.

    UIA를 붙이지 않아 COM 세션 크래시와 무관하게 동작한다.
    """
    src_mode = bool(request.config.getoption("--packaged-src"))
    if src_mode:
        launch = [sys.executable, str(_REPO_ROOT / "main.py")]
        mode = "src"
    else:
        exe_path = Path(request.config.getoption("--packaged-exe"))
        if not exe_path.is_file():
            pytest.skip(f"패키징 exe 없음: {exe_path} (build 후 실행)")
        launch = [str(exe_path)]
        mode = "exe"

    runtime_dir = tmp_path_factory.mktemp("parity_runtime")
    seed = runtime_dir / "seed" / "data.xlsx"
    seed.parent.mkdir(parents=True, exist_ok=True)
    create_test_workbook(seed)

    control_port = find_free_port()
    log_path = runtime_dir / "parity-app.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        launch
        + [
            "--e2e",
            "--native",
            "--title", f"parity_{mode}_{os.getpid()}",
            "--control-port", str(control_port),
            "--runtime-dir", str(runtime_dir),
            "--data-file", str(seed),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    control_url = f"http://127.0.0.1:{control_port}"

    if not _wait_ping(control_url):
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, check=False,
        )
        log_file.close()
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        except Exception:
            pass
        pytest.fail(f"parity 대상 기동 실패 ({mode}):\n{tail}")

    yield {"control_url": control_url, "mode": mode}

    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
        capture_output=True, check=False,
    )
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    log_file.close()


def test_p01_runtime_lifecycle_and_calls(control_target):
    """P01: 런타임 시작→재로그인 명령→중지가 대상 프로세스에서 동일하게 동작한다."""
    control_url = control_target["control_url"]
    _ensure_runtime_running(control_url)

    send_control_command(control_url, {"cmd": "relogin"})
    deadline = time.monotonic() + 10
    calls: list = []
    while time.monotonic() < deadline:
        calls = fetch_runtime_calls(control_url)
        if ["request_relogin", None] in calls:
            break
        time.sleep(0.3)
    assert ["request_relogin", None] in calls, f"relogin 미전달: {calls}"

    stopped = send_control_command(control_url, {"cmd": "runtime_stop"})
    assert stopped["result"]["state"] == "IDLE", stopped["result"]


def test_p02_phone_tls_roundtrip(control_target):
    """P02: 실제 TLS 페어링→status→스캔→재스캔이 대상에서 동일하게 완주한다."""
    control_url = control_target["control_url"]
    _ensure_runtime_running(control_url)

    link = send_control_command(control_url, {"cmd": "phone_link_start"})
    addr = link["result"]["addr"]
    fingerprint = link["result"]["fingerprint"]
    assert addr.startswith("https://") and fingerprint

    # 광고 주소가 호스트 기준 최적 서빙 IP와 일치하는지 — 가상 어댑터 오선택 회귀 감지.
    from services.cert_service import detect_lan_ips
    from services.network_path_service import order_serving_ips

    ordered = order_serving_ips(detect_lan_ips())
    if ordered:
        advertised_ip = addr.split("://", 1)[-1].rsplit(":", 1)[0]
        assert advertised_ip == ordered[0], (
            f"QR 광고 주소 오선택: 광고={advertised_ip} 기대={ordered[0]}"
        )

    pair = send_control_command(
        control_url,
        {
            "cmd": "phone_pair",
            "device_name": "parity-phone",
            "device_uid": f"parity-{control_target['mode']}",
            "approve": True,
        },
    )
    token = pair["result"]["token"]
    assert token, f"페어링 토큰 미발급: {pair['result']}"

    status = send_control_command(
        control_url, {"cmd": "phone_status", "token": token}
    )
    assert status["result"]["http"] == 200, status["result"]

    mode = control_target["mode"]
    scan = send_control_command(
        control_url,
        {
            "cmd": "phone_scan",
            "token": token,
            "qr_url": TEST_QR_URL,
            "request_id": f"parity-{mode}-scan-1",
        },
    )["result"]
    assert scan.get("state") == "succeeded", scan

    rescan = send_control_command(
        control_url,
        {
            "cmd": "phone_scan",
            "token": token,
            "qr_url": TEST_QR_URL,
            "request_id": f"parity-{mode}-scan-2",
        },
    )["result"]
    assert rescan.get("state") == "already_processed", rescan


def test_p03_receipt_render_and_print(control_target):
    """P03: 실제 영수증 렌더→스텁 프린터 경로가 동작하고 픽셀 해시를 기록한다."""
    control_url = control_target["control_url"]
    result = send_control_command(control_url, {"cmd": "print_receipt"})
    assert result.get("ok"), result
    assert result["result"]["printed"] >= 1, result

    jobs = fetch_printer_jobs(control_url)
    job = next((j for j in jobs if "TEST-ORDER-01" in j["job_name"]), None)
    assert job is not None, f"영수증 작업 미기록: {jobs}"
    digest = str(job.get("image_sha256", ""))
    assert len(digest) == 64, f"이미지 해시 누락: {job}"

    _PARITY_DIR.mkdir(parents=True, exist_ok=True)
    mode = control_target["mode"]
    (_PARITY_DIR / f"receipt-{mode}.sha256").write_text(digest, encoding="utf-8")
    (_PARITY_DIR / f"receipt-{mode}.json").write_text(
        json.dumps(
            {
                "sha256": digest,
                "image_size": job.get("image_size"),
                "job_name": job["job_name"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_p04_receipt_image_parity(control_target):
    """P04: src/exe 양쪽의 영수증 렌더 픽셀이 동일한지 비교한다."""
    src_file = _PARITY_DIR / "receipt-src.sha256"
    exe_file = _PARITY_DIR / "receipt-exe.sha256"
    if not (src_file.is_file() and exe_file.is_file()):
        pytest.skip("양쪽(src/exe) 실행 결과가 모두 있어야 비교 가능")
    src_digest = src_file.read_text(encoding="utf-8").strip()
    exe_digest = exe_file.read_text(encoding="utf-8").strip()
    assert src_digest == exe_digest, (
        "영수증 렌더 픽셀이 src/exe 간 불일치 — 템플릿·폰트·렌더 경로 번들 회귀 가능. "
        f"src={src_digest[:16]} exe={exe_digest[:16]}"
    )
