"""소스 실행과 패키징 exe의 self-check 리포트를 비교해 기능 동치를 검증한다.

판정:
- src에서 통과한 기능이 exe에서 실패하면 회귀(FAIL)
- exe 리포트에 검사 항목이 빠지면 패키징 누락(FAIL)
- src도 실패한 항목은 환경 한계로 간주해 경고만 남긴다
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_report(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"리포트를 읽을 수 없습니다: {path} ({exc})") from exc


def compare_reports(src_report: dict, exe_report: dict) -> tuple[bool, list[str]]:
    """(통과 여부, 판정 메시지 목록)을 반환한다."""
    src = {r["name"]: r for r in src_report.get("results", [])}
    exe = {r["name"]: r for r in exe_report.get("results", [])}
    messages: list[str] = []
    ok = True

    missing_in_exe = sorted(set(src) - set(exe))
    extra_in_exe = sorted(set(exe) - set(src))
    if missing_in_exe:
        ok = False
        messages.append(f"exe에서 검사 누락: {', '.join(missing_in_exe)}")
    if extra_in_exe:
        messages.append(f"exe에만 존재하는 검사: {', '.join(extra_in_exe)}")

    for name in sorted(set(src) & set(exe)):
        src_ok = bool(src[name].get("ok"))
        exe_ok = bool(exe[name].get("ok"))
        if src_ok and not exe_ok:
            ok = False
            messages.append(
                f"회귀: {name} — src OK / exe FAIL ({exe[name].get('detail', '')})"
            )
        elif not src_ok and not exe_ok:
            messages.append(
                f"경고: {name} — 두 환경 모두 실패(환경 한계 가능) ({exe[name].get('detail', '')})"
            )
        elif not src_ok and exe_ok:
            messages.append(f"참고: {name} — src FAIL / exe OK")
        else:
            messages.append(f"정상: {name}")

    if not src:
        ok = False
        messages.append("src 리포트에 검사 결과가 없습니다")
    if not exe:
        ok = False
        messages.append("exe 리포트에 검사 결과가 없습니다")

    summary = "파리티 통과" if ok else "파리티 불일치"
    messages.insert(0, f"{summary} (src env={src_report.get('env')}, exe env={exe_report.get('env')})")
    return ok, messages


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="self-check 소스↔exe 파리티 비교")
    parser.add_argument("--src", required=True, type=Path, help="소스 실행 리포트 JSON")
    parser.add_argument("--exe", required=True, type=Path, help="패키징 exe 리포트 JSON")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        src_report = _load_report(args.src)
        exe_report = _load_report(args.exe)
    except ValueError as exc:
        print(str(exc))
        return 1

    ok, messages = compare_reports(src_report, exe_report)
    for message in messages:
        print(f"[parity] {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
