"""Windows audio service tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.windows_audio_service import WindowsAudioService


class WindowsAudioServiceTest(unittest.TestCase):
    def test_play_file_returns_false_for_empty_or_missing_path(self) -> None:
        service = WindowsAudioService()
        self.assertFalse(service.play_file(""))
        self.assertFalse(service.play_file("Z:/definitely-missing-file.mp3"))

    def test_play_file_resolves_relative_path_under_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "Resources" / "sound" / "success.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"RIFF")

            service = WindowsAudioService()
            service._winmm = object()
            commands: list[str] = []

            def _send_command(command: str) -> int:
                commands.append(command)
                return 0

            with (
                patch("project_paths.PROJECT_ROOT", root),
                patch("project_paths.BUNDLE_ROOT", root),
                patch.object(service, "_send_command", side_effect=_send_command),
            ):
                self.assertTrue(service.play_file("Resources/sound/success.wav"))

        self.assertGreaterEqual(len(commands), 2)
        self.assertTrue(any("open " in command for command in commands))


if __name__ == "__main__":
    unittest.main()
