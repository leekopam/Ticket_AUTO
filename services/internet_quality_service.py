"""PC의 인터넷 연결 품질 측정 — 외부 엔드포인트 응답시간·지터·손실률.

순수 계산(상태 판정)과 IO(HTTP 프로브)를 분리해 테스트 가능하게 한다.
네트워크 관리 탭이 주기 갱신 루프에서 호출한다.
"""
from __future__ import annotations

import http.client
import json
import statistics
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

# 연결 확인용 엔드포인트 — 해외 CDN 2곳 + 국내 경로 1곳.
# 단일 경로만 쓰면 그 경로의 장애를 인터넷 단절로 오판하므로 다중화한다.
PROBE_ENDPOINTS: tuple[str, ...] = (
    "https://www.gstatic.com/generate_204",
    "https://www.cloudflare.com/cdn-cgi/trace",
    "https://www.naver.com/",
)
PROBE_URL = PROBE_ENDPOINTS[0]  # 호환용 — 단일 경로 측정이 필요한 호출자
PROBE_TIMEOUT_SEC = 3.0
HISTORY_MAX = 12

# 상태 판정 경계 — keep-alive 측정(핸드셰이크 제외 순수 왕복시간) 기준
LATENCY_WARN_MS = 80
LATENCY_POOR_MS = 200
JITTER_WARN_MS = 20
# 손실률은 프로브 단위 — 3경로 중 1개 사망(33%)이 과반 불량으로 오판되지 않게
# 경고는 10%, 불량은 과반 실패(50%)부터 본다.
LOSS_WARN_PCT = 10.0
LOSS_POOR_PCT = 50.0
# 전체 경로가 이 횟수만큼 연속 무응답이면 단절·차단으로 본다
UNREACHABLE_CYCLES = 2
# 품질 이력 로그 상한 — 초과 시 최근 절반만 남긴다
LOG_MAX_BYTES = 64 * 1024

QUALITY_GOOD = "good"
QUALITY_WARN = "warn"
QUALITY_POOR = "poor"
QUALITY_OFFLINE = "offline"
QUALITY_UNREACHABLE = "unreachable"


def endpoint_label(url: str) -> str:
    """프로브 엔드포인트의 표시용 라벨 — 호스트명."""
    return urlsplit(url).hostname or url


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
    새 연결에서는 웜업 요청을 한 번 보내 모든 샘플이 웜 상태 RTT가 되게 한다.
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
        self._needs_warmup = False

    def _ensure_conn(self):
        if self._conn is None:
            self._conn = self._conn_factory(self._host, self._port, timeout=self._timeout_sec)
            self._conn.connect()
            self._needs_warmup = True
        return self._conn

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None
            self._needs_warmup = False

    def _round_trip_ms(self) -> int:
        conn = self._ensure_conn()
        if self._needs_warmup:
            # 핸드셰이크 직후 첫 요청은 버린다 — 측정값이 연결 수립 비용에 오염되지 않게
            conn.request("HEAD", self._path)
            conn.getresponse().read()
            self._needs_warmup = False
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
    failed_endpoints: tuple[str, ...] = field(default=())


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
        # 지터는 인접 차이의 중앙값 — 단발 스파이크(진입·복귀 2개 diff만 큼)가
        # 지속 흔들림으로 오인되지 않게, 판정도 표시값과 같은 중앙값을 쓴다
        jitter_ms = int(round(statistics.median(diffs)))
        jittery = jitter_ms >= JITTER_WARN_MS
    if (loss_pct or 0) >= LOSS_POOR_PCT or latency_ms >= LATENCY_POOR_MS:
        status = QUALITY_POOR
    elif (loss_pct or 0) >= LOSS_WARN_PCT or latency_ms >= LATENCY_WARN_MS or jittery:
        status = QUALITY_WARN
    else:
        status = QUALITY_GOOD
    return status, latency_ms, jitter_ms, loss_pct


