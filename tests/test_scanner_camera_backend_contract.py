"""Legacy scanner camera and preview contract tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from views.scanner_view import ScannerView, build_preview_frame, split_capture_frame


class _FakeCapture:
    def __init__(self, *, opened: bool = True, frames: list[np.ndarray] | None = None, supports_set: bool = True):
        self._opened = opened
        self._frames = list(frames or [])
        self.released = False
        self.supports_set = supports_set
        self.set_calls: list[tuple[int, float]] = []

    def isOpened(self) -> bool:  # noqa: N802 - OpenCV compatibility
        return self._opened

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self._frames:
            return False, None
        frame = self._frames.pop(0)
        return True, frame

    def release(self) -> None:
        self.released = True

    def set(self, prop: int, value: float) -> bool:
        self.set_calls.append((prop, value))
        return self.supports_set


class _RaisingCapture(_FakeCapture):
    def __init__(self, error: Exception):
        super().__init__(frames=[])
        self._error = error

    def read(self) -> tuple[bool, np.ndarray | None]:
        raise self._error


class ScannerCameraBackendContractTest(unittest.TestCase):
    def test_open_camera_with_fallback_uses_default_videocapture_only(self) -> None:
        fake_cap = _FakeCapture(frames=[np.zeros((4, 4, 3), dtype=np.uint8)])

        with patch("views.scanner_view.cv2.VideoCapture", return_value=fake_cap) as video_capture_mock:
            cap, backend_name = ScannerView._open_camera_with_fallback(2)

        self.assertIs(cap, fake_cap)
        self.assertEqual(backend_name, "DEFAULT")
        video_capture_mock.assert_called_once_with(2)
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        if autofocus_prop is not None:
            self.assertIn((autofocus_prop, 1.0), fake_cap.set_calls)

    def test_open_camera_returns_none_when_device_cannot_open(self) -> None:
        fake_cap = _FakeCapture(opened=False)

        with patch("views.scanner_view.cv2.VideoCapture", return_value=fake_cap):
            cap, backend_name = ScannerView._open_camera_with_fallback(0)

        self.assertIsNone(cap)
        self.assertIsNone(backend_name)
        self.assertTrue(fake_cap.released)

    def test_split_capture_frame_returns_independent_raw_copies(self) -> None:
        frame = np.arange(3 * 5 * 7, dtype=np.uint8).reshape(5, 7, 3)
        preview_frame, decode_frame = split_capture_frame(frame)
        decode_frame[0, 0, 0] = 0

        self.assertFalse(np.shares_memory(preview_frame, frame))
        self.assertFalse(np.shares_memory(decode_frame, frame))
        self.assertFalse(np.shares_memory(preview_frame, decode_frame))
        self.assertEqual(preview_frame[0, 0, 0], frame[0, 0, 0])

    def test_build_preview_frame_returns_raw_copy_without_extra_processing(self) -> None:
        frame = np.full((60, 60, 3), 252, dtype=np.uint8)
        frame[20:40, 20:40] = 180

        preview_frame = build_preview_frame(frame)

        self.assertTrue(np.array_equal(preview_frame, frame))
        self.assertIsNot(preview_frame, frame)

    def test_capture_loop_recovers_after_opencv_read_exception(self) -> None:
        bad_cap = _RaisingCapture(cv2.error("boom"))
        good_cap = _FakeCapture(
            frames=[np.full((12, 12, 3), 120, dtype=np.uint8) for _ in range(2)]
        )
        received_frames: list[str] = []

        def on_frame_ready(b64_str: str) -> None:
            received_frames.append(b64_str)
            scanner._is_running = False

        scanner = ScannerView(on_frame_ready=on_frame_ready)
        scanner._cap = bad_cap
        scanner._camera_backend_name = "DEFAULT"
        scanner._is_running = True

        with (
            patch.object(scanner, "_open_camera_with_fallback", return_value=(good_cap, "DEFAULT")),
            patch("views.scanner_view.decode", return_value=[]),
        ):
            scanner._capture_loop()

        self.assertEqual(len(received_frames), 1)
        self.assertTrue(bad_cap.released)
        self.assertEqual(scanner._camera_backend_name, "DEFAULT")

    def test_capture_loop_skips_decode_when_auth_not_ready(self) -> None:
        fake_cap = _FakeCapture(frames=[np.full((12, 12, 3), 120, dtype=np.uint8)])

        def on_frame_ready(_: str) -> None:
            scanner._is_running = False

        scanner = ScannerView(on_frame_ready=on_frame_ready)
        scanner._cap = fake_cap
        scanner._is_running = True
        scanner.set_auth_ready(False)

        with patch("views.scanner_view.decode", return_value=[]) as decode_mock:
            scanner._capture_loop()

        decode_mock.assert_not_called()

    def test_capture_loop_skips_decode_when_scanning_is_disabled(self) -> None:
        fake_cap = _FakeCapture(frames=[np.full((12, 12, 3), 120, dtype=np.uint8)])

        def on_frame_ready(_: str) -> None:
            scanner._is_running = False

        scanner = ScannerView(on_frame_ready=on_frame_ready)
        scanner._cap = fake_cap
        scanner._is_running = True
        scanner.set_auth_ready(True)
        scanner.set_scanning_enabled(False)

        with patch("views.scanner_view.decode", return_value=[]) as decode_mock:
            scanner._capture_loop()

        decode_mock.assert_not_called()

    def test_capture_loop_queues_qr_and_switches_to_processing(self) -> None:
        fake_cap = _FakeCapture(frames=[np.full((12, 12, 3), 120, dtype=np.uint8)])

        def on_frame_ready(_: str) -> None:
            scanner._is_running = False

        scanner = ScannerView(on_frame_ready=on_frame_ready)
        scanner._cap = fake_cap
        scanner._is_running = True
        scanner.set_auth_ready(True)

        with patch("views.scanner_view.decode", return_value=[SimpleNamespace(data=b"qr://ready")]):
            scanner._capture_loop()

        self.assertEqual(scanner.get_next_qr(timeout_sec=0.01), "qr://ready")
        self.assertFalse(scanner._is_scanning_enabled)
        self.assertEqual(scanner._runtime_status_message, "처리 중...")

    def test_enable_autofocus_returns_false_when_property_is_unsupported(self) -> None:
        fake_cap = _FakeCapture(supports_set=False)

        applied = ScannerView._enable_autofocus(fake_cap)

        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        if autofocus_prop is None:
            self.assertFalse(applied)
        else:
            self.assertFalse(applied)
            self.assertIn((autofocus_prop, 1.0), fake_cap.set_calls)

    def test_set_auth_ready_re_enables_webcam_autofocus_when_camera_exists(self) -> None:
        fake_cap = _FakeCapture()
        scanner = ScannerView()
        scanner._cap = fake_cap

        scanner.set_auth_ready(True)

        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        if autofocus_prop is None:
            self.assertEqual(fake_cap.set_calls, [])
        else:
            self.assertEqual(fake_cap.set_calls[-1], (autofocus_prop, 1.0))

    def test_set_scanning_enabled_re_enables_webcam_autofocus_when_camera_exists(self) -> None:
        fake_cap = _FakeCapture()
        scanner = ScannerView()
        scanner._cap = fake_cap
        scanner._runtime_status_message = "처리 중..."

        scanner.set_scanning_enabled(True)

        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        if autofocus_prop is None:
            self.assertEqual(fake_cap.set_calls, [])
        else:
            self.assertEqual(fake_cap.set_calls[-1], (autofocus_prop, 1.0))


if __name__ == "__main__":
    unittest.main()
