"""Windows camera enumeration helpers."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import subprocess
import threading
import time
from typing import Iterator, Literal

import cv2


FocusMode = Literal["auto", "manual"]
FocusApplyStatus = Literal["applied", "failed", "unsupported"]


@dataclass(frozen=True)
class FocusApplyResult:
    """카메라 초점 적용 결과와 재시도 가능 여부입니다."""

    status: FocusApplyStatus

    @property
    def applied(self) -> bool:
        return self.status == "applied"

    def __bool__(self) -> bool:
        return self.applied


@dataclass(frozen=True)
class FocusCapability:
    """카메라가 공개한 초점 제어 상태입니다.

    ``None``은 OpenCV 속성은 존재하지만 실제 장치 지원 여부를 아직
    설정 결과로 확인하지 못했다는 의미입니다.
    """

    autofocus_supported: bool | None
    manual_focus_supported: bool | None
    focus_min: float | None = None
    focus_max: float | None = None
    focus_step: float | None = None


def _coerce_capture_set_result(result: object) -> bool:
    return True if result is None else bool(result)


def _capture_is_available(cap: cv2.VideoCapture | object) -> bool:
    if cap is None or not hasattr(cap, "set"):
        return False
    is_opened = getattr(cap, "isOpened", None)
    if not callable(is_opened):
        return True
    try:
        return bool(is_opened())
    except Exception:
        return False


def detect_focus_capability(cap: cv2.VideoCapture | object) -> FocusCapability:
    """장치 설정을 변경하지 않고 확인 가능한 초점 기능을 반환합니다."""
    if not _capture_is_available(cap):
        return FocusCapability(autofocus_supported=False, manual_focus_supported=False)

    autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
    focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)
    return FocusCapability(
        autofocus_supported=None if autofocus_prop is not None else False,
        manual_focus_supported=None if focus_prop is not None else False,
    )


def apply_focus_mode(
    cap: cv2.VideoCapture | object,
    capability: FocusCapability,
    *,
    mode: FocusMode,
    manual_focus_value: float | None = None,
) -> FocusApplyResult:
    """초점 모드를 적용하고 성공·실패·미지원 상태를 반환합니다."""
    if not _capture_is_available(cap):
        return FocusApplyResult(status="failed")

    autofocus_prop = getattr(cv2, "CAP_PROP_AUTOFOCUS", None)
    focus_prop = getattr(cv2, "CAP_PROP_FOCUS", None)

    if mode == "auto":
        if capability.autofocus_supported is False or autofocus_prop is None:
            return FocusApplyResult(status="unsupported")
        try:
            applied = _coerce_capture_set_result(cap.set(autofocus_prop, 1.0))
        except Exception:
            applied = False
        return FocusApplyResult(status="applied" if applied else "failed")

    if capability.manual_focus_supported is False or focus_prop is None:
        if capability.autofocus_supported is not False and autofocus_prop is not None:
            try:
                _coerce_capture_set_result(cap.set(autofocus_prop, 1.0))
            except Exception:
                pass
        return FocusApplyResult(status="unsupported")

    try:
        normalized_manual_focus = None if manual_focus_value is None else float(manual_focus_value)
    except (TypeError, ValueError):
        normalized_manual_focus = None
    if normalized_manual_focus is None or not math.isfinite(normalized_manual_focus):
        if capability.autofocus_supported is not False and autofocus_prop is not None:
            try:
                _coerce_capture_set_result(cap.set(autofocus_prop, 1.0))
            except Exception:
                pass
        return FocusApplyResult(status="failed")

    if capability.autofocus_supported is not False and autofocus_prop is not None:
        try:
            autofocus_disabled = _coerce_capture_set_result(cap.set(autofocus_prop, 0.0))
        except Exception:
            autofocus_disabled = False
        if not autofocus_disabled:
            try:
                _coerce_capture_set_result(cap.set(autofocus_prop, 1.0))
            except Exception:
                pass
            return FocusApplyResult(status="failed")

    try:
        applied = _coerce_capture_set_result(cap.set(focus_prop, normalized_manual_focus))
    except Exception:
        applied = False
    if applied:
        return FocusApplyResult(status="applied")

    if capability.autofocus_supported is not False and autofocus_prop is not None:
        try:
            _coerce_capture_set_result(cap.set(autofocus_prop, 1.0))
        except Exception:
            pass
    return FocusApplyResult(status="failed")


@dataclass(frozen=True)
class CameraDevice:
    """Camera device metadata."""

    index: int
    name: str


class WindowsCameraService:
    """Enumerate camera devices for the Windows desktop app."""

    _WMI_CACHE_TTL_SEC = 10.0
    _OPENCV_PROBE_CACHE_TTL_SEC = 2.0

    def __init__(self) -> None:
        self._cached_wmi_names: list[str] | None = None
        self._cached_wmi_names_at = 0.0
        self._opencv_probe_lock = threading.Lock()
        self._cached_opencv_indices: dict[tuple[int, int | None], tuple[float, list[int]]] = {}

    def list_cameras(self, *, max_index: int = 9) -> list[CameraDevice]:
        """Return usable camera devices.

        If WMI can see physical camera names, we only probe until that many
        working indices are found. That keeps phantom backends and invalid
        indices from polluting the dropdown as extra "camera N" entries.
        """
        wmi_names = self._get_cached_wmi_camera_names()
        target_count = len(wmi_names) if wmi_names else None
        openable_indices = self._get_cached_opencv_indices(
            max_index=max_index,
            target_count=target_count,
        )

        if wmi_names:
            return [
                CameraDevice(index=index, name=wmi_names[position])
                for position, index in enumerate(openable_indices[: len(wmi_names)])
            ]

        return [
            CameraDevice(index=index, name=f"카메라 {index}")
            for index in openable_indices
        ]

    @staticmethod
    def open_camera_settings(cap: cv2.VideoCapture | object) -> bool:
        """DirectShow 카메라의 Windows/제조사 속성 창을 엽니다."""
        settings_prop = getattr(cv2, "CAP_PROP_SETTINGS", None)
        if settings_prop is None or not _capture_is_available(cap):
            return False
        try:
            return _coerce_capture_set_result(cap.set(settings_prop, 1.0))
        except Exception:
            return False

    def _get_cached_wmi_camera_names(self) -> list[str]:
        now = time.monotonic()
        cached = self._cached_wmi_names
        if cached is not None and (now - self._cached_wmi_names_at) < self._WMI_CACHE_TTL_SEC:
            return list(cached)

        names = self._get_wmi_camera_names()
        self._cached_wmi_names = list(names)
        self._cached_wmi_names_at = now
        return list(names)

    def _get_cached_opencv_indices(
        self,
        *,
        max_index: int,
        target_count: int | None,
    ) -> list[int]:
        cache_key = (max_index, target_count)
        now = time.monotonic()
        cached = self._cached_opencv_indices.get(cache_key)
        if cached is not None:
            cached_at, cached_indices = cached
            if (now - cached_at) < self._OPENCV_PROBE_CACHE_TTL_SEC:
                print(
                    "CAMERA_LIST_PROBE_CACHE_HIT "
                    f"max_index={max_index} target_count={target_count} count={len(cached_indices)}"
                )
                return list(cached_indices)

        with self._opencv_probe_lock:
            now = time.monotonic()
            cached = self._cached_opencv_indices.get(cache_key)
            if cached is not None:
                cached_at, cached_indices = cached
                if (now - cached_at) < self._OPENCV_PROBE_CACHE_TTL_SEC:
                    print(
                        "CAMERA_LIST_PROBE_CACHE_HIT "
                        f"max_index={max_index} target_count={target_count} count={len(cached_indices)}"
                    )
                    return list(cached_indices)

            probe_start = time.perf_counter()
            indices = self._probe_opencv_indices(max_index=max_index, target_count=target_count)
            probe_ms = (time.perf_counter() - probe_start) * 1000.0
            self._cached_opencv_indices[cache_key] = (time.monotonic(), list(indices))
            print(
                "CAMERA_LIST_PROBE_TIMING "
                f"max_index={max_index} target_count={target_count} "
                f"count={len(indices)} probe_ms={probe_ms:.1f}"
            )
            return list(indices)

    @staticmethod
    def _probe_opencv_indices(
        max_index: int = 9,
        *,
        target_count: int | None = None,
    ) -> list[int]:
        """Return indices that can open and deliver a real frame."""
        result: list[int] = []
        with WindowsCameraService._silence_opencv_probe_logs():
            for index in range(max_index + 1):
                cap = None
                try:
                    for backend in (cv2.CAP_DSHOW, None):
                        try:
                            cap = (
                                cv2.VideoCapture(index, backend)
                                if backend is not None
                                else cv2.VideoCapture(index)
                            )
                        except Exception:
                            cap = None
                            continue

                        if cap and cap.isOpened():
                            ok, frame = cap.read()
                            if ok and WindowsCameraService._is_usable_probe_frame(frame):
                                result.append(index)
                                break

                        if cap is not None:
                            try:
                                cap.release()
                            except Exception:
                                pass
                            cap = None
                finally:
                    if cap is not None:
                        try:
                            cap.release()
                        except Exception:
                            pass

                if target_count is not None and len(result) >= target_count:
                    break

        return result

    @staticmethod
    def _is_usable_probe_frame(frame: object) -> bool:
        if frame is None:
            return False
        shape = getattr(frame, "shape", None)
        size = getattr(frame, "size", 0)
        if not shape or len(shape) < 2 or size <= 0:
            return False
        try:
            height, width = int(shape[0]), int(shape[1])
        except Exception:
            return False
        return height > 0 and width > 0

    @staticmethod
    @contextmanager
    def _silence_opencv_probe_logs() -> Iterator[None]:
        logging_api = getattr(getattr(cv2, "utils", None), "logging", None)
        set_level = getattr(logging_api, "setLogLevel", None)
        get_level = getattr(logging_api, "getLogLevel", None)
        silent_level = getattr(logging_api, "LOG_LEVEL_SILENT", None)
        if not callable(set_level) or not callable(get_level) or silent_level is None:
            yield
            return

        previous_level = get_level()
        try:
            set_level(silent_level)
            yield
        finally:
            set_level(previous_level)

    @staticmethod
    def _get_wmi_camera_names() -> list[str]:
        """Read physical camera names from WMI when available."""
        names = WindowsCameraService._get_powershell_camera_names()
        if names:
            return names

        try:
            import wmi  # type: ignore[import-untyped]

            client = wmi.WMI()
            names: list[str] = []
            seen: set[str] = set()

            for pnp_class in ("Camera", "Image"):
                try:
                    devices = client.Win32_PnPEntity(PNPClass=pnp_class)
                except Exception:
                    continue
                for device in devices:
                    name = getattr(device, "Name", None) or getattr(device, "Caption", None)
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)

            if not names:
                try:
                    devices = client.Win32_PnPEntity(Service="usbvideo")
                except Exception:
                    devices = []
                for device in devices:
                    name = getattr(device, "Name", None) or getattr(device, "Caption", None)
                    if name and name not in seen:
                        seen.add(name)
                        names.append(name)

            return names
        except Exception:
            return []

    @staticmethod
    def _get_powershell_camera_names() -> list[str]:
        script = """
