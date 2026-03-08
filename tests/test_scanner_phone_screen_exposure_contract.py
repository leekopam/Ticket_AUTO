"""Legacy scanner compatibility tests for removed exposure recovery hooks."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from views.scanner_view import ScannerView


class _TrackingExposureCapture:
    def __init__(self, *, supports_set: bool = True):
        self.supports_set = supports_set
        self.values: dict[int, float] = {}
        self.set_calls: list[tuple[int, float]] = []

    def set(self, prop: int, value: float) -> bool:
        self.set_calls.append((prop, value))
        if not self.supports_set:
            return False
        self.values[prop] = value
        return True

    def get(self, prop: int) -> float:
        return self.values.get(prop, 1.0)


class ScannerPhoneScreenExposureContractTest(unittest.TestCase):
    def test_phone_screen_recovery_hook_stays_in_default_mode(self) -> None:
        scanner = ScannerView()
        bright_frame = np.full((80, 80), 252, dtype=np.uint8)

        mode = scanner._update_phone_screen_recovery_state(bright_frame)

        self.assertEqual(mode, "default")
        self.assertEqual(scanner._active_exposure_mode, "default")
        self.assertIsNone(scanner._persistent_camera_status_message)

    def test_apply_exposure_mode_returns_false_in_legacy_mode(self) -> None:
        scanner = ScannerView()
        capture = _TrackingExposureCapture(supports_set=False)

        applied = scanner._apply_exposure_mode(capture, "phone_screen")

        self.assertFalse(applied)
        self.assertEqual(scanner._applied_exposure_mode, "default")

    def test_sync_exposure_mode_logs_without_qr_payload(self) -> None:
        scanner = ScannerView()
        scanner._active_exposure_mode = "phone_screen"
        scanner._last_emitted_qr = "https://example.com/private-qr"

        with patch("builtins.print") as print_mock:
            scanner._sync_exposure_mode()

        joined = " ".join(" ".join(str(arg) for arg in call.args) for call in print_mock.call_args_list)
        self.assertIn("CAMERA_EXPOSURE_PROFILE_UNSUPPORTED", joined)
        self.assertNotIn("private-qr", joined)
        self.assertNotIn("https://example.com", joined)


if __name__ == "__main__":
    unittest.main()
