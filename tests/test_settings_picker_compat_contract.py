"""File picker compatibility helper tests."""
from __future__ import annotations

import unittest


class _DummyFile:
    def __init__(self, path: str):
        self.path = path


class _DummyEvent:
    def __init__(self, files=None, path=None):
        self.files = files
        self.path = path


class SettingsPickerCompatContractTest(unittest.TestCase):
    def test_coerce_picker_files_supports_result_event_and_list(self) -> None:
        try:
            from views.settings_flet_view import _coerce_picker_files
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        files = [_DummyFile("C:/temp/test.mp3")]
        self.assertEqual(_coerce_picker_files(_DummyEvent(files=files)), files)
        self.assertEqual(_coerce_picker_files(files), files)
        self.assertEqual(_coerce_picker_files(None), [])

    def test_coerce_picker_path_supports_path_event_string_and_file_list(self) -> None:
        try:
            from views.settings_flet_view import _coerce_picker_path
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(_coerce_picker_path(_DummyEvent(path="C:/temp/test.json")), "C:/temp/test.json")
        self.assertEqual(_coerce_picker_path("C:/temp/test.json"), "C:/temp/test.json")
        self.assertEqual(
            _coerce_picker_path([_DummyFile("C:/temp/test.mp3")]),
            "C:/temp/test.mp3",
        )
        self.assertIsNone(_coerce_picker_path(None))


if __name__ == "__main__":
    unittest.main()