$ErrorActionPreference = 'Stop'
$names = [System.Collections.Generic.List[string]]::new()
foreach ($pnpClass in @('Camera', 'Image')) {
    try {
        Get-CimInstance Win32_PnPEntity -Filter "PNPClass='$pnpClass'" | ForEach-Object {
            if ($_.Name) { $names.Add($_.Name) }
            elseif ($_.Caption) { $names.Add($_.Caption) }
        }
    }
    catch { }
}
if ($names.Count -eq 0) {
    try {
        Get-CimInstance Win32_PnPEntity -Filter "Service='usbvideo'" | ForEach-Object {
            if ($_.Name) { $names.Add($_.Name) }
            elseif ($_.Caption) { $names.Add($_.Caption) }
        }
    }
    catch { }
}
$names | Select-Object -Unique | ConvertTo-Json -Compress
""".strip()

        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except Exception:
            return []

        if result.returncode != 0:
            return []

        stdout = (result.stdout or "").strip()
        if not stdout or stdout == "null":
            return []

        try:
            parsed = json.loads(stdout)
        except Exception:
            return []

        if isinstance(parsed, str):
            values = [parsed]
        elif isinstance(parsed, list):
            values = [value for value in parsed if isinstance(value, str)]
        else:
            return []

        names: list[str] = []
        seen: set[str] = set()
        for value in values:
            name = value.strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names
