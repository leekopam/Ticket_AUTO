"""PC의 인터넷 연결 품질 측정 — 외부 엔드포인트 응답시간·지터·손실률.

순수 계산(상태 판정)과 IO(HTTP 프로브)를 분리해 테스트 가능하게 한다.
네트워크 관리 탭이 주기 갱신 루프에서 호출한다.
"""
from __future__ import annotations

import http.client
import statistics
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

# 연결 확인용으로 널리 쓰이는 204 응답 엔드포인트
PROBE_URL = "https://www.gstatic.com/generate_204"
PROBE_TIMEOUT_SEC = 3.0
HISTORY_MAX = 12

# 상태 판정 경계 — keep-alive 측정(핸드셰이크 제외 순수 왕복시간) 기준
LATENCY_WARN_MS = 80
LATENCY_POOR_MS = 200
JITTER_WARN_MS = 20
LOSS_WARN_PCT = 3.0

QUALITY_GOOD = "good"
QUALITY_WARN = "warn"
QUALITY_POOR = "poor"
QUALITY_OFFLINE = "offline"


@dataclass(frozen=True)
class QualityProbeResult:
    """프로브 1회 결과 — 실패 시 latency_ms는 None."""

    ok: bool
    latency_ms: int | None


def probe_once(
    url: str = PROBE_URL,
    *,
    timeout_sec: float = PROBE_TIMEOUT_SEC,
) -> QualityProbeResult:
    """외부 엔드포인트에 HEAD 요청을 보내 응답시간을 측정한다 (콜드 요청용 — DNS+TCP+TLS 포함)."""
    request = urllib.request.Request(url, method="HEAD")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec):
            pass
    except OSError:
        return QualityProbeResult(ok=False, latency_ms=None)
    latency_ms = int((time.monotonic() - started) * 1000)
    return QualityProbeResult(ok=True, latency_ms=latency_ms)


class KeepAliveProbe:
    """재사용 HTTPS 연결로 순수 요청 왕복시간을 측정하는 프로브.

    매번 DNS+TCP+TLS 핸드셰이크를 측정에 포함하면 정상 회선(40ms)에서도
    100ms 이상으로 관측돼 경고로 오판한다 — 연결을 유지하고 요청 구간만 잰다.
    서버가 유휴 연결을 끊은 경우 1회 재연결 후 재측정한다.
    """

    def __init__(
        self,
        url: str = PROBE_URL,
        *,
        timeout_sec: float = PROBE_TIMEOUT_SEC,
        conn_factory=None,
    ) -> None:
        parts = urlsplit(url)
        self._host = parts.hostname or "www.gstatic.com"
        self._port = parts.port or 443
        self._path = parts.path or "/"
        if parts.query:
            self._path += f"?{parts.query}"
        self._timeout_sec = timeout_sec
        self._conn_factory = conn_factory or http.client.HTTPSConnection
        self._conn = None

    def _ensure_conn(self):
        if self._conn is None:
            self._conn = self._conn_factory(self._host, self._port, timeout=self._timeout_sec)
            self._conn.connect()
        return self._conn

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def _round_trip_ms(self) -> int:
        conn = self._ensure_conn()
        started = time.monotonic()
        conn.request("HEAD", self._path)
        resp = conn.getresponse()
        resp.read()  # 응답을 끝까지 읽어야 연결이 재사용 가능해진다
        return int((time.monotonic() - started) * 1000)

    def __call__(self) -> QualityProbeResult:
        try:
            return QualityProbeResult(ok=True, latency_ms=self._round_trip_ms())
        except (OSError, http.client.HTTPException):
            # 유휴 연결 절단 등 — 재연결 후 1회 재측정 (재연결 자체는 측정에서 제외)
            self._close()
            try:
                return QualityProbeResult(ok=True, latency_ms=self._round_trip_ms())
            except (OSError, http.client.HTTPException):
                self._close()
                return QualityProbeResult(ok=False, latency_ms=None)


@dataclass(frozen=True)
class InternetQualityState:
    """네트워크 관리 탭 품질 카드에 표시할 상태."""

    status: str
    latency_ms: int | None
    jitter_ms: int | None
    loss_pct: float | None
    history: tuple[int, ...]
    measured_at: str


def _format_now() -> str:
    return time.strftime("%H:%M:%S")


def summarize_quality(
    latencies: tuple[int, ...],
    failures: int,
    attempts: int,
) -> tuple[str, int | None, int | None, float | None]:
    """표본에서 (상태, 응답시간, 지터, 손실률)을 계산한다. 측정 전이면 offline.

    응답시간은 최근 윈도우의 중앙값 — 단발 스파이크 한 번이 상태를 오염시키지 않게 한다.
    """
    if attempts <= 0:
        return QUALITY_OFFLINE, None, None, None
    loss_pct = round(failures / attempts * 100, 1)
    if not latencies:
        return QUALITY_OFFLINE, None, None, loss_pct
    latency_ms = int(round(statistics.median(latencies)))
    jitter_ms = None
    jittery = False
    if len(latencies) >= 2:
        diffs = [abs(b - a) for a, b in zip(latencies, latencies[1:])]
        # 지터도 중앙값 — 단발 스파이크가 평균을 오염시키지 않게 한다
        jitter_ms = int(round(statistics.median(diffs)))
        # 경고 판정은 지속 흔들림 기준 — 스파이크 1회(diffs 1건만 큼)는 무시한다
        jittery = sum(1 for d in diffs if d >= JITTER_WARN_MS) >= 2
    if (loss_pct or 0) >= 20 or latency_ms >= LATENCY_POOR_MS:
        status = QUALITY_POOR
    elif (loss_pct or 0) >= LOSS_WARN_PCT or latency_ms >= LATENCY_WARN_MS or jittery:
        status = QUALITY_WARN
    else:
        status = QUALITY_GOOD
    return status, latency_ms, jitter_ms, loss_pct


class InternetQualityMonitor:
    """측정 이력을 유지하는 롤링 모니터 — 탭이 열려 있을 때만 호출된다."""

    def __init__(
        self,
        probe: Callable[[], QualityProbeResult] | None = None,
        *,
        history_max: int = HISTORY_MAX,
    ) -> None:
        # 기본은 keep-alive 프로브 — 콜드 측정이면 핸드셰이크 비용이 지연으로 오계산된다
        self._probe = probe if probe is not None else KeepAliveProbe()
        self._history_max = history_max
        self._latencies: list[int] = []
        self._attempts = 0
        self._failures = 0

    def measure(self) -> InternetQualityState:
        """프로브 1회를 실행하고 최신 품질 상태를 반환한다."""
        result = self._probe()
        self._attempts += 1
        if result.ok and result.latency_ms is not None:
            self._latencies.append(result.latency_ms)
            self._latencies = self._latencies[-self._history_max:]
        else:
            self._failures += 1
        status, latency_ms, jitter_ms, loss_pct = summarize_quality(
            tuple(self._latencies), self._failures, self._attempts
        )
        return InternetQualityState(
            status=status,
            latency_ms=latency_ms,
            jitter_ms=jitter_ms,
            loss_pct=loss_pct,
            history=tuple(self._latencies),
            measured_at=_format_now(),
        )
