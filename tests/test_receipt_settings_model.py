"""ReceiptSettings model compatibility tests."""
from __future__ import annotations

import unittest

from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
from project_paths import RESOURCE_PRODUCT_TEMPLATE_FILE


class ReceiptSettingsModelTest(unittest.TestCase):
    def test_qr_scan_auto_print_defaults_on_for_legacy_settings(self) -> None:
        self.assertTrue(ReceiptSettings().qr_scan_auto_print_enabled)
        self.assertTrue(ReceiptSettings.from_dict({}).qr_scan_auto_print_enabled)
        self.assertTrue(
            ReceiptSettings.from_dict({"printer_name": "영수증 프린터"}).qr_scan_auto_print_enabled
        )

    def test_qr_scan_auto_print_false_round_trips_through_dict(self) -> None:
        settings = ReceiptSettings(qr_scan_auto_print_enabled=False)

        restored = ReceiptSettings.from_dict(settings.to_dict())

        self.assertFalse(settings.to_dict()["qr_scan_auto_print_enabled"])
        self.assertFalse(restored.qr_scan_auto_print_enabled)

    def test_extended_paths_and_flags_round_trip_through_dict(self) -> None:
        settings = ReceiptSettings(
            qr_scan_success_sound_path="C:/sounds/scan.mp3",
            qr_scan_success_sound_rules=[
                ScanSuccessSoundRule(
                    name="기본 한국어",
                    sound_path="C:/sounds/thanks-ko.mp3",
                    weight=2,
                    trigger_type="always",
                ),
                ScanSuccessSoundRule(
                    name="희귀 특수 대사",
                    sound_path="C:/sounds/special.mp3",
                    trigger_type="specific_counts",
                    trigger_value="10, 20",
                ),
            ],
            product_template_path=RESOURCE_PRODUCT_TEMPLATE_FILE.as_posix(),
            print_product_receipt=True,
        )
        restored = ReceiptSettings.from_dict(settings.to_dict())
        self.assertEqual(restored.qr_scan_success_sound_path, "C:/sounds/scan.mp3")
        self.assertEqual(len(restored.qr_scan_success_sound_rules), 2)
        self.assertEqual(restored.qr_scan_success_sound_rules[0].name, "기본 한국어")
        self.assertEqual(restored.qr_scan_success_sound_rules[1].trigger_type, "specific_counts")
        self.assertEqual(restored.qr_scan_success_sound_rules[1].trigger_value, "10, 20")
        self.assertEqual(restored.product_template_path, RESOURCE_PRODUCT_TEMPLATE_FILE.as_posix())
        self.assertTrue(restored.print_product_receipt)

    def test_scanner_focus_settings_round_trip_through_dict(self) -> None:
        settings = ReceiptSettings(
            scanner_focus_mode="manual",
            scanner_manual_focus_value=8.5,
        )

        restored = ReceiptSettings.from_dict(settings.to_dict())

        self.assertEqual(restored.scanner_focus_mode, "manual")
        self.assertEqual(restored.scanner_manual_focus_value, 8.5)

    def test_scanner_focus_settings_fall_back_to_safe_defaults(self) -> None:
        restored = ReceiptSettings.from_dict(
            {
                "scanner_focus_mode": "unsupported",
                "scanner_manual_focus_value": "not-a-number",
            }
        )

        self.assertEqual(restored.scanner_focus_mode, "auto")
        self.assertIsNone(restored.scanner_manual_focus_value)

    def test_scanner_focus_settings_without_manual_value_fall_back_to_auto(self) -> None:
        restored = ReceiptSettings.from_dict(
            {
                "scanner_focus_mode": "manual",
                "scanner_manual_focus_value": None,
            }
        )

        self.assertEqual(restored.scanner_focus_mode, "auto")
        self.assertIsNone(restored.scanner_manual_focus_value)

    def test_scanner_focus_settings_reject_non_finite_manual_values(self) -> None:
        for raw_value in ("nan", "inf", "-inf", float("nan"), float("inf")):
            with self.subTest(raw_value=raw_value):
                restored = ReceiptSettings.from_dict(
                    {
                        "scanner_focus_mode": "manual",
                        "scanner_manual_focus_value": raw_value,
                    }
                )

                self.assertEqual(restored.scanner_focus_mode, "auto")
                self.assertIsNone(restored.scanner_manual_focus_value)

    def test_sound_rule_from_dict_falls_back_to_safe_defaults(self) -> None:
        restored = ReceiptSettings.from_dict(
            {
                "qr_scan_success_sound_rules": [
                    {
                        "name": "특수 대사",
                        "sound_path": "C:/sounds/special.mp3",
                        "weight": "invalid",
                        "trigger_type": "unsupported",
                        "trigger_value": "10",
                    }
                ]
            }
        )

        self.assertEqual(len(restored.qr_scan_success_sound_rules), 1)
        self.assertEqual(restored.qr_scan_success_sound_rules[0].weight, 1)
        self.assertEqual(restored.qr_scan_success_sound_rules[0].trigger_type, "always")

    def test_removed_sound_trigger_types_are_dropped_during_load(self) -> None:
        restored = ReceiptSettings.from_dict(
            {
                "qr_scan_success_sound_rules": [
                    {
                        "name": "희귀",
                        "sound_path": "C:/sounds/rare.mp3",
                        "trigger_type": "rare_percent",
                        "trigger_value": "3",
                    },
                    {
                        "name": "주문번호",
                        "sound_path": "C:/sounds/order.mp3",
                        "trigger_type": "order_number_equals",
                        "trigger_value": "ORDER-1",
                    },
                    {
                        "name": "유지",
                        "sound_path": "C:/sounds/keep.mp3",
                        "trigger_type": "specific_counts",
                        "trigger_value": "10",
                    },
                ]
            }
        )

        self.assertEqual(len(restored.qr_scan_success_sound_rules), 1)
        self.assertEqual(restored.qr_scan_success_sound_rules[0].sound_path, "C:/sounds/keep.mp3")
        self.assertEqual(restored.qr_scan_success_sound_rules[0].trigger_type, "specific_counts")

    def test_legacy_template_paths_are_normalized_to_resources_templates(self) -> None:
        restored = ReceiptSettings.from_dict(
            {
                "template_path": "templates/receipt_layout.json",
                "product_template_path": "templates/product_receipt_layout.json",
            }
        )

        self.assertEqual(restored.template_path, "Resources/templates/receipt_layout.json")
        self.assertEqual(restored.product_template_path, "Resources/templates/product_receipt_layout.json")


if __name__ == "__main__":
    unittest.main()