class InternetQualityMonitor:
    """측정 이력을 유지하는 롤링 모니터 — 탭이 열려 있을 때만 호출된다.

    사이클(= measure 1회)마다 모든 엔드포인트를 순서대로 측정하고,
    사이클 대표값(성공 샘플 중앙값)을 이력에 쌓는다 — 경로별 기준값 차이가
    지터로 오염되지 않게 사이클 단위로만 비교한다.
    """

    def __init__(
        self,
        probe: Callable[[], QualityProbeResult] | None = None,
        probes: list[tuple[str, Callable[[], QualityProbeResult]]] | None = None,
        *,
        history_max: int = HISTORY_MAX,
        log_path: str | None = None,
        log_max_bytes: int = LOG_MAX_BYTES,
    ) -> None:
        if probes is not None:
            self._probes = list(probes)
        elif probe is not None:
            self._probes = [("기본 경로", probe)]
        else:
            # 기본은 keep-alive 다중 프로브 — 콜드 측정·단일 경로 장애가 오판을 만든다
            self._probes = [
                (endpoint_label(url), KeepAliveProbe(url)) for url in PROBE_ENDPOINTS
            ]
        self._history_max = history_max
        self._latencies: list[int] = []  # 사이클 대표 응답시간(중앙값) 이력
        # 프로브별 성공/실패도 롤링 — 과거 장애가 손실률에 영구 반영되지 않게 한다
        self._outcomes: list[bool] = []
        self._dead_cycles = 0
        self._log_path = Path(log_path) if log_path else None
        self._log_max_bytes = log_max_bytes

    def measure(self) -> InternetQualityState:
        """모든 경로를 1회씩 측정하고 최신 품질 상태를 반환한다."""
        results: list[tuple[str, QualityProbeResult]] = []
        for label, probe in self._probes:
            try:
                result = probe()
            except Exception:  # 프로브 내부 오류가 측정 루프를 죽이지 않게
                result = QualityProbeResult(ok=False, latency_ms=None)
            results.append((label, result))

        ok_latencies = [r.latency_ms for _, r in results if r.ok and r.latency_ms is not None]
        failed = tuple(label for label, r in results if not (r.ok and r.latency_ms is not None))
        self._outcomes.extend(r.ok and r.latency_ms is not None for _, r in results)
        outcomes_cap = self._history_max * len(self._probes)
        self._outcomes = self._outcomes[-outcomes_cap:]

        if ok_latencies:
            cycle_ms = int(round(statistics.median(ok_latencies)))
            self._latencies.append(cycle_ms)
            self._latencies = self._latencies[-self._history_max:]
            self._dead_cycles = 0
        else:
            self._dead_cycles += 1

        status, latency_ms, jitter_ms, loss_pct = summarize_quality(
            tuple(self._latencies),
            sum(1 for ok in self._outcomes if not ok),
            len(self._outcomes),
        )
        if self._dead_cycles >= UNREACHABLE_CYCLES:
            # 전체 경로가 연속 무응답 — 단일 경로 장애와 인터넷 단절·차단을 구분한다
            status = QUALITY_UNREACHABLE
        state = InternetQualityState(
            status=status,
            latency_ms=latency_ms,
            jitter_ms=jitter_ms,
            loss_pct=loss_pct,
            history=tuple(self._latencies),
            measured_at=_format_now(),
            failed_endpoints=failed,
        )
        self._append_log(state, results)
        return state

    def _append_log(
        self,
        state: InternetQualityState,
        results: list[tuple[str, QualityProbeResult]],
    ) -> None:
        """측정 결과를 JSONL로 영속화 — 로그 실패는 측정을 깨지 않게 무시한다."""
        if self._log_path is None:
            return
        try:
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": state.status,
                "latency_ms": state.latency_ms,
                "jitter_ms": state.jitter_ms,
                "loss_pct": state.loss_pct,
                "endpoints": {
                    label: {"ok": r.ok, "ms": r.latency_ms} for label, r in results
                },
            }
            path = self._log_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > self._log_max_bytes:
                lines = path.read_text(encoding="utf-8").splitlines()
                kept = "\n".join(lines[len(lines) // 2:])
                path.write_text(kept + ("\n" if kept else ""), encoding="utf-8")
            with path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
