from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_paths import (
    copy_data_file_to_managed_location,
    ensure_managed_data_file,
    ensure_managed_templates_dir,
    make_project_relative_path,
    resolve_runtime_file_path,
)


class ProjectPathsDataFileTest(unittest.TestCase):
    def test_ensure_managed_data_file_migrates_legacy_root_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            legacy = root / "data.xlsx"
            legacy.write_bytes(b"legacy-data")

            with patch("project_paths.PROJECT_ROOT", root):
                managed = ensure_managed_data_file()

            self.assertEqual(managed, root / "Resources" / "data" / "data.xlsx")
            self.assertTrue(managed.exists())
            self.assertEqual(managed.read_bytes(), b"legacy-data")

    def test_copy_data_file_to_managed_location_copies_selected_excel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "selected.xlsx"
            source.write_bytes(b"selected-data")

            with patch("project_paths.PROJECT_ROOT", root):
                managed = copy_data_file_to_managed_location(source)

            self.assertEqual(managed, root / "Resources" / "data" / "data.xlsx")
            self.assertTrue(managed.exists())
            self.assertEqual(managed.read_bytes(), b"selected-data")

    def test_ensure_managed_templates_dir_migrates_legacy_template_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            legacy_dir = root / "templates"
            legacy_dir.mkdir(parents=True, exist_ok=True)
            (legacy_dir / "receipt_layout.json").write_text("{}", encoding="utf-8")
            (legacy_dir / "product_receipt_layout.json").write_text("{}", encoding="utf-8")

            with patch("project_paths.PROJECT_ROOT", root):
                managed_dir = ensure_managed_templates_dir()

            self.assertEqual(managed_dir, root / "Resources" / "templates")
            self.assertTrue((managed_dir / "receipt_layout.json").exists())
            self.assertTrue((managed_dir / "product_receipt_layout.json").exists())

    def test_make_project_relative_path_converts_managed_resource_to_portable_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            managed_sound = root / "Resources" / "sound" / "success.wav"
            managed_sound.parent.mkdir(parents=True, exist_ok=True)
            managed_sound.write_bytes(b"wav")

            with patch("project_paths.PROJECT_ROOT", root), patch("project_paths.BUNDLE_ROOT", root):
                portable = make_project_relative_path(managed_sound)

            self.assertEqual(portable, "Resources/sound/success.wav")

    def test_resolve_runtime_file_path_expands_portable_relative_path_under_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            with patch("project_paths.PROJECT_ROOT", root), patch("project_paths.BUNDLE_ROOT", root):
                resolved = resolve_runtime_file_path("Resources/sound/success.wav")

            self.assertEqual(resolved, root / "Resources" / "sound" / "success.wav")


if __name__ == "__main__":
    unittest.main()
