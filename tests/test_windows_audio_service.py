"""Windows audio service tests."""
from __future__ import annotations

import unittest

from services.windows_audio_service import WindowsAudioService


class WindowsAudioServiceTest(unittest.TestCase):
    def test_play_file_returns_false_for_empty_or_missing_path(self) -> None:
        service = WindowsAudioService()
        self.assertFalse(service.play_file(""))
        self.assertFalse(service.play_file("Z:/definitely-missing-file.mp3"))


if __name__ == "__main__":
    unittest.main()
