"""Legacy scanner camera and preview contract tests."""
from __future__ import annotations

import unittest
import time
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from PIL import ImageFont

from views.scanner_view import (
    FocusCapability,
    ScannerView,
    _wrap_status_message,
    apply_focus_mode,
    build_preview_frame,
    detect_focus_capability,
    split_capture_frame,
)


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


class _FailThenFrameCapture(_FakeCapture):
    def __init__(self, *, fail_count: int, frame: np.ndarray):
        super().__init__(frames=[])
        self._remaining_failures = fail_count
        self._frame = frame

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            return False, None
        return True, self._frame.copy()


class _FocusAwareCapture(_FakeCapture):
    def __init__(self, prop_results: dict[int, bool]):
        super().__init__(supports_set=False)
        self._prop_results = dict(prop_results)

    def set(self, prop: int, value: float) -> bool:
        self.set_calls.append((prop, value))
        return self._prop_results.get(prop, False)


class ScannerCameraBackendContractTest(unittest.TestCase):
    def test_draw_status_ascii_fallback_renders_visible_status_badge(self) -> None:
        scanner = ScannerView()
        frame = np.zeros((80, 180, 3), dtype=np.uint8)

        scanner._draw_status(
            frame,
            message="READY",
            scanning_enabled=True,
            auth_ready=True,
            status_font=None,
        )

        self.assertGreater(int(frame[:60, :, :].sum()), 0)

    def test_draw_status_with_pil_font_renders_visible_bar(self) -> None:
        scanner = ScannerView()
        frame = np.zeros((80, 180, 3), dtype=np.uint8)

        scanner._draw_status(
            frame,
            message="READY",
            scanning_enabled=True,
            auth_ready=True,
            status_font=ImageFont.load_default(),
        )

        self.assertGreater(int(frame[:60, :, :].sum()), 0)

    def test_draw_status_with_pil_font_long_message_renders_within_bar(self) -> None:
        scanner = ScannerView()
        frame = np.zeros((120, 360, 3), dtype=np.uint8)

        scanner._draw_status(
            frame,
            message="브라우저 요청 실패: Target page closed",
            scanning_enabled=False,
            auth_ready=True,
            status_font=ImageFont.load_default(),
        )

        # 38px 바 영역에 렌더링됨
        self.assertGreater(int(frame[:84, :, :].sum()), 0)

    def test_draw_status_ascii_fallback_renders_within_bar(self) -> None:
        scanner = ScannerView()
        frame = np.zeros((120, 360, 3), dtype=np.uint8)

        scanner._draw_status(
            frame,
            message="BROWSER REQUEST FAIL",
            scanning_enabled=False,
            auth_ready=True,
            status_font=None,
        )

        # 38px 바 영역에 렌더링됨
        self.assertGreater(int(frame[:84, :, :].sum()), 0)

    def test_wrap_status_message_wraps_long_korean_text_in_ascii_fallback(self) -> None:
        lines = _wrap_status_message(
            "이미 처리되었거나 중복으로 스캔된 주문일 수 있습니다",
            max_width=320,
            status_font=None,
        )

        self.assertGreaterEqual(len(lines), 2)

    def test_open_camera_with_fallback_uses_default_videocapture_only(self) -> None:
        fake_cap = _FakeCapture(frames=[np.zeros((4, 4, 3), dtype=np.uint8)])

        with patch("views.scanner_view.cv2.VideoCapture", return_value=fake_cap) as video_capture_mock:
            cap, backend_name = ScannerView._open_camera_with_fallback(2)

        self.assertIs(cap, fake_cap)
        self.assertEqual(backend_name, "DEFAULT")
        # DirectShow 백엔드가 우선 시도된다
        video_capture_mock.assert_called_once_with(2, cv2.CAP_DSHOW)
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        if autofocus_prop is None:
            self.assertEqual(fake_cap.set_calls, [])
        else:
            self.assertEqual(fake_cap.set_calls, [])

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

    def test_capture_loop_does_not_reopen_camera_for_transient_single_read_failure(self) -> None:
        fake_cap = _FailThenFrameCapture(
            fail_count=1,
            frame=np.full((12, 12, 3), 120, dtype=np.uint8),
        )
        received_frames: list[str] = []

        def on_frame_ready(_: str) -> None:
            received_frames.append("frame")
            scanner._is_running = False

        scanner = ScannerView(on_frame_ready=on_frame_ready)
        scanner._cap = fake_cap
        scanner._is_running = True
        scanner.set_auth_ready(True)

        with (
            patch.object(scanner, "_attempt_camera_reopen", wraps=scanner._attempt_camera_reopen) as reopen_mock,
            patch("views.scanner_view.decode", return_value=[]),
        ):
            scanner._capture_loop()

        self.assertEqual(received_frames, ["frame"])
        reopen_mock.assert_not_called()

    def test_capture_loop_does_not_reopen_camera_during_initial_warmup_failures(self) -> None:
        fake_cap = _FailThenFrameCapture(
            fail_count=12,
            frame=np.full((12, 12, 3), 120, dtype=np.uint8),
        )
        received_frames: list[str] = []

        def on_frame_ready(_: str) -> None:
            received_frames.append("frame")
            scanner._is_running = False

        scanner = ScannerView(on_frame_ready=on_frame_ready)
        scanner._cap = fake_cap
        scanner._is_running = True
        scanner.set_auth_ready(True)
        scanner._camera_warmup_until = time.monotonic() + 30.0

        with (
            patch.object(scanner, "_attempt_camera_reopen", wraps=scanner._attempt_camera_reopen) as reopen_mock,
            patch("views.scanner_view.decode", return_value=[]),
        ):
            scanner._capture_loop()

        self.assertEqual(received_frames, ["frame"])
        reopen_mock.assert_not_called()

    def test_capture_loop_does_not_reopen_camera_for_brief_stall_after_recent_success(self) -> None:
        fake_cap = _FailThenFrameCapture(
            fail_count=12,
            frame=np.full((12, 12, 3), 120, dtype=np.uint8),
        )
        received_frames: list[str] = []

        def on_frame_ready(_: str) -> None:
            received_frames.append("frame")
            scanner._is_running = False

        scanner = ScannerView(on_frame_ready=on_frame_ready)
        scanner._cap = fake_cap
        scanner._is_running = True
        scanner.set_auth_ready(True)
        scanner._camera_warmup_until = 0.0
        scanner._last_successful_frame_at = time.monotonic()

        with (
            patch.object(scanner, "_attempt_camera_reopen", wraps=scanner._attempt_camera_reopen) as reopen_mock,
            patch("views.scanner_view.decode", return_value=[]),
        ):
            scanner._capture_loop()

        self.assertEqual(received_frames, ["frame"])
        reopen_mock.assert_not_called()

    def test_capture_loop_reopens_camera_after_sustained_stall(self) -> None:
        fake_cap = _FailThenFrameCapture(
            fail_count=999,
            frame=np.full((12, 12, 3), 120, dtype=np.uint8),
        )

        scanner = ScannerView()
        scanner._cap = fake_cap
        scanner._is_running = True
        scanner.set_auth_ready(True)
        scanner._camera_warmup_until = 0.0
        scanner._last_successful_frame_at = time.monotonic() - 30.0

        def _stop_after_reopen(*args, **kwargs) -> bool:
            scanner._is_running = False
            return False

        with (
            patch.object(scanner, "_attempt_camera_reopen", side_effect=_stop_after_reopen) as reopen_mock,
            patch("views.scanner_view.decode", return_value=[]),
        ):
            scanner._capture_loop()

        reopen_mock.assert_called_once()

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
            self.assertEqual(fake_cap.set_calls, [(autofocus_prop, 1.0)])

    def test_detect_focus_capability_does_not_force_manual_focus_probe(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        prop_results = {
            prop: True
            for prop in (autofocus_prop, focus_prop)
            if prop is not None
        }
        fake_cap = _FocusAwareCapture(prop_results)

        capability = detect_focus_capability(fake_cap)

        self.assertEqual(capability.autofocus_supported, autofocus_prop is not None)
        self.assertEqual(capability.manual_focus_supported, focus_prop is not None)
        if autofocus_prop is not None:
            self.assertFalse(any(prop == autofocus_prop for prop, _ in fake_cap.set_calls))
        if focus_prop is not None:
            self.assertFalse(any(prop == focus_prop for prop, _ in fake_cap.set_calls))

    def test_set_auth_ready_reapplies_manual_focus_when_manual_mode_is_active(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        prop_results = {
            prop: True
            for prop in (autofocus_prop, focus_prop)
            if prop is not None
        }
        fake_cap = _FocusAwareCapture(prop_results)
        scanner = ScannerView()
        scanner._cap = fake_cap
        scanner._focus_mode = "manual"
        scanner._focus_capability = FocusCapability(
            autofocus_supported=autofocus_prop is not None,
            manual_focus_supported=focus_prop is not None,
        )
        scanner.set_manual_focus_value(5.0)
        fake_cap.set_calls.clear()

        scanner.set_auth_ready(True)

        if focus_prop is None:
            if autofocus_prop is not None:
                self.assertIn((autofocus_prop, 1.0), fake_cap.set_calls)
        else:
            if autofocus_prop is not None:
                self.assertNotIn((autofocus_prop, 1.0), fake_cap.set_calls)
                self.assertIn((autofocus_prop, 0.0), fake_cap.set_calls)
            self.assertIn((focus_prop, 5.0), fake_cap.set_calls)

    def test_apply_focus_mode_manual_disables_autofocus_before_setting_focus(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        prop_results = {
            prop: True
            for prop in (autofocus_prop, focus_prop)
            if prop is not None
        }
        fake_cap = _FocusAwareCapture(prop_results)
        capability = FocusCapability(
            autofocus_supported=autofocus_prop is not None,
            manual_focus_supported=focus_prop is not None,
        )

        applied = apply_focus_mode(
            fake_cap,
            capability,
            mode="manual",
            manual_focus_value=8.0,
        )

        if focus_prop is None:
            self.assertFalse(applied)
        else:
            self.assertTrue(applied)
            if autofocus_prop is not None:
                self.assertIn((autofocus_prop, 0.0), fake_cap.set_calls)
            self.assertIn((focus_prop, 8.0), fake_cap.set_calls)

    def test_apply_focus_mode_manual_falls_back_to_autofocus_when_manual_focus_is_unavailable(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        prop_results = {
            prop: True
            for prop in (autofocus_prop,)
            if prop is not None
        }
        fake_cap = _FocusAwareCapture(prop_results)
        capability = FocusCapability(
            autofocus_supported=autofocus_prop is not None,
            manual_focus_supported=False,
        )

        applied = apply_focus_mode(
            fake_cap,
            capability,
            mode="manual",
            manual_focus_value=8.0,
        )

        self.assertFalse(applied)
        if autofocus_prop is not None:
            self.assertIn((autofocus_prop, 1.0), fake_cap.set_calls)
        if focus_prop is not None:
            self.assertFalse(any(prop == focus_prop for prop, _ in fake_cap.set_calls))

    def test_set_focus_mode_manual_updates_cached_focus_capability(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        prop_results = {
            prop: True
            for prop in (autofocus_prop, focus_prop)
            if prop is not None
        }
        fake_cap = _FocusAwareCapture(prop_results)
        scanner = ScannerView()
        scanner._cap = fake_cap

        scanner.set_manual_focus_value(9.0)
        applied = scanner.set_focus_mode("manual")

        self.assertEqual(scanner._focus_capability.autofocus_supported, autofocus_prop is not None)
        self.assertEqual(scanner._focus_capability.manual_focus_supported, focus_prop is not None)
        if focus_prop is None:
            if autofocus_prop is not None:
                self.assertIn((autofocus_prop, 1.0), fake_cap.set_calls)
            self.assertFalse(applied)
        else:
            self.assertTrue(applied)
            self.assertEqual(scanner._focus_mode, "manual")
            self.assertEqual(scanner._manual_focus_value, 9.0)

    def test_configure_focus_for_capture_applies_current_mode_and_caches_capability(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        prop_results = {
            prop: True
            for prop in (autofocus_prop, focus_prop)
            if prop is not None
        }
        fake_cap = _FocusAwareCapture(prop_results)
        scanner = ScannerView()
        scanner.set_manual_focus_value(7.0)
        scanner._focus_mode = "manual"

        applied = scanner._configure_focus_for_capture(fake_cap)

        self.assertEqual(scanner._focus_capability.autofocus_supported, autofocus_prop is not None)
        self.assertEqual(scanner._focus_capability.manual_focus_supported, focus_prop is not None)
        if focus_prop is None:
            if autofocus_prop is not None:
                self.assertIn((autofocus_prop, 1.0), fake_cap.set_calls)
            self.assertFalse(applied)
        else:
            self.assertTrue(applied)
            if autofocus_prop is not None:
                self.assertIn((autofocus_prop, 0.0), fake_cap.set_calls)
            self.assertIn((focus_prop, 7.0), fake_cap.set_calls)

    def test_attempt_camera_reopen_configures_focus_for_reopened_capture(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        prop_results = {
            prop: True
            for prop in (autofocus_prop, focus_prop)
            if prop is not None
        }
        old_cap = _FakeCapture()
        new_cap = _FocusAwareCapture(prop_results)
        scanner = ScannerView()
        scanner._cap = old_cap
        scanner._focus_mode = "manual"
        scanner.set_manual_focus_value(6.0)
        scanner._last_camera_reopen_at = -999.0

        with patch.object(scanner, "_open_camera_with_fallback", return_value=(new_cap, "DEFAULT")):
            reopened = scanner._attempt_camera_reopen(10.0, reason="test")

        self.assertTrue(reopened)
        self.assertIs(scanner._cap, new_cap)
        self.assertEqual(scanner._camera_backend_name, "DEFAULT")
        self.assertTrue(old_cap.released)
        self.assertEqual(scanner._focus_capability.manual_focus_supported, focus_prop is not None)
        if focus_prop is None:
            if autofocus_prop is not None:
                self.assertIn((autofocus_prop, 1.0), new_cap.set_calls)
        else:
            self.assertIn((focus_prop, 6.0), new_cap.set_calls)

    def test_attempt_camera_reopen_releases_old_capture_before_opening_new_one(self) -> None:
        old_cap = _FakeCapture()
        new_cap = _FakeCapture()
        scanner = ScannerView()
        scanner._cap = old_cap
        scanner._last_camera_reopen_at = -999.0
        release_state: list[bool] = []

        def _open_after_release(*args, **kwargs):
            release_state.append(old_cap.released)
            return new_cap, "DEFAULT"

        with patch.object(scanner, "_open_camera_with_fallback", side_effect=_open_after_release):
            reopened = scanner._attempt_camera_reopen(10.0, reason="read_failure")

        self.assertTrue(reopened)
        self.assertEqual(release_state, [True])
        self.assertTrue(old_cap.released)
        self.assertIs(scanner._cap, new_cap)

    def test_attempt_camera_reopen_configures_auto_focus_for_reopened_capture(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        prop_results = {
            prop: True
            for prop in (autofocus_prop,)
            if prop is not None
        }
        old_cap = _FakeCapture()
        new_cap = _FocusAwareCapture(prop_results)
        scanner = ScannerView()
        scanner._cap = old_cap
        scanner._focus_mode = "auto"
        scanner._last_camera_reopen_at = -999.0

        with patch.object(scanner, "_open_camera_with_fallback", return_value=(new_cap, "DEFAULT")):
            reopened = scanner._attempt_camera_reopen(10.0, reason="test")

        self.assertTrue(reopened)
        self.assertIs(scanner._cap, new_cap)
        self.assertEqual(scanner._camera_backend_name, "DEFAULT")
        self.assertTrue(old_cap.released)
        self.assertEqual(scanner._focus_capability.autofocus_supported, autofocus_prop is not None)
        if autofocus_prop is not None:
            self.assertIn((autofocus_prop, 1.0), new_cap.set_calls)

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
            self.assertEqual(fake_cap.set_calls, [(autofocus_prop, 1.0)])

    def test_set_scanning_enabled_reapplies_manual_focus_when_manual_mode_is_active(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        prop_results = {
            prop: True
            for prop in (autofocus_prop, focus_prop)
            if prop is not None
        }
        fake_cap = _FocusAwareCapture(prop_results)
        scanner = ScannerView()
        scanner._cap = fake_cap
        scanner._focus_mode = "manual"
        scanner._focus_capability = FocusCapability(
            autofocus_supported=autofocus_prop is not None,
            manual_focus_supported=focus_prop is not None,
        )
        scanner.set_manual_focus_value(11.0)
        fake_cap.set_calls.clear()
        scanner._runtime_status_message = "processing"

        scanner.set_scanning_enabled(True)

        if focus_prop is None:
            if autofocus_prop is not None:
                self.assertIn((autofocus_prop, 1.0), fake_cap.set_calls)
        else:
            if autofocus_prop is not None:
                self.assertNotIn((autofocus_prop, 1.0), fake_cap.set_calls)
                self.assertIn((autofocus_prop, 0.0), fake_cap.set_calls)
            self.assertIn((focus_prop, 11.0), fake_cap.set_calls)


if __name__ == "__main__":
    unittest.main()
