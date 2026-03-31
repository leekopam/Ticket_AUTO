"""Windows camera enumeration helpers."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import subprocess
import time
from typing import Iterator

import cv2


@dataclass(frozen=True)
class CameraDevice:
    """Camera device metadata."""

    index: int
    name: str


class WindowsCameraService:
    """Enumerate camera devices for the Windows desktop app."""

    _WMI_CACHE_TTL_SEC = 10.0

    def __init__(self) -> None:
        self._cached_wmi_names: list[str] | None = None
        self._cached_wmi_names_at = 0.0

    def list_cameras(self, *, max_index: int = 9) -> list[CameraDevice]:
        """Return usable camera devices.

        If WMI can see physical camera names, we only probe until that many
        working indices are found. That keeps phantom backends and invalid
        indices from polluting the dropdown as extra "camera N" entries.
        """
        wmi_names = self._get_cached_wmi_camera_names()
        target_count = len(wmi_names) if wmi_names else None
        openable_indices = self._probe_opencv_indices(
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

    def _get_cached_wmi_camera_names(self) -> list[str]:
        now = time.monotonic()
        cached = self._cached_wmi_names
        if cached is not None and (now - self._cached_wmi_names_at) < self._WMI_CACHE_TTL_SEC:
            return list(cached)

        names = self._get_wmi_camera_names()
        self._cached_wmi_names = list(names)
        self._cached_wmi_names_at = now
        return list(names)

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
