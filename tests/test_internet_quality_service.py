"""인터넷 연결 품질 측정 단위 테스트 — 프로브는 주입해 외부 요청 없이 검증한다."""
from __future__ import annotations

import unittest

import http.client

import json
import tempfile
import unittest
from pathlib import Path

import http.client

from services.internet_quality_service import (
    QUALITY_GOOD,
    QUALITY_OFFLINE,
    QUALITY_POOR,
    QUALITY_UNREACHABLE,
    QUALITY_WARN,
    InternetQualityMonitor,
    KeepAliveProbe,
    QualityProbeResult,
    summarize_quality,
)


class SummarizeQualityTest(unittest.TestCase):
    def test_no_attempts_is_offline(self) -> None:
        status, latency, jitter, loss = summarize_quality((), 0, 0)
        self.assertEqual(status, QUALITY_OFFLINE)
        self.assertIsNone(latency)
        self.assertIsNone(loss)

    def test_all_failed_is_offline_with_loss(self) -> None:
        status, latency, jitter, loss = summarize_quality((), 3, 3)
        self.assertEqual(status, QUALITY_OFFLINE)
        self.assertEqual(loss, 100.0)

    def test_good_connection(self) -> None:
        status, latency, jitter, loss = summarize_quality((20, 22, 24), 0, 3)
        self.assertEqual(status, QUALITY_GOOD)
        self.assertEqual(latency, 22)  # 윈도우 중앙값 — 단발 스파이크에 안정적
        self.assertEqual(jitter, 2)
        self.assertEqual(loss, 0.0)

    def test_latency_uses_median_not_last_sample(self) -> None:
        # 표본이 충분하면 마지막 샘플의 스파이크는 상태를 바꾸지 않는다
        status, latency, *_ = summarize_quality((30, 32, 31, 33, 300), 0, 5)
        self.assertEqual(status, QUALITY_GOOD)
        self.assertEqual(latency, 32)

    def test_sustained_high_jitter_still_warns(self) -> None:
        # 단발이 아니라 계속 흔들리면 지터 경고는 유지된다
        status, _latency, jitter, *_ = summarize_quality((30, 80, 35, 85, 40), 0, 5)
        self.assertEqual(status, QUALITY_WARN)
        self.assertGreaterEqual(jitter, 20)

    def test_single_spike_does_not_warn(self) -> None:
        # 스파이크 1회(19→60→22 복귀)는 큰 diff 2개를 만들지만 지속 흔들림이 아니다.
        # 지터 판정은 표시값(중앙값)과 같은 기준 — 표시 18ms인데 경고가 뜨는 모순 방지.
        status, latency, jitter, *_ = summarize_quality(
            (19, 22, 60, 24, 25, 23, 21, 26), 0, 8
        )
        self.assertEqual(status, QUALITY_GOOD)
        self.assertLess(jitter, 20)

    def test_warn_on_high_latency(self) -> None:
        status, *_ = summarize_quality((95,), 0, 1)
        self.assertEqual(status, QUALITY_WARN)

    def test_warn_on_small_loss(self) -> None:
        status, _latency, _jitter, loss = summarize_quality((20,), 1, 10)
        self.assertEqual(status, QUALITY_WARN)
        self.assertEqual(loss, 10.0)

    def test_poor_on_very_high_latency_or_loss(self) -> None:
        status, *_ = summarize_quality((250,), 0, 1)
        self.assertEqual(status, QUALITY_POOR)
        status2, *_ = summarize_quality((20,), 5, 10)  # 과반(50%) 손실
        self.assertEqual(status2, QUALITY_POOR)

    def test_partial_endpoint_loss_warns_not_poor(self) -> None:
        # 다중 경로 중 일부(33%)만 실패하면 경로 불량 경고지 인터넷 불량이 아니다
        status, *_ = summarize_quality((20,), 2, 6)
        self.assertEqual(status, QUALITY_WARN)


