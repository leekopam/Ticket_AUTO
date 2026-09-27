"""self-check 서비스와 src↔exe 파리티 비교기 계약 테스트."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from services.self_check_service import (
    SelfCheckResult,
    build_report,
    run_self_check_cli,
    run_self_checks,
    write_report,
)


def _load_compare_module():
    """scripts/qa는 패키지가 아니므로 파일 경로로 로드한다."""
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "qa"
        / "compare_self_check.py"
    )
    spec = importlib.util.spec_from_file_location("compare_self_check", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- run_self_checks ---


def test_run_self_checks_executes_every_check_even_after_failure():
    calls = []

    def good():
        calls.append("good")
        return SelfCheckResult("good", True, "ok")

    def bad():
        calls.append("bad")
        raise RuntimeError("경계 예외")

    results = run_self_checks(checks=(("good", good), ("bad", bad)))
    assert calls == ["good", "bad"]
    assert results[0] == SelfCheckResult("good", True, "ok")
    assert results[1].name == "bad"
    assert results[1].ok is False
    assert "RuntimeError" in results[1].detail


def test_run_self_checks_default_suite_covers_feature_boundaries():
    names = [r.name for r in run_self_checks()]
    assert "imports" in names
    assert "project_paths" in names
    assert "qr_roundtrip" in names
    assert "camera_enum" in names
    assert "excel_load" in names
    assert "printer_enum" in names
    assert "audio_init" in names
    assert "playwright_runtime" in names


# --- build_report / write_report ---


def test_build_report_marks_failure_and_lists_failed_names():
    report = build_report(
        [SelfCheckResult("a", True, ""), SelfCheckResult("b", False, "boom")]
    )
    assert report["ok"] is False
    assert report["failed"] == ["b"]
    assert report["env"] in ("python", "frozen")


def test_build_report_all_pass_marks_ok():
    report = build_report([SelfCheckResult("a", True, "")])
    assert report["ok"] is True
    assert report["failed"] == []


def test_write_report_creates_json_file(tmp_path):
    out = tmp_path / "nested" / "report.json"
    report = build_report([SelfCheckResult("a", True, "상세")])
    write_report(report, out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["results"] == [{"name": "a", "ok": True, "detail": "상세"}]


# --- run_self_check_cli ---


def test_self_check_cli_writes_report_and_returns_zero_on_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.self_check_service.run_self_checks",
        lambda: [SelfCheckResult("a", True, "")],
    )
    out = tmp_path / "out.json"
    assert run_self_check_cli(["--self-check", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["ok"] is True


def test_self_check_cli_returns_nonzero_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.self_check_service.run_self_checks",
        lambda: [SelfCheckResult("a", False, "실패")],
    )
    out = tmp_path / "out.json"
    assert run_self_check_cli(["--self-check", "--out", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["failed"] == ["a"]


def test_self_check_cli_defaults_out_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "services.self_check_service.run_self_checks",
        lambda: [SelfCheckResult("a", True, "")],
    )
    assert run_self_check_cli(["--self-check"]) == 0
    assert (tmp_path / "self_check_report.json").is_file()


# --- compare_reports ---


@pytest.fixture()
def compare():
    return _load_compare_module()


def _report(env, *results):
    return {
        "env": env,
        "ok": all(r["ok"] for r in results),
        "results": [dict(r) for r in results],
    }


def test_compare_passes_when_exe_matches_src(compare):
    src = _report("python", {"name": "imports", "ok": True, "detail": ""})
    exe = _report("frozen", {"name": "imports", "ok": True, "detail": ""})
    ok, messages = compare.compare_reports(src, exe)
    assert ok is True
    assert "파리티 통과" in messages[0]


def test_compare_fails_on_exe_regression(compare):
    src = _report(
        "python",
        {"name": "imports", "ok": True, "detail": ""},
        {"name": "qr_roundtrip", "ok": True, "detail": ""},
    )
    exe = _report(
        "frozen",
        {"name": "imports", "ok": True, "detail": ""},
        {"name": "qr_roundtrip", "ok": False, "detail": "zbar DLL 없음"},
    )
    ok, messages = compare.compare_reports(src, exe)
    assert ok is False
    assert any("회귀: qr_roundtrip" in m for m in messages)


def test_compare_fails_on_missing_check_in_exe(compare):
    src = _report(
        "python",
        {"name": "imports", "ok": True, "detail": ""},
        {"name": "excel_load", "ok": True, "detail": ""},
    )
    exe = _report("frozen", {"name": "imports", "ok": True, "detail": ""})
    ok, messages = compare.compare_reports(src, exe)
    assert ok is False
    assert any("excel_load" in m and "누락" in m for m in messages)


def test_compare_warns_not_fails_when_both_environments_fail(compare):
    src = _report("python", {"name": "camera_enum", "ok": False, "detail": "no cam"})
    exe = _report("frozen", {"name": "camera_enum", "ok": False, "detail": "no cam"})
    ok, messages = compare.compare_reports(src, exe)
    assert ok is True
    assert any("경고" in m for m in messages)


def test_compare_fails_on_empty_exe_report(compare):
    src = _report("python", {"name": "imports", "ok": True, "detail": ""})
    ok, messages = compare.compare_reports(src, _report("frozen"))
    assert ok is False


def test_main_returns_nonzero_for_unreadable_report(compare, tmp_path):
    missing = tmp_path / "missing.json"
    argv = sys.argv
    try:
        sys.argv = ["compare_self_check.py", "--src", str(missing), "--exe", str(missing)]
        assert compare.main() == 1
    finally:
        sys.argv = argv
