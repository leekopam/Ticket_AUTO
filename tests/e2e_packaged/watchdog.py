"""패키징 exe의 '응답 없음'·비정상 상태를 감시하는 watchdog.

- IsHungAppWindow: 작업 관리자의 '응답 없음' 판정과 동일한 규칙
  (메시지 펌프가 내부 타임아웃(약 5초) 동안 PeekMessage를 호출하지 않음).
- SendMessageTimeout(WM_NULL): 지정 시간 안에 메시지 펌프가 응답하는지 핑.
- psutil: RSS·핸들 수 시계열로 누수 추적 (검증된 구현 재사용).
  창 판정 API는 psutil 범위 밖이라 user32만 ctypes로 호출한다.
"""
from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field
import threading

import psutil

if hasattr(ctypes, "windll"):
    _user32 = ctypes.windll.user32
else:  # pragma: no cover - 비Windows 실행 방어
    _user32 = None

WM_NULL = 0x0000
SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
SMTO_ERRORONEXIT = 0x0020


def is_window_hung(hwnd: int) -> bool:
    """Windows의 '응답 없음' 규칙으로 창이 멈췄는지 반환한다."""
    if not hwnd:
        return False
    return bool(_user32.IsHungAppWindow(hwnd))


def probe_window(hwnd: int, timeout_ms: int = 1000) -> bool:
    """WM_NULL 핑이 timeout 안에 처리되면 True(응답 중)를 반환한다."""
    if not hwnd:
        return False
    result = ctypes.c_size_t(0)  # lpdwResult는 DWORD_PTR(64비트에서 8바이트)
    ok = _user32.SendMessageTimeoutW(
        hwnd,
        WM_NULL,
        0,
        0,
        SMTO_ABORTIFHUNG | SMTO_BLOCK | SMTO_ERRORONEXIT,
        timeout_ms,
        ctypes.byref(result),
    )
    return bool(ok)


@dataclass(frozen=True)
class ProcessSample:
    """프로세스 자원 사용량 스냅샷."""

    timestamp: float
    rss_bytes: int
    peak_rss_bytes: int
    handle_count: int


def sample_process(pid: int) -> ProcessSample | None:
    """RSS/핸들 수를 수집한다. 프로세스가 종료됐으면 None."""
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return None
        mem = proc.memory_info()
        return ProcessSample(
            timestamp=time.monotonic(),
            rss_bytes=int(mem.rss),
            peak_rss_bytes=int(getattr(mem, "peak_wset", 0)),
            handle_count=int(proc.num_handles()),
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


@dataclass
class WatchdogEvent:
    """감지된 이상 이벤트."""

    kind: str  # "hung" | "probe_timeout" | "process_dead"
    timestamp: float
    detail: str = ""


@dataclass
class WatchdogReport:
    """감시 종료 후 요약."""

    events: list[WatchdogEvent] = field(default_factory=list)
    samples: list[ProcessSample] = field(default_factory=list)
    watched_seconds: float = 0.0

    @property
    def healthy(self) -> bool:
        return not self.events

    @property
    def rss_growth_bytes(self) -> int:
        if len(self.samples) < 2:
            return 0
        return self.samples[-1].rss_bytes - self.samples[0].rss_bytes

    def summary_lines(self) -> list[str]:
        lines = [
            f"watched={self.watched_seconds:.1f}s events={len(self.events)} "
            f"samples={len(self.samples)} rss_growth={self.rss_growth_bytes / 1024 / 1024:.1f}MB"
        ]
        for event in self.events:
            lines.append(f"  {event.kind} t={event.timestamp:.1f}s {event.detail}")
        return lines


class ResponsivenessWatchdog:
    """창 메시지 펌프와 프로세스 자원을 백그라운드에서 폴링한다.

    hwnd가 없으면 프로세스 감시만 한다. 창이 늦게 생기는 경우
    `set_hwnd`로 나중에 연결할 수 있다.
    """

    def __init__(
        self,
        pid: int,
        *,
        hwnd: int = 0,
        interval_sec: float = 0.5,
        probe_timeout_ms: int = 1000,
    ) -> None:
        self._pid = pid
        self._hwnd = hwnd
        self._interval = interval_sec
        self._probe_timeout = probe_timeout_ms
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._report = WatchdogReport()
        self._started_at = 0.0

    def set_hwnd(self, hwnd: int) -> None:
        self._hwnd = hwnd

    def start(self) -> None:
        if self._thread is not None:
            return
        self._started_at = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="watchdog", daemon=True
        )
        self._thread.start()

    def stop(self) -> WatchdogReport:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._report.watched_seconds = time.monotonic() - self._started_at
        return self._report

    @property
    def report(self) -> WatchdogReport:
        return self._report

    def _loop(self) -> None:
        hung_since: float | None = None
        while not self._stop.is_set():
            sample = sample_process(self._pid)
            if sample is None:
                self._report.events.append(
                    WatchdogEvent(
                        "process_dead",
                        time.monotonic() - self._started_at,
                        "프로세스 핸들을 열 수 없음",
                    )
                )
                return
            self._report.samples.append(sample)

            if self._hwnd:
                if is_window_hung(self._hwnd):
                    hung_since = hung_since or time.monotonic()
                else:
                    hung_since = None
                # OS 규칙(5초 미펌프)보다 빠른 1초 핑으로 짧은 멈춤도 잡는다.
                if not probe_window(self._hwnd, self._probe_timeout):
                    if hung_since is None:
                        hung_since = time.monotonic()
                if hung_since is not None:
                    already = any(e.kind == "hung" for e in self._report.events)
                    if not already:
                        self._report.events.append(
                            WatchdogEvent(
                                "hung",
                                hung_since - self._started_at,
                                f"hwnd=0x{self._hwnd:X} "
                                f"{time.monotonic() - hung_since:.1f}s 미응답",
                            )
                        )
            self._stop.wait(self._interval)

    def assert_healthy(self) -> None:
        """hang/사망 이벤트가 있으면 AssertionError를 발생시킨다."""
        if self._report.healthy:
            return
        raise AssertionError(
            "응답 없음/비정상 감지:\n" + "\n".join(self._report.summary_lines())
        )
