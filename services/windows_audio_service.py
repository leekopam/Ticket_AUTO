"""Play short notification sounds on Windows without extra dependencies."""
from __future__ import annotations

import ctypes
import threading
from pathlib import Path


class WindowsAudioService:
    """Asynchronous MCI-based audio playback for local files."""

    def __init__(self):
        self._alias = "ticket_auto_scan_sound"
        self._lock = threading.Lock()
        try:
            self._winmm = ctypes.WinDLL("winmm")
        except Exception:
            self._winmm = None

    def play_file(self, path: str) -> bool:
        if not path or self._winmm is None:
            return False

        file_path = Path(path).expanduser()
        if not file_path.exists() or not file_path.is_file():
            return False

        media_type = self._resolve_media_type(file_path)
        escaped_path = str(file_path.resolve()).replace('"', '""')

        with self._lock:
            self.stop()
            if self._send_command(f'open "{escaped_path}" type {media_type} alias {self._alias}') != 0:
                return False
            if self._send_command(f"play {self._alias} from 0") != 0:
                self.stop()
                return False
        return True

    def stop(self) -> None:
        if self._winmm is None:
            return
        self._send_command(f"close {self._alias}")

    @staticmethod
    def _resolve_media_type(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".wav":
            return "waveaudio"
        return "mpegvideo"

    def _send_command(self, command: str) -> int:
        if self._winmm is None:
            return 1
        return int(self._winmm.mciSendStringW(command, None, 0, 0))
