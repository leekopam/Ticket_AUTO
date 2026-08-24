"""패키징된 실행 파일의 조기 종료 여부를 검사한다."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path
from typing import Sequence


def _terminate_process_tree(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        process.terminate()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _write_outcome(log_path: Path, message: str) -> None:
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"\n[smoke] {message}\n")


def run_executable_smoke(
    command: Sequence[str],
    *,
    startup_seconds: float,
    log_path: Path,
    cwd: Path | None = None,
) -> tuple[bool, str]:
    """실행 파일이 지정 시간 동안 즉시 종료되지 않는지 확인한다."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not command:
        message = "No executable command was provided."
        _write_outcome(log_path, message)
        return False, message

    executable = Path(command[0])
    if not executable.is_file():
        message = f"Executable not found: {executable}"
        _write_outcome(log_path, message)
        return False, message

    if startup_seconds <= 0:
        message = "Startup observation time must be greater than zero."
        _write_outcome(log_path, message)
        return False, message

    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    working_directory = Path(cwd) if cwd is not None else executable.parent

    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            log_file.write(f"[smoke] command={' '.join(command)}\n")
            log_file.write(f"[smoke] cwd={working_directory}\n")
            log_file.flush()
            process = subprocess.Popen(
                list(command),
                cwd=working_directory,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )

            deadline = time.monotonic() + startup_seconds
            while time.monotonic() < deadline:
                return_code = process.poll()
                if return_code is not None:
                    message = f"Process exited during startup observation. Exit code: {return_code}"
                    log_file.write(f"[smoke] {message}\n")
                    return False, message
                time.sleep(min(0.1, max(0.01, startup_seconds / 10)))

            _terminate_process_tree(process)
    except OSError as exc:
        message = f"Failed to launch executable: {exc}"
        _write_outcome(log_path, message)
        return False, message

    message = f"Process remained alive for {startup_seconds:g} seconds."
    _write_outcome(log_path, message)
    return True, message


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="패키징된 Ticket_AUTO EXE 기동 검사")
    parser.add_argument("--exe", required=True, type=Path, help="검사할 EXE 경로")
    parser.add_argument(
        "--startup-seconds",
        type=float,
        default=10.0,
        help="즉시 종료 여부를 관찰할 시간",
    )
    parser.add_argument("--log-path", required=True, type=Path, help="실행 로그 저장 경로")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    success, message = run_executable_smoke(
        [str(args.exe.resolve())],
        startup_seconds=args.startup_seconds,
        log_path=args.log_path.resolve(),
        cwd=args.exe.resolve().parent,
    )
    print(message)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
