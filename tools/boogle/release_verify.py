"""L4 Release 검증을 Boogle 실행 계약으로 감싸는 래퍼.

Boogle command 어댑터는 .ps1 실행 파일을 거부하므로 이 스크립트가
`verify_release.ps1 -Release`를 서브프로세스로 실행하고, 결과를
BOOGLE_* 환경변수 계약(metric/artifact manifest)으로 변환한다.

수집 evidence:
- artifacts/test-results/<ts>/ 의 summary.md, pytest.xml, exe-smoke.log, 로그 tail
- metric: release/verify_ok, release/duration_s, release/pytest_* 등
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = PROJECT_ROOT / "scripts" / "qa" / "verify_release.ps1"
RESULTS_ROOT = PROJECT_ROOT / "artifacts" / "test-results"
def _find_powershell() -> str:
    """Windows PowerShell 실행 파일 경로를 반환한다(실제 설치 폴더는 v1.0)."""
    candidates = [
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    resolved = shutil.which("powershell") or shutil.which("powershell.exe")
    if resolved:
        return resolved
    raise FileNotFoundError("powershell.exe를 찾을 수 없습니다")

# 봉인 대상 로그의 최대 크기(빌드 로그는 tail만 남긴다)
_MAX_SEAL_LOG_BYTES = 256 * 1024


def _artifact_kind(file_name: str) -> str:
    ext = Path(file_name).suffix.lower()
    return {
        ".png": "capture", ".jpg": "capture", ".jpeg": "capture",
        ".webp": "capture", ".webm": "video", ".mp4": "video",
        ".zip": "trace", ".ndjson": "trace",
    }.get(ext, "output")


def _latest_results_dir() -> Path | None:
    if not RESULTS_ROOT.exists():
        return None
    dirs = [p for p in RESULTS_ROOT.iterdir() if p.is_dir()]
    return max(dirs, key=lambda p: p.name) if dirs else None


def _seal_results(results_dir: Path, artifacts_dir: Path) -> list[str]:
    """검증 산출물을 artifacts 디렉터리로 복사하고 파일명 목록을 반환한다."""
    sealed: list[str] = []
    for name in ("summary.md", "pytest.xml", "exe-smoke.log"):
        src = results_dir / name
        if src.is_file():
            shutil.copyfile(src, artifacts_dir / name)
            sealed.append(name)
    # 대형 로그는 tail만 보존한다.
    for name in ("pytest.log", "build.log", "exe-smoke-runner.log"):
        src = results_dir / name
        if not src.is_file():
            continue
        data = src.read_bytes()
        tail = data[-_MAX_SEAL_LOG_BYTES:]
        target = artifacts_dir / f"{Path(name).stem}-tail.log"
        target.write_bytes(tail)
        sealed.append(target.name)
    return sealed


def _metric(name: str, value: float, *, unit: str, direction: str,
            threshold_kind: str, threshold_value: float,
            aggregation: str = "single") -> dict:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "definitionVersion": "1",
        "aggregation": aggregation,
        "direction": direction,
        "threshold": {"kind": threshold_kind, "value": threshold_value},
    }


def main() -> int:
    metrics_path = os.environ.get("BOOGLE_METRICS_PATH")
    artifacts_path = os.environ.get("BOOGLE_ARTIFACTS_PATH")
    artifacts_dir = Path(
        os.environ.get("BOOGLE_ARTIFACTS_DIR", PROJECT_ROOT / "artifacts" / "l4-release")
    )
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    cmd = [
        _find_powershell(),
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(VERIFY_SCRIPT), "-Release",
    ]
    completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    duration_s = time.perf_counter() - started
    verify_ok = completed.returncode == 0

    results_dir = _latest_results_dir()
    sealed: list[str] = []
    if results_dir is not None:
        sealed = _seal_results(results_dir, artifacts_dir)
        print(f"[release_verify] sealed {len(sealed)} files from {results_dir.name}")

    metrics = [
        _metric(
            "release/verify_ok", 1.0 if verify_ok else 0.0,
            unit="bool", direction="higher",
            threshold_kind="absolute", threshold_value=1.0,
        ),
        _metric(
            "release/duration_s", round(duration_s, 2),
            unit="s", direction="lower",
            threshold_kind="relative_percent", threshold_value=100.0,
        ),
        _metric(
            "release/exe_smoke_ok",
            1.0 if (results_dir and (results_dir / "exe-smoke.log").is_file()) else 0.0,
            unit="bool", direction="higher",
            threshold_kind="absolute", threshold_value=1.0,
        ),
        _metric(
            "release/sealed_files", float(len(sealed)),
            unit="count", direction="higher",
            threshold_kind="absolute", threshold_value=0.0,
            aggregation="sum",
        ),
    ]
    if metrics_path:
        Path(metrics_path).write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if artifacts_path:
        files = sorted(p for p in artifacts_dir.iterdir() if p.is_file())
        manifest = {
            "artifacts": [
                {"fileName": p.name, "kind": _artifact_kind(p.name)} for p in files
            ]
        }
        Path(artifacts_path).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"[release_verify] verify_ok={verify_ok} duration={duration_s:.1f}s")
    return 0 if verify_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
