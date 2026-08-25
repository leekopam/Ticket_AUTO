from __future__ import annotations

import unittest
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

from services.windows_camera_service import (
    CameraDevice,
    FocusCapability,
    WindowsCameraService,
    apply_focus_mode,
    detect_focus_capability,
)


class _FakeCapture:
    def __init__(
        self,
        *,
        opened: bool = True,
        frame: object | None = None,
        property_results: dict[int, bool] | None = None,
    ):
        self._opened = opened
        self._frame = frame if frame is not None else np.zeros((4, 4, 3), dtype=np.uint8)
        self.released = False
        self.property_results = dict(property_results or {})
        self.set_calls: list[tuple[int, float]] = []

    def isOpened(self) -> bool:
        return self._opened

    def read(self) -> tuple[bool, object | None]:
        return self._opened, self._frame

    def release(self) -> None:
        self.released = True

    def set(self, prop: int, value: float) -> bool:
        self.set_calls.append((prop, value))
        return self.property_results.get(prop, False)


class WindowsCameraServiceTest(unittest.TestCase):
    def test_detect_focus_capability_keeps_unverified_properties_unknown(self) -> None:
        capture = _FakeCapture()

        capability = detect_focus_capability(capture)

        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        self.assertIs(capability.autofocus_supported, None if autofocus_prop is not None else False)
        self.assertIs(capability.manual_focus_supported, None if focus_prop is not None else False)
        self.assertEqual(capture.set_calls, [])

    def test_apply_focus_mode_allows_unverified_manual_property(self) -> None:
        autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
        focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
        property_results = {
            prop: True
            for prop in (autofocus_prop, focus_prop)
            if prop is not None
        }
        capture = _FakeCapture(property_results=property_results)
        capability = FocusCapability(
            autofocus_supported=None if autofocus_prop is not None else False,
            manual_focus_supported=None if focus_prop is not None else False,
        )

        applied = apply_focus_mode(capture, capability, mode="manual", manual_focus_value=7.0)

        if focus_prop is None:
            self.assertFalse(applied)
        else:
            self.assertTrue(applied)
            self.assertIn((focus_prop, 7.0), capture.set_calls)

    def test_open_camera_settings_uses_opencv_settings_property(self) -> None:
        settings_prop = getattr(cv2, "CAP_PROP_SETTINGS", None)
        property_results = {settings_prop: True} if settings_prop is not None else {}
        capture = _FakeCapture(property_results=property_results)

        opened = WindowsCameraService.open_camera_settings(capture)

        if settings_prop is None:
            self.assertFalse(opened)
        else:
            self.assertTrue(opened)
            self.assertEqual(capture.set_calls, [(settings_prop, 1.0)])

    def test_get_powershell_camera_names_parses_unique_names(self) -> None:
        with patch(
            "services.windows_camera_service.subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout='["HD Pro Webcam C920","USB Camera","HD Pro Webcam C920"]',
            ),
        ):
            names = WindowsCameraService._get_powershell_camera_names()

        self.assertEqual(names, ["HD Pro Webcam C920", "USB Camera"])

    def test_get_cached_wmi_camera_names_reuses_recent_result(self) -> None:
        service = WindowsCameraService()

        with patch.object(service, "_get_wmi_camera_names", return_value=["HD Pro Webcam C920"]) as getter:
            first = service._get_cached_wmi_camera_names()
            second = service._get_cached_wmi_camera_names()

        self.assertEqual(first, ["HD Pro Webcam C920"])
        self.assertEqual(second, ["HD Pro Webcam C920"])
        getter.assert_called_once()

    def test_probe_opencv_indices_stops_after_target_count(self) -> None:
        calls: list[tuple[int, object | None]] = []

        def _video_capture(index: int, backend: object | None = None) -> _FakeCapture:
            calls.append((index, backend))
            return _FakeCapture(opened=(index == 0))

        with patch("services.windows_camera_service.cv2.VideoCapture", side_effect=_video_capture):
            result = WindowsCameraService._probe_opencv_indices(max_index=5, target_count=1)

        self.assertEqual(result, [0])
        self.assertEqual(calls, [(0, cv2.CAP_DSHOW)])

    def test_list_cameras_uses_only_wmi_named_devices(self) -> None:
        service = WindowsCameraService()

        with (
            patch.object(service, "_get_wmi_camera_names", return_value=["HD Pro Webcam C920"]),
            patch.object(service, "_probe_opencv_indices", return_value=[0, 7]),
        ):
            devices = service.list_cameras()

        self.assertEqual(devices, [CameraDevice(index=0, name="HD Pro Webcam C920")])

    def test_list_cameras_falls_back_to_generic_names_without_wmi(self) -> None:
        service = WindowsCameraService()

        with (
            patch.object(service, "_get_wmi_camera_names", return_value=[]),
            patch.object(service, "_probe_opencv_indices", return_value=[2, 4]),
        ):
            devices = service.list_cameras()

        self.assertEqual(
            devices,
            [
                CameraDevice(index=2, name="카메라 2"),
                CameraDevice(index=4, name="카메라 4"),
            ],
        )

    def test_list_cameras_reuses_recent_opencv_probe_result(self) -> None:
        service = WindowsCameraService()

        with (
            patch.object(service, "_get_wmi_camera_names", return_value=[]),
            patch.object(service, "_probe_opencv_indices", return_value=[0]) as probe,
        ):
            first = service.list_cameras()
            second = service.list_cameras()

        self.assertEqual(first, [CameraDevice(index=0, name="카메라 0")])
        self.assertEqual(second, [CameraDevice(index=0, name="카메라 0")])
        probe.assert_called_once_with(max_index=9, target_count=None)

    def test_list_cameras_suppresses_duplicate_inflight_opencv_probe(self) -> None:
        service = WindowsCameraService()
        probe_started = threading.Event()
        release_probe = threading.Event()
        results: list[list[CameraDevice]] = []
        errors: list[Exception] = []

        def slow_probe(*, max_index: int = 9, target_count: int | None = None) -> list[int]:
            probe_started.set()
            release_probe.wait(timeout=5)
            return [1]

        def load_cameras() -> None:
            try:
                results.append(service.list_cameras())
            except Exception as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        with (
            patch.object(service, "_get_wmi_camera_names", return_value=[]),
            patch.object(service, "_probe_opencv_indices", side_effect=slow_probe) as probe,
        ):
            first_thread = threading.Thread(target=load_cameras)
            second_thread = threading.Thread(target=load_cameras)
            first_thread.start()
            self.assertTrue(probe_started.wait(timeout=1.0))
            second_thread.start()
            time.sleep(0.05)
            inflight_call_count = probe.call_count
            release_probe.set()
            first_thread.join(timeout=2.0)
            second_thread.join(timeout=2.0)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(inflight_call_count, 1)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(
            results,
            [
                [CameraDevice(index=1, name="카메라 1")],
                [CameraDevice(index=1, name="카메라 1")],
            ],
        )


if __name__ == "__main__":
    unittest.main()
