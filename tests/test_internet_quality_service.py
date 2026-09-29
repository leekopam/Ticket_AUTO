"""인터넷 연결 품질 측정 단위 테스트 — 프로브는 주입해 외부 요청 없이 검증한다."""
from __future__ import annotations

import unittest

import http.client

from services.internet_quality_service import (
    QUALITY_GOOD,
    QUALITY_OFFLINE,
    QUALITY_POOR,
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
        status2, *_ = summarize_quality((20,), 2, 8)  # 25% 손실
        self.assertEqual(status2, QUALITY_POOR)


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
        self.assertEqual(conn.request_calls, 2)

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
