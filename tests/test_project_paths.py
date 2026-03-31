from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_paths import (
    copy_data_file_to_managed_location,
    ensure_managed_data_file,
    ensure_managed_templates_dir,
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


if __name__ == "__main__":
    unittest.main()