class InternetQualityMonitorTest(unittest.TestCase):
    def test_measure_accumulates_history(self) -> None:
        values = iter([10, 20, 30])
        monitor = InternetQualityMonitor(
            probe=lambda: QualityProbeResult(ok=True, latency_ms=next(values))
        )
        for _ in range(3):
            state = monitor.measure()
        self.assertEqual(state.history, (10, 20, 30))
        self.assertEqual(state.latency_ms, 20)
        self.assertEqual(state.status, QUALITY_GOOD)
        self.assertTrue(state.measured_at)

    def test_failures_count_as_loss_not_history(self) -> None:
        calls = iter([QualityProbeResult(True, 20), QualityProbeResult(False, None), QualityProbeResult(True, 24)])
        monitor = InternetQualityMonitor(probe=lambda: next(calls))
        monitor.measure()
        monitor.measure()
        state = monitor.measure()
        self.assertEqual(state.history, (20, 24))
        self.assertAlmostEqual(state.loss_pct, 33.3, places=1)

    def test_history_window_capped(self) -> None:
        monitor = InternetQualityMonitor(
            probe=lambda: QualityProbeResult(ok=True, latency_ms=10),
            history_max=5,
        )
        for _ in range(8):
            state = monitor.measure()
        self.assertEqual(len(state.history), 5)

    def test_multi_endpoint_cycle_median_and_failed_label(self) -> None:
        # 경로별 기준값이 달라도 사이클 대표값(중앙값)만 이력에 쌓인다
        monitor = InternetQualityMonitor(
            probes=[
                ("fast", lambda: QualityProbeResult(True, 10)),
                ("dead", lambda: QualityProbeResult(False, None)),
                ("slow", lambda: QualityProbeResult(True, 50)),
            ]
        )
        state = monitor.measure()
        self.assertEqual(state.history, (30,))  # (10, 50) 중앙값
        self.assertEqual(state.failed_endpoints, ("dead",))
        self.assertEqual(state.status, QUALITY_WARN)  # 1/3 경로 실패 → 경고
        state = monitor.measure()
        self.assertEqual(state.history, (30, 30))
        self.assertEqual(state.status, QUALITY_WARN)

    def test_unreachable_after_consecutive_dead_cycles(self) -> None:
        monitor = InternetQualityMonitor(
            probes=[
                ("a", lambda: QualityProbeResult(False, None)),
                ("b", lambda: QualityProbeResult(False, None)),
            ]
        )
        first = monitor.measure()
        self.assertNotEqual(first.status, QUALITY_UNREACHABLE)  # 1회 실패는 일시 장애로 간주
        second = monitor.measure()
        self.assertEqual(second.status, QUALITY_UNREACHABLE)
        self.assertEqual(second.failed_endpoints, ("a", "b"))

    def test_recovers_from_unreachable(self) -> None:
        alive = {"v": False}

        def flaky() -> QualityProbeResult:
            return QualityProbeResult(ok=alive["v"], latency_ms=20 if alive["v"] else None)

        monitor = InternetQualityMonitor(probes=[("x", flaky)])
        monitor.measure()
        self.assertEqual(monitor.measure().status, QUALITY_UNREACHABLE)
        alive["v"] = True
        # 복귀 직후엔 창에 남은 실패 때문에 unreachable은 아니지만 바로 good은 아니다
        self.assertNotEqual(monitor.measure().status, QUALITY_UNREACHABLE)
        for _ in range(12):  # 실패가 롤링 창(12)을 빠져나가면 양호로 회복
            state = monitor.measure()
        self.assertEqual(state.status, QUALITY_GOOD)

    def test_measure_writes_jsonl_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "q.jsonl"
            monitor = InternetQualityMonitor(
                probe=lambda: QualityProbeResult(ok=True, latency_ms=42),
                log_path=str(log),
            )
            monitor.measure()
            monitor.measure()
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            record = json.loads(lines[0])
            self.assertEqual(record["latency_ms"], 42)
            self.assertIn("기본 경로", record["endpoints"])
            self.assertIn("ts", record)

    def test_log_rotates_when_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "q.jsonl"
            monitor = InternetQualityMonitor(
                probe=lambda: QualityProbeResult(ok=True, latency_ms=42),
                log_path=str(log),
                log_max_bytes=600,
            )
            for _ in range(10):
                monitor.measure()
            self.assertLessEqual(log.stat().st_size, 600 + 200)  # 절단 후 새 레코드 한 줄 여유
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines)  # 최근 레코드는 남아 있다


class _FakeResponse:
    def read(self) -> bytes:
        return b""


class _FakeConn:
    """conn_factory 주입용 가짜 HTTPSConnection."""

    created = 0
    fail_requests = False  # True면 request에서 연결 절단 시뮬레이션

    def __init__(self, *args, **kwargs):
        type(self).created += 1
        self.connect_calls = 0
        self.request_calls = 0

    def connect(self) -> None:
        self.connect_calls += 1

    def request(self, method: str, path: str) -> None:
        self.request_calls += 1
        if type(self).fail_requests:
            raise http.client.RemoteDisconnected()

    def getresponse(self):
        return _FakeResponse()

    def close(self) -> None:
        pass


class KeepAliveProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        _FakeConn.created = 0
        _FakeConn.fail_requests = False

    def _probe(self) -> KeepAliveProbe:
        return KeepAliveProbe(conn_factory=_FakeConn)

    def test_reuses_connection_between_measurements(self) -> None:
        probe = self._probe()
        r1 = probe()
        r2 = probe()
        self.assertTrue(r1.ok and r2.ok)
        # 연결 생성+connect는 처음 한 번 — 이후 요청 구간만 측정한다
        self.assertEqual(_FakeConn.created, 1)
        conn = probe._conn
        self.assertEqual(conn.connect_calls, 1)
        # 첫 호출은 웜업+측정 2회, 이후는 측정 1회씩
        self.assertEqual(conn.request_calls, 3)

    def test_warmup_excluded_from_first_sample(self) -> None:
        # 새 연결의 첫 요청(핸드셰이크 직후)은 측정값이 아니라 웜업으로 버린다
        probe = self._probe()
        self.assertTrue(probe().ok)
        conn = probe._conn
        self.assertFalse(probe._needs_warmup)
        self.assertEqual(conn.request_calls, 2)

    def test_warmup_repeated_after_reconnect(self) -> None:
        probe = self._probe()
        self.assertTrue(probe().ok)
        probe._close()
        self.assertTrue(probe().ok)
        self.assertEqual(_FakeConn.created, 2)  # 재연결도 웜업 포함

    def test_reconnects_once_on_stale_connection(self) -> None:
        probe = self._probe()
        self.assertTrue(probe().ok)
        _FakeConn.fail_requests = True
        # 끊긴 연결로 실패 → 재연결해도 계속 실패하면 최종 실패
        r = probe()
        self.assertFalse(r.ok)
        self.assertIsNone(r.latency_ms)
        # 복구되면 다시 성공
        _FakeConn.fail_requests = False
        self.assertTrue(probe().ok)


if __name__ == "__main__":
    unittest.main()
