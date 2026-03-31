"""Settings Excel product name loader contract tests."""
from __future__ import annotations

import unittest
from pathlib import Path


class _FakeExcelService:
    def __init__(self, excel_path: str) -> None:
        self.excel_path = excel_path

    def get_product_names(self) -> list[str]:
        return ["VIP", "일반"]


class _FailingExcelService:
    def __init__(self, excel_path: str) -> None:
        self.excel_path = excel_path

    def get_product_names(self) -> list[str]:
        raise RuntimeError("boom")


class SettingsExcelProductNamesContractTest(unittest.TestCase):
    def test_load_excel_product_names_returns_names_from_excel_service(self) -> None:
        try:
            from views.settings_flet_view import _load_excel_product_names
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        result = _load_excel_product_names(
            excel_path="custom.xlsx",
            excel_service_cls=_FakeExcelService,
        )

        self.assertEqual(result, ["VIP", "일반"])

    def test_load_excel_product_names_returns_empty_list_on_error(self) -> None:
        try:
            from views.settings_flet_view import _load_excel_product_names
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        result = _load_excel_product_names(excel_service_cls=_FailingExcelService)

        self.assertEqual(result, [])

    def test_settings_panels_use_excel_product_names_helper(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _load_excel_product_names(", source)
        self.assertGreaterEqual(source.count("_load_excel_product_names("), 3)


if __name__ == "__main__":
    unittest.main()
