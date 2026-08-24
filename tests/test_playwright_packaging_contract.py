from __future__ import annotations

import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlaywrightPackagingContractTest(unittest.TestCase):
    def test_pyinstaller_specs_bundle_chromium_revision_into_frozen_playwright_path(self) -> None:
        """Frozen builds must place the matching Chromium cache where Playwright looks for it."""
        try:
            module_spec = importlib.util.find_spec("build_support.playwright_browsers")
        except ModuleNotFoundError:
            module_spec = None
        self.assertIsNotNone(
            module_spec,
            "PyInstaller specs need a shared helper that collects Playwright browser files.",
        )
        module = importlib.import_module("build_support.playwright_browsers")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            package_root = root / "playwright" / "driver" / "package"
            package_root.mkdir(parents=True)
            (package_root / "browsers.json").write_text(
                json.dumps(
                    {
                        "browsers": [
                            {
                                "name": "chromium",
                                "revision": "4321",
                                "installByDefault": True,
                            },
                            {
                                "name": "firefox",
                                "revision": "9999",
                                "installByDefault": True,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            chromium_dir = root / "ms-playwright" / "chromium-4321"
            chrome_exe = chromium_dir / "chrome-win64" / "chrome.exe"
            chrome_exe.parent.mkdir(parents=True)
            chrome_exe.write_text("fake chrome", encoding="utf-8")

            datas = module.collect_playwright_browser_datas(
                package_root=package_root,
                browsers_path=root / "ms-playwright",
            )

        self.assertEqual(
            datas,
            [
                (
                    str(chromium_dir),
                    "playwright/driver/package/.local-browsers/chromium-4321",
                )
            ],
        )

        for spec_name in ("Ticket_AUTO.spec", "Ticket_AUTO_flat.spec"):
            spec_file = ROOT / "build_support" / "specs" / spec_name
            source = spec_file.read_text(encoding="utf-8-sig")
            self.assertIn(
                "from build_support.playwright_browsers import collect_playwright_browser_datas",
                source,
                f"{spec_file} must import the shared Playwright browser bundling helper.",
            )
            self.assertIn(
                "datas += collect_playwright_browser_datas()",
                source,
                f"{spec_file} must bundle the matching browser cache into the frozen app.",
            )
            self.assertIn(
                "PROJECT_ROOT = Path(SPECPATH).resolve().parents[1]",
                source,
                f"{spec_file} must resolve app resources from the repository root.",
            )
            self.assertNotIn(
                "receipt_form.json",
                source,
                f"{spec_file} must not bundle the obsolete receipt form sample.",
            )


if __name__ == "__main__":
    unittest.main()
