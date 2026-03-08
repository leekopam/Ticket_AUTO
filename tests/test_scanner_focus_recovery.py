"""Legacy scanner state contract tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from views.scanner_view import (
    ScannerView,
    build_qr_decode_candidates,
    compute_focus_metric,
    decide_focus_recovery_action,
    should_enable_roi_recovery,
)


class ScannerLegacyStateContractTest(unittest.TestCase):
    def test_focus_recovery_action_is_disabled_in_legacy_mode(self) -> None:
        action = decide_focus_recovery_action(
            now=101.3,
            scanning_enabled=True,
            auth_ready=True,
            scan_enabled_since=100.0,
            last_decode_success_at=100.0,
            last_focus_pulse_at=0.0,
            last_manual_focus_step_at=0.0,
            focus_blur_streak=0,
        )
        self.assertEqual(action, "none")

    def test_roi_recovery_is_disabled_in_legacy_mode(self) -> None:
        self.assertFalse(
            should_enable_roi_recovery(
                now=102.1,
                scanning_enabled=True,
                auth_ready=True,
                scan_enabled_since=100.0,
                last_decode_success_at=100.0,
                exposure_mode="default",
            )
        )

    def test_build_qr_decode_candidates_returns_raw_frame_only(self) -> None:
        frame = np.full((240, 320, 3), 160, dtype=np.uint8)
        candidates = build_qr_decode_candidates(frame, enable_roi_recovery=False)
        self.assertEqual(len(candidates), 1)
        self.assertIs(candidates[0], frame)

    def test_compute_focus_metric_still_handles_invalid_input(self) -> None:
        self.assertEqual(compute_focus_metric(None), 0.0)
        self.assertEqual(compute_focus_metric(np.array([], dtype=np.uint8)), 0.0)

    def test_set_scanning_enabled_restores_ready_message(self) -> None:
        scanner = ScannerView()
        scanner.set_status_message("처리 중...")
        scanner.set_scanning_enabled(True)
        self.assertEqual(scanner._runtime_status_message, "준비됨")

    def test_same_qr_requires_rearm_before_repeat_emit(self) -> None:
        scanner = ScannerView()
        self.assertTrue(scanner._can_emit_qr("qr://same"))
        self.assertFalse(scanner._can_emit_qr("qr://same"))

        for _ in range(scanner._rearm_missing_frames):
            scanner._record_missing_qr_frame()

        self.assertTrue(scanner._can_emit_qr("qr://same"))

    def test_decode_qr_reads_first_pyzbar_result_from_raw_frame(self) -> None:
        frame = np.full((20, 20, 3), 120, dtype=np.uint8)
        decoded = [SimpleNamespace(data=b"https://example.com/qr")]

        with patch("views.scanner_view.decode", return_value=decoded) as decode_mock:
            qr_url = ScannerView._decode_qr(frame)

        self.assertEqual(qr_url, "https://example.com/qr")
        decode_mock.assert_called_once_with(frame)

    def test_decode_qr_returns_none_when_raw_frame_decode_misses(self) -> None:
        frame = np.full((20, 20, 3), 120, dtype=np.uint8)

        with patch("views.scanner_view.decode", return_value=[]) as decode_mock:
            qr_url = ScannerView._decode_qr(frame)

        self.assertIsNone(qr_url)
        decode_mock.assert_called_once_with(frame)

    def test_decode_qr_returns_none_when_pyzbar_raises(self) -> None:
        frame = np.full((20, 20, 3), 120, dtype=np.uint8)

        with patch("views.scanner_view.decode", side_effect=RuntimeError("decode failed")) as decode_mock:
            qr_url = ScannerView._decode_qr(frame)

        self.assertIsNone(qr_url)
        decode_mock.assert_called_once_with(frame)

    def test_reset_focus_recovery_timers_is_safe_noop(self) -> None:
        scanner = ScannerView()
        self.assertIsNone(scanner._reset_focus_recovery_timers())


if __name__ == "__main__":
    unittest.main()
