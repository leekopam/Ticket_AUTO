"""pytest 공용 훅 — Boogle 실행 시 metric/artifact/junit evidence를 제출한다.

Boogle SDK command 어댑터는 테스트 프로세스에 다음 환경변수를 주입한다.
- BOOGLE_METRICS_PATH: metric JSON 배열을 기록할 경로
- BOOGLE_ARTIFACTS_DIR: evidence 파일을 모아둘 디렉터리
- BOOGLE_ARTIFACTS_PATH: artifact manifest JSON을 기록할 경로

환경변수가 없으면(일반 pytest 실행) 아무 동작도 하지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(TESTS_DIR) not in sys.path:
    # e2e/e2e_ui 패키지 헬퍼(e2e.support 등)를 conftest에서 import할 수 있게 한다.
    sys.path.insert(0, str(TESTS_DIR))

_METRICS_PATH = os.environ.get("BOOGLE_METRICS_PATH")
_ARTIFACTS_PATH = os.environ.get("BOOGLE_ARTIFACTS_PATH")
_ARTIFACTS_DIR = os.environ.get("BOOGLE_ARTIFACTS_DIR")
_BOOGLE_ENABLED = bool(_METRICS_PATH or _ARTIFACTS_PATH or _ARTIFACTS_DIR)

# junit XML용 테스트 결과 누적
_test_records: list[dict] = []
_session_started = time.perf_counter()

_ARTIFACT_KIND_BY_EXT = {
    ".png": "capture",
    ".jpg": "capture",
    ".jpeg": "capture",
    ".webp": "capture",
    ".webm": "video",
    ".mp4": "video",
    ".zip": "trace",
    ".ndjson": "trace",
}


def pytest_sessionstart(session) -> None:
    if not _BOOGLE_ENABLED:
        return
    try:
        from e2e.support import reset_metrics
    except Exception:
        return
    reset_metrics()


def pytest_runtest_logreport(report) -> None:
    if not _BOOGLE_ENABLED:
        return
    if report.when == "setup" and report.outcome == "passed":
        return
    if report.when not in {"call", "setup", "teardown"}:
        return
    # teardown 실패는 call 결과를 덮어쓰지 않고 별도 기록한다.
    if report.when == "call" or (report.outcome in {"failed", "skipped"}):
        _test_records.append(
            {
                "nodeid": report.nodeid,
                "when": report.when,
                "outcome": report.outcome,
                "duration": float(getattr(report, "duration", 0.0) or 0.0),
                "longrepr": str(report.longrepr) if report.outcome == "failed" else "",
            }
        )


def _artifact_kind(file_name: str) -> str:
    """Boogle manifest kind 규칙(capture|video|output|trace)으로 매핑한다."""
    ext = Path(file_name).suffix.lower()
    return _ARTIFACT_KIND_BY_EXT.get(ext, "output")


def _write_junit_xml(directory: Path) -> Path:
    """수집된 테스트 결과를 junit XML로 기록한다."""
    cases = [r for r in _test_records if r["when"] == "call"]
    teardown_fails = [
        r for r in _test_records if r["when"] == "teardown" and r["outcome"] == "failed"
    ]
    # call 기록이 없는 테스트(setup 실패 등)도 junit에 포함한다.
    covered = {r["nodeid"] for r in cases}
    for r in _test_records:
        if r["nodeid"] not in covered and r["when"] == "setup":
            cases.append(r)
            covered.add(r["nodeid"])

    suite = ET.Element(
        "testsuite",
        {
            "name": "ticket-auto-e2e",
            "tests": str(len(cases) + len(teardown_fails)),
            "failures": str(
                sum(1 for r in cases if r["outcome"] == "failed") + len(teardown_fails)
            ),
            "skipped": str(sum(1 for r in cases if r["outcome"] == "skipped")),
            "time": f"{sum(r['duration'] for r in cases):.3f}",
        },
    )
    for rec in cases:
        nodeid = rec["nodeid"]
        classname = nodeid.split("::")[0].replace("/", ".").replace("\\", ".")
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": classname,
                "name": nodeid,
                "time": f"{rec['duration']:.3f}",
            },
        )
        if rec["outcome"] == "failed":
            failure = ET.SubElement(case, "failure", {"message": "test failed"})
            failure.text = rec["longrepr"][:4000]
        elif rec["outcome"] == "skipped":
            ET.SubElement(case, "skipped")
    for rec in teardown_fails:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": rec["nodeid"].split("::")[0],
                "name": f"{rec['nodeid']}::teardown",
                "time": "0",
            },
        )
        failure = ET.SubElement(case, "failure", {"message": "teardown failed"})
        failure.text = rec["longrepr"][:4000]

    target = directory / "junit-e2e.xml"
    ET.ElementTree(suite).write(target, encoding="utf-8", xml_declaration=True)
    return target


def _metric(
    name: str,
    value: float,
    *,
    unit: str,
    direction: str,
    threshold_kind: str,
    threshold_value: float,
    aggregation: str = "single",
) -> dict:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "definitionVersion": "1",
        "aggregation": aggregation,
        "direction": direction,
        "threshold": {"kind": threshold_kind, "value": threshold_value},
    }


def _write_metrics() -> None:
    """계획서 §4 계약의 metric 배열을 BOOGLE_METRICS_PATH에 기록한다."""
    if not _METRICS_PATH:
        return
    try:
        from e2e.support import metric_snapshot
    except Exception:
        metric_snapshot = dict

    counters = metric_snapshot()
    duration_ms = (time.perf_counter() - _session_started) * 1000.0
    cases = [r for r in _test_records if r["when"] == "call"]
    failed = sum(1 for r in cases if r["outcome"] == "failed")
    passed = sum(1 for r in cases if r["outcome"] == "passed")

    metrics = [
        _metric(
            "e2e/duration_ms",
            duration_ms,
            unit="ms",
            direction="lower",
            threshold_kind="relative_percent",
            threshold_value=100.0,
        ),
        _metric(
            "e2e/tests_total",
            float(len(cases)),
            unit="count",
            direction="higher",
            threshold_kind="absolute",
            threshold_value=0.0,
            aggregation="sum",
        ),
        _metric(
            "e2e/tests_passed",
            float(passed),
            unit="count",
            direction="higher",
            threshold_kind="absolute",
            threshold_value=0.0,
            aggregation="sum",
        ),
        _metric(
            "e2e/tests_failed",
            float(failed),
            unit="count",
            direction="lower",
            threshold_kind="absolute",
            threshold_value=0.0,
            aggregation="sum",
        ),
        _metric(
            "e2e/status_transitions",
            float(counters.get("status_transitions", 0)),
            unit="count",
            direction="higher",
            threshold_kind="relative_percent",
            threshold_value=50.0,
            aggregation="sum",
        ),
        _metric(
            "e2e/recovery_attempts",
            float(counters.get("recovery_attempts", 0)),
            unit="count",
            direction="higher",
            threshold_kind="relative_percent",
            threshold_value=50.0,
            aggregation="sum",
        ),
        _metric(
            "e2e/rollback_successes",
            float(counters.get("rollback_successes", 0)),
            unit="count",
            direction="higher",
            threshold_kind="absolute",
            threshold_value=0.0,
            aggregation="sum",
        ),
        _metric(
            "e2e/print_jobs",
            float(counters.get("print_jobs", 0)),
            unit="count",
            direction="higher",
            threshold_kind="relative_percent",
            threshold_value=50.0,
            aggregation="sum",
        ),
        _metric(
            "e2e/resolve_redirects",
            float(counters.get("resolve_redirects", 0)),
            unit="count",
            direction="higher",
            threshold_kind="relative_percent",
            threshold_value=50.0,
            aggregation="sum",
        ),
    ]
    if "camera_preview_samples" in counters:
        metrics.extend(
            [
                _metric(
                    "e2e/camera_preview_samples",
                    float(counters["camera_preview_samples"]),
                    unit="count",
                    direction="higher",
                    threshold_kind="absolute",
                    threshold_value=0.0,
                ),
                _metric(
                    "e2e/camera_preview_black_frames",
                    float(counters["camera_preview_black_frames"]),
                    unit="count",
                    direction="lower",
                    threshold_kind="absolute",
                    threshold_value=0.0,
                ),
            ]
        )
    if "device_camera_sampled_frames" in counters:
        sampled = counters["device_camera_sampled_frames"]
        metrics.extend(
            [
                _metric(
                    "e2e/device_camera_qr_decode_percent",
                    100.0 * counters["device_camera_qr_frames"] / sampled if sampled else 0.0,
                    unit="percent",
                    direction="higher",
                    threshold_kind="relative_percent",
                    threshold_value=20.0,
                ),
                _metric(
                    "e2e/device_camera_read_failures",
                    float(counters["device_camera_read_failures"]),
                    unit="count",
                    direction="lower",
                    threshold_kind="absolute",
                    threshold_value=0.0,
                ),
            ]
        )
    Path(_METRICS_PATH).write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_artifact_manifest() -> None:
    """artifacts 디렉터리의 파일을 manifest로 기록한다(파일↔목록 일치 필수)."""
    if not _ARTIFACTS_PATH or not _ARTIFACTS_DIR:
        return
    directory = Path(_ARTIFACTS_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in directory.iterdir() if p.is_file())
    manifest = {
        "artifacts": [
            {"fileName": p.name, "kind": _artifact_kind(p.name)} for p in files
        ]
    }
    Path(_ARTIFACTS_PATH).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def pytest_sessionfinish(session, exitstatus) -> None:
    if not _BOOGLE_ENABLED:
        return
    try:
        if _ARTIFACTS_DIR:
            _write_junit_xml(Path(_ARTIFACTS_DIR))
        _write_metrics()
        _write_artifact_manifest()
    except Exception:
        # evidence 기록 실패가 테스트 결과를 덮지 않도록 로그만 남긴다.
        import traceback

        traceback.print_exc()
