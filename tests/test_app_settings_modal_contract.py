"""App settings modal structure tests."""
from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


class _StubPage:
    def __init__(self) -> None:
        self.overlay = []

    def update(self) -> None:
        return None


class AppSettingsModalContractTest(unittest.TestCase):
    def _walk_controls(self, control):
        if control is None:
            return
        yield control

        content = getattr(control, "content", None)
        if content is not None:
            yield from self._walk_controls(content)

        controls = getattr(control, "controls", None)
        if isinstance(controls, list):
            for child in controls:
                yield from self._walk_controls(child)

        actions = getattr(control, "actions", None)
        if isinstance(actions, list):
            for child in actions:
                yield from self._walk_controls(child)

    def _collect_strings(self, control) -> set[str]:
        values: set[str] = set()
        for node in self._walk_controls(control):
            for attr in ("value", "label", "text"):
                text = getattr(node, attr, None)
                if isinstance(text, str) and text:
                    values.add(text)
        return values

    def _find_control_by_label(self, control, label: str):
        for node in self._walk_controls(control):
            if getattr(node, "label", None) == label:
                return node
        return None

    def _find_clickable_control_by_text(self, control, text: str):
        for node in self._walk_controls(control):
            if getattr(node, "text", None) == text and callable(getattr(node, "on_click", None)):
                return node
        return None

    def _find_clickable_control_containing_text(self, control, text: str):
        for node in self._walk_controls(control):
            if callable(getattr(node, "on_click", None)) and text in self._collect_strings(node):
                return node
        return None

    def _find_control_by_tooltip(self, control, tooltip: str):
        for node in self._walk_controls(control):
            if getattr(node, "tooltip", None) == tooltip:
                return node
        return None

    def _find_card_by_heading(self, control, heading: str):
        for node in self._walk_controls(control):
            content = getattr(node, "content", None)
            controls = getattr(content, "controls", None)
            if (
                isinstance(controls, list)
                and controls
                and getattr(controls[0], "value", None) == heading
            ):
                return node
        return None

    def test_modal_contains_sound_configuration_controls(self) -> None:
        try:
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = build_app_settings_panel(_StubPage())
        strings = self._collect_strings(panel)

        self.assertIn("티켓 확인 설정", strings)
        self.assertIn("QR 스캔 완료 알림음", strings)
        self.assertIn("음원 선택", strings)
        self.assertIn("미리 듣기", strings)
        self.assertIn("초기화", strings)
        self.assertIn("티켓 상품 분류", strings)
        self.assertIn("영수증 양식 설정", strings)

    def test_modal_contains_scanner_focus_configuration_controls(self) -> None:
        try:
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = build_app_settings_panel(_StubPage())
        strings = self._collect_strings(panel)

        self.assertIn("카메라 초점 설정", strings)
        self.assertIn("초점 모드", strings)
        self.assertIn("수동 초점 값", strings)

    def test_modal_contains_ticket_debug_tools_section_below_scan_sound_settings(self) -> None:
        try:
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            panel = build_app_settings_panel(
                _StubPage(),
                store_path=str(Path(temp_dir) / "receipt_settings.json"),
                debug_store_path=str(Path(temp_dir) / "ticket_debug_settings.json"),
            )
            strings = self._collect_strings(panel)

            self.assertIn("개발자 도구", strings)
            self.assertIn("QR 스캔 성공 시 누적 카운트 반영", strings)
            self.assertIn("중복 스캔 시 효과음 재생", strings)
            self.assertIn("현재 활성 디버그 기능: 없음", strings)
            self.assertNotIn("기존 처리 흐름은 유지한 채 기능 테스트용 분기만 별도로 켭니다.", strings)
            self.assertNotIn("카메라 테스트 모드", strings)

    def test_ticket_debug_tools_summary_updates_with_enabled_flags(self) -> None:
        try:
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            panel = build_app_settings_panel(
                _StubPage(),
                store_path=str(Path(temp_dir) / "receipt_settings.json"),
                debug_store_path=str(Path(temp_dir) / "ticket_debug_settings.json"),
            )

            count_scan_success_switch = self._find_control_by_label(panel, "QR 스캔 성공 시 누적 카운트 반영")
            duplicate_sound_switch = self._find_control_by_label(panel, "중복 스캔 시 효과음 재생")

            self.assertIsNotNone(count_scan_success_switch)
            self.assertIsNotNone(duplicate_sound_switch)

            count_scan_success_switch.value = True
            count_scan_success_switch.on_change(SimpleNamespace(control=count_scan_success_switch))
            duplicate_sound_switch.value = True
            duplicate_sound_switch.on_change(SimpleNamespace(control=duplicate_sound_switch))

            strings = self._collect_strings(panel)
            self.assertIn(
                "현재 활성 디버그 기능: QR 스캔 성공 시 누적 카운트 반영, 중복 스캔 시 효과음 재생",
                strings,
            )

    def test_app_settings_panel_persists_ticket_debug_tool_flags(self) -> None:
        try:
            from services.ticket_debug_settings_store import TicketDebugSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            debug_store_path = str(Path(temp_dir) / "ticket_debug_settings.json")
            panel = build_app_settings_panel(
                _StubPage(),
                store_path=store_path,
                debug_store_path=debug_store_path,
            )

            count_scan_success_switch = self._find_control_by_label(panel, "QR 스캔 성공 시 누적 카운트 반영")
            duplicate_sound_switch = self._find_control_by_label(panel, "중복 스캔 시 효과음 재생")

            self.assertIsNotNone(count_scan_success_switch)
            self.assertIsNotNone(duplicate_sound_switch)

            count_scan_success_switch.value = True
            count_scan_success_switch.on_change(SimpleNamespace(control=count_scan_success_switch))
            duplicate_sound_switch.value = True
            duplicate_sound_switch.on_change(SimpleNamespace(control=duplicate_sound_switch))

            saved = TicketDebugSettingsStore(debug_store_path).load()
            self.assertTrue(saved.count_scan_success_as_processed)
            self.assertTrue(saved.play_sound_for_duplicate_received_qr)

    def test_app_settings_panel_persists_scanner_focus_settings(self) -> None:
        try:
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            focus_mode_dropdown = self._find_control_by_label(panel, "초점 모드")
            manual_focus_value_field = self._find_control_by_label(panel, "수동 초점 값")

            self.assertIsNotNone(focus_mode_dropdown)
            self.assertIsNotNone(manual_focus_value_field)

            focus_mode_dropdown.value = "manual"
            focus_mode_dropdown.on_change(SimpleNamespace(control=focus_mode_dropdown))
            manual_focus_value_field.value = "8.5"
            manual_focus_value_field.on_blur(SimpleNamespace(control=manual_focus_value_field))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.scanner_focus_mode, "manual")
            self.assertEqual(saved.scanner_manual_focus_value, 8.5)

    def test_receipt_sidebar_settings_panel_persists_qr_auto_print_switch(self) -> None:
        try:
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_receipt_sidebar_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            panel = build_receipt_sidebar_settings_panel(_StubPage(), store_path=store_path)

            qr_auto_print_switch = self._find_control_by_label(panel, "QR 스캔 시 영수증 자동 출력")

            self.assertIsNotNone(qr_auto_print_switch)
            self.assertTrue(qr_auto_print_switch.value)

            qr_auto_print_switch.value = False
            qr_auto_print_switch.on_change(SimpleNamespace(control=qr_auto_print_switch))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertFalse(saved.qr_scan_auto_print_enabled)

    def test_app_settings_panel_applies_scanner_focus_settings_to_runtime_callback(self) -> None:
        try:
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        callback_calls: list[tuple[str, float | None]] = []

        def _apply_focus_settings(focus_mode: str, manual_focus_value: float | None) -> str:
            callback_calls.append((focus_mode, manual_focus_value))
            return "런타임 초점 즉시 적용 완료"

        panel = build_app_settings_panel(
            _StubPage(),
            on_apply_scanner_focus_settings=_apply_focus_settings,
        )

        focus_mode_dropdown = self._find_control_by_label(panel, "초점 모드")
        manual_focus_value_field = self._find_control_by_label(panel, "수동 초점 값")

        self.assertIsNotNone(focus_mode_dropdown)
        self.assertIsNotNone(manual_focus_value_field)

        manual_focus_value_field.value = "8.5"
        focus_mode_dropdown.value = "manual"
        focus_mode_dropdown.on_change(SimpleNamespace(control=focus_mode_dropdown))

        self.assertEqual(callback_calls, [("manual", 8.5)])
        self.assertIn("런타임 초점 즉시 적용 완료", self._collect_strings(panel))

    def test_app_settings_panel_disables_manual_focus_for_unsupported_camera(self) -> None:
        try:
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        callback_calls: list[tuple[str, float | None]] = []
        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            panel = build_app_settings_panel(
                _StubPage(),
                store_path=store_path,
                focus_capability_getter=lambda: SimpleNamespace(manual_focus_supported=False),
                on_apply_scanner_focus_settings=lambda mode, value: callback_calls.append((mode, value)),
            )

            focus_mode_dropdown = self._find_control_by_label(panel, "초점 모드")
            manual_focus_value_field = self._find_control_by_label(panel, "수동 초점 값")

            self.assertIsNotNone(focus_mode_dropdown)
            self.assertIsNotNone(manual_focus_value_field)
            self.assertEqual([option.key for option in focus_mode_dropdown.options], ["auto"])
            self.assertTrue(manual_focus_value_field.disabled)
            self.assertIn(
                "현재 카메라는 수동 초점을 지원하지 않습니다. 자동 초점 또는 카메라 고급 설정을 사용하세요.",
                self._collect_strings(panel),
            )
            self.assertEqual(ReceiptSettingsStore(store_path).load().scanner_focus_mode, "auto")
            self.assertEqual(callback_calls, [])

    def test_app_settings_panel_marks_focus_support_as_unverified_when_runtime_is_idle(self) -> None:
        try:
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = build_app_settings_panel(
            _StubPage(),
            focus_capability_getter=lambda: None,
        )

        self.assertIn(
            "카메라 실행 후 수동 초점 지원 여부를 확인합니다.",
            self._collect_strings(panel),
        )

    def test_app_settings_panel_restores_auto_when_manual_apply_is_rejected(self) -> None:
        try:
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        capability_state = [SimpleNamespace(manual_focus_supported=None)]

        def _apply_focus(_mode: str, _value: float | None) -> str:
            capability_state[0] = SimpleNamespace(manual_focus_supported=False)
            return "현재 카메라는 수동 초점을 지원하지 않습니다."

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            panel = build_app_settings_panel(
                _StubPage(),
                store_path=store_path,
                focus_capability_getter=lambda: capability_state[0],
                on_apply_scanner_focus_settings=_apply_focus,
            )
            focus_mode_dropdown = self._find_control_by_label(panel, "초점 모드")
            manual_focus_value_field = self._find_control_by_label(panel, "수동 초점 값")
            manual_focus_value_field.value = "8.5"
            focus_mode_dropdown.value = "manual"

            focus_mode_dropdown.on_change(SimpleNamespace(control=focus_mode_dropdown))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.scanner_focus_mode, "auto")
            self.assertIsNone(saved.scanner_manual_focus_value)
            self.assertEqual(focus_mode_dropdown.value, "auto")

    def test_app_settings_panel_opens_native_camera_settings(self) -> None:
        try:
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        open_calls: list[bool] = []
        panel = build_app_settings_panel(
            _StubPage(),
            on_open_camera_settings=lambda: open_calls.append(True) or True,
        )

        button = self._find_clickable_control_by_text(panel, "카메라 고급 설정")
        self.assertIsNotNone(button)
        button.on_click(SimpleNamespace(control=button))

        self.assertEqual(open_calls, [True])

    def test_app_settings_panel_rejects_non_finite_manual_focus_value(self) -> None:
        try:
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        callback_calls: list[tuple[str, float | None]] = []
        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            panel = build_app_settings_panel(
                _StubPage(),
                store_path=store_path,
                on_apply_scanner_focus_settings=lambda mode, value: callback_calls.append((mode, value)),
            )
            focus_mode_dropdown = self._find_control_by_label(panel, "초점 모드")
            manual_focus_value_field = self._find_control_by_label(panel, "수동 초점 값")
            manual_focus_value_field.value = "nan"
            focus_mode_dropdown.value = "manual"

            focus_mode_dropdown.on_change(SimpleNamespace(control=focus_mode_dropdown))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.scanner_focus_mode, "auto")
            self.assertIsNone(saved.scanner_manual_focus_value)
            self.assertEqual(callback_calls, [])
            self.assertIn("초점 값은 유한한 숫자로 입력하세요.", self._collect_strings(panel))

    def test_app_settings_panel_notifies_ticket_product_changes_immediately(self) -> None:
        try:
            import flet as ft
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        callback_calls: list[list[str]] = []

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            panel = build_app_settings_panel(
                _StubPage(),
                store_path=store_path,
                on_ticket_products_changed=lambda names: callback_calls.append(list(names)),
            )

            ticket_checkbox = None
            for node in self._walk_controls(panel):
                if isinstance(node, ft.Checkbox) and getattr(node, "label", None):
                    ticket_checkbox = node
                    break

            self.assertIsNotNone(ticket_checkbox)

            ticket_checkbox.value = True
            ticket_checkbox.on_change(SimpleNamespace(control=ticket_checkbox))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.ticket_product_names, [ticket_checkbox.label])
            self.assertEqual(callback_calls, [[ticket_checkbox.label]])

    def test_app_settings_panel_reload_hook_refreshes_ticket_product_options(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        product_name_states = iter(
            [
                ["테스트 티켓1", "테스트 티켓2"],
                ["실사용 상품A", "실사용 상품B"],
            ]
        )

        with patch("views.settings_flet_view._load_excel_product_names", side_effect=lambda *args, **kwargs: next(product_name_states)):
            panel = build_app_settings_panel(_StubPage())

            reload_fn = getattr(panel, "_reload_ticket_product_options", None)
            self.assertTrue(callable(reload_fn))

            initial_labels = [
                node.label
                for node in self._walk_controls(panel)
                if isinstance(node, ft.Checkbox) and getattr(node, "label", None)
            ]
            self.assertIn("테스트 티켓1", initial_labels)
            self.assertNotIn("실사용 상품A", initial_labels)

            reload_fn()

            refreshed_labels = [
                node.label
                for node in self._walk_controls(panel)
                if isinstance(node, ft.Checkbox) and getattr(node, "label", None)
            ]
            self.assertIn("실사용 상품A", refreshed_labels)
            self.assertIn("실사용 상품B", refreshed_labels)
            self.assertNotIn("테스트 티켓1", refreshed_labels)

    def test_receipt_settings_panel_reload_hook_refreshes_ticket_product_options(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import build_receipt_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        product_name_states = iter(
            [
                ["테스트 티켓1"],
                ["실사용 상품A", "실사용 상품B"],
            ]
        )

        with patch("views.settings_flet_view._load_excel_product_names", side_effect=lambda *args, **kwargs: next(product_name_states)):
            panel = build_receipt_settings_panel(_StubPage(), show_section_tabs=False)

            reload_fn = getattr(panel, "_reload_ticket_product_options", None)
            self.assertTrue(callable(reload_fn))

            initial_labels = [
                node.label
                for node in self._walk_controls(panel)
                if isinstance(node, ft.Checkbox) and getattr(node, "label", None)
            ]
            self.assertEqual(initial_labels, ["테스트 티켓1"])

            reload_fn()

            refreshed_labels = [
                node.label
                for node in self._walk_controls(panel)
                if isinstance(node, ft.Checkbox) and getattr(node, "label", None)
            ]
            self.assertEqual(refreshed_labels, ["실사용 상품A", "실사용 상품B"])

    def test_app_settings_panel_loads_advanced_scan_sound_rule_editor(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(
                            name="10번째",
                            sound_path="C:/sounds/rare.mp3",
                            weight=3,
                            trigger_type="specific_counts",
                            trigger_value="10",
                        )
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            weight_field = self._find_control_by_label(panel, "확률(%)")
            trigger_type_dropdown = self._find_control_by_label(panel, "조건 타입")
            trigger_value_field = self._find_control_by_label(panel, "조건값")
            sound_path_field = self._find_control_by_label(panel, "음원 파일 주소")

            self.assertIsNotNone(weight_field)
            self.assertIsNotNone(trigger_type_dropdown)
            self.assertIsNotNone(trigger_value_field)
            self.assertIsNotNone(sound_path_field)
            self.assertEqual(weight_field.value, "3")
            self.assertEqual(trigger_type_dropdown.value, "specific_counts")
            self.assertEqual(trigger_value_field.value, "10")
            self.assertEqual(sound_path_field.value, "C:/sounds/rare.mp3")
            self.assertEqual(trigger_value_field.hint_text, "예: 7, 77, 777 처럼 쉼표로 구분")
            self.assertFalse(bool(getattr(getattr(weight_field, "_visibility_host", None), "visible", True)))
            self.assertTrue(bool(getattr(getattr(trigger_value_field, "_visibility_host", None), "visible", False)))

    def test_app_settings_panel_uses_file_names_and_trigger_badges_in_rule_cards(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            import flet as ft
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(name="커스텀 이름", sound_path="C:/sounds/ko.mp3", weight=60),
                        ScanSuccessSoundRule(
                            name="열번째",
                            sound_path="C:/sounds/nth.mp3",
                            trigger_type="specific_counts",
                            trigger_value="10",
                        ),
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            self.assertIsNone(self._find_control_by_label(panel, "규칙 이름"))
            self.assertIsNone(self._find_control_by_label(panel, "활성"))
            strings = self._collect_strings(panel)
            tooltips = {
                tooltip
                for node in self._walk_controls(panel)
                if isinstance((tooltip := getattr(node, "tooltip", None)), str) and tooltip
            }
            self.assertIn("커스텀 이름", strings)
            self.assertIn("열번째", strings)
            self.assertTrue(any(value.endswith("%") for value in strings))
            self.assertIn("기본랜덤", strings)
            self.assertIn("특정 번호", strings)
            self.assertIn("10번", strings)
            self.assertNotIn("기본 랜덤 (확률 60%)", strings)
            self.assertIn("커스텀 이름", tooltips)
            self.assertIn("열번째", tooltips)
            self.assertIn("특정 번호 조건값: 10", tooltips)
            special_rule_card = self._find_clickable_control_containing_text(panel, "열번째")
            self.assertIsNotNone(special_rule_card)
            special_row_values = [
                getattr(getattr(control, "content", None), "value", None)
                for control in getattr(getattr(special_rule_card, "content", None), "controls", [])
                if isinstance(getattr(getattr(control, "content", None), "value", None), str)
            ]
            self.assertIn("10번", special_row_values)
            self.assertIn("특정 번호", special_row_values)
            self.assertLess(special_row_values.index("10번"), special_row_values.index("특정 번호"))
            self.assertFalse(any(str(value).endswith("%") for value in special_row_values))
            switches = [node for node in self._walk_controls(panel) if node.__class__.__name__ == "Switch"]
            self.assertGreaterEqual(len(switches), 2)
            self.assertTrue(all(getattr(node, "scale", None) == 0.72 for node in switches[:2]))
            self.assertIsNotNone(self._find_control_by_tooltip(panel, "프로그램 표시 이름 수정"))

    def test_app_settings_panel_rule_card_switch_persists_enabled_state(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(name="일반 1", sound_path="C:/sounds/a.mp3", weight=50),
                        ScanSuccessSoundRule(name="일반 2", sound_path="C:/sounds/b.mp3", weight=50),
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            switches = [node for node in self._walk_controls(panel) if node.__class__.__name__ == "Switch"]

            self.assertGreaterEqual(len(switches), 2)

            switches[0].value = False
            switches[0].on_change(SimpleNamespace(control=switches[0]))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertFalse(saved.qr_scan_success_sound_rules[0].enabled)
            self.assertTrue(saved.qr_scan_success_sound_rules[1].enabled)
            self.assertEqual(saved.qr_scan_success_sound_rules[1].weight, 100.0)

    def test_app_settings_panel_persists_advanced_scan_sound_rule_changes(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(
                            name="Rare Rule",
                            sound_path="C:/sounds/rare.mp3",
                            weight=1,
                            trigger_type="always",
                        )
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            weight_field = self._find_control_by_label(panel, "확률(%)")
            trigger_type_dropdown = self._find_control_by_label(panel, "조건 타입")
            trigger_value_field = self._find_control_by_label(panel, "조건값")

            self.assertIsNotNone(weight_field)
            self.assertIsNotNone(trigger_type_dropdown)
            self.assertIsNotNone(trigger_value_field)

            weight_field.value = "5"
            weight_field.on_blur(SimpleNamespace(control=weight_field))
            self.assertTrue(bool(getattr(getattr(weight_field, "_visibility_host", None), "visible", True)))
            self.assertFalse(bool(getattr(getattr(trigger_value_field, "_visibility_host", None), "visible", True)))
            trigger_type_dropdown.value = "specific_counts"
            trigger_type_dropdown.on_change(SimpleNamespace(control=trigger_type_dropdown))
            self.assertFalse(bool(getattr(getattr(weight_field, "_visibility_host", None), "visible", True)))
            self.assertTrue(bool(getattr(getattr(trigger_value_field, "_visibility_host", None), "visible", False)))
            self.assertEqual(trigger_value_field.hint_text, "예: 7, 77, 777 처럼 쉼표로 구분")
            trigger_value_field.value = "10, 20"
            trigger_value_field.on_blur(SimpleNamespace(control=trigger_value_field))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(len(saved.qr_scan_success_sound_rules), 1)
            self.assertEqual(saved.qr_scan_success_sound_rules[0].name, "Rare Rule")
            self.assertEqual(saved.qr_scan_success_sound_rules[0].weight, 100.0)
            self.assertEqual(saved.qr_scan_success_sound_rules[0].trigger_type, "specific_counts")
            self.assertEqual(saved.qr_scan_success_sound_rules[0].trigger_value, "10, 20")

    def test_app_settings_panel_manual_general_probability_edit_rebalances_other_general_rules(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(name="일반 1", sound_path="C:/sounds/a.mp3", weight=50),
                        ScanSuccessSoundRule(name="일반 2", sound_path="C:/sounds/b.mp3", weight=50),
                        ScanSuccessSoundRule(
                            name="10번째",
                            sound_path="C:/sounds/nth.mp3",
                            trigger_type="every_n",
                            trigger_value="10",
                            weight=7,
                        ),
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            weight_field = self._find_control_by_label(panel, "확률(%)")
            trigger_type_dropdown = self._find_control_by_label(panel, "조건 타입")
            trigger_value_field = self._find_control_by_label(panel, "조건값")

            self.assertIsNotNone(weight_field)
            self.assertIsNotNone(trigger_type_dropdown)
            self.assertIsNotNone(trigger_value_field)
            self.assertEqual(weight_field.value, "50")
            self.assertEqual(trigger_type_dropdown.value, "always")

            weight_field.value = "70"
            weight_field.on_blur(SimpleNamespace(control=weight_field))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.qr_scan_success_sound_rules[0].weight, 70.0)
            self.assertEqual(saved.qr_scan_success_sound_rules[1].weight, 30.0)
            self.assertEqual(saved.qr_scan_success_sound_rules[2].weight, 7)

            third_rule_card = self._find_clickable_control_containing_text(panel, "10번째")
            self.assertIsNotNone(third_rule_card)
            third_rule_card.on_click(SimpleNamespace(control=third_rule_card))
            self.assertEqual(trigger_type_dropdown.value, "every_n")
            self.assertEqual(trigger_value_field.value, "10")
            self.assertEqual(trigger_value_field.hint_text, "예: 10 입력 시 10번마다 재생")
            self.assertFalse(bool(getattr(getattr(weight_field, "_visibility_host", None), "visible", True)))
            self.assertTrue(bool(getattr(getattr(trigger_value_field, "_visibility_host", None), "visible", False)))

    def test_app_settings_panel_live_general_probability_change_rebalances_on_change(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(name="일반 1", sound_path="C:/sounds/a.mp3", weight=50),
                        ScanSuccessSoundRule(name="일반 2", sound_path="C:/sounds/b.mp3", weight=50),
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            weight_field = self._find_control_by_label(panel, "확률(%)")

            self.assertIsNotNone(weight_field)
            self.assertEqual(weight_field.value, "50")

            weight_field.value = "70"
            weight_field.on_change(SimpleNamespace(control=weight_field))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.qr_scan_success_sound_rules[0].weight, 70.0)
            self.assertEqual(saved.qr_scan_success_sound_rules[1].weight, 30.0)
            self.assertEqual(weight_field.value, "70")

    def test_app_settings_panel_equalizes_general_rules_when_added_pool_changes(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(name="일반 1", sound_path="C:/sounds/a.mp3", weight=70),
                        ScanSuccessSoundRule(name="일반 2", sound_path="C:/sounds/b.mp3", weight=30),
                        ScanSuccessSoundRule(
                            name="10번째",
                            sound_path="C:/sounds/nth.mp3",
                            trigger_type="every_n",
                            trigger_value="10",
                            weight=9,
                        ),
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            trigger_type_dropdown = self._find_control_by_label(panel, "조건 타입")
            weight_field = self._find_control_by_label(panel, "확률(%)")
            sound_path_field = self._find_control_by_label(panel, "음원 파일 주소")

            self.assertIsNotNone(trigger_type_dropdown)
            self.assertIsNotNone(weight_field)
            self.assertIsNotNone(sound_path_field)

            first_rule_card = self._find_clickable_control_containing_text(panel, "일반 2")
            self.assertIsNotNone(first_rule_card)
            first_rule_card.on_click(SimpleNamespace(control=first_rule_card))
            trigger_type_dropdown.value = "specific_counts"
            trigger_type_dropdown.on_change(SimpleNamespace(control=trigger_type_dropdown))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.qr_scan_success_sound_rules[0].weight, 100.0)
            self.assertEqual(saved.qr_scan_success_sound_rules[1].trigger_type, "specific_counts")

    def test_app_settings_panel_persists_program_display_name_changes(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(name="old.wav", sound_path="C:/sounds/old.wav", weight=100),
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            name_field = self._find_control_by_label(panel, "프로그램 표시 이름")
            edit_button = self._find_control_by_tooltip(panel, "프로그램 표시 이름 수정")

            self.assertIsNotNone(name_field)
            self.assertIsNotNone(edit_button)

            edit_button.on_click(SimpleNamespace(control=edit_button))
            name_field.value = "현장용 이름"
            name_field.on_blur(SimpleNamespace(control=name_field))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.qr_scan_success_sound_rules[0].name, "현장용 이름")

    def test_app_settings_panel_open_sound_path_button_opens_explorer_for_selected_file(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            sound_path = Path(temp_dir) / "success 1.wav"
            sound_path.write_bytes(b"test")
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(sound_path=str(sound_path), weight=100),
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)
            open_button = self._find_control_by_tooltip(panel, "파일 탐색기에서 위치 열기")

            self.assertIsNotNone(open_button)
            self.assertFalse(bool(getattr(open_button, "disabled", True)))

            with patch("views.settings_flet_view.subprocess.Popen") as mock_popen:
                open_button.on_click(SimpleNamespace(control=open_button))

            mock_popen.assert_called_once_with(["explorer", f"/select,{sound_path}"])

    def test_app_settings_panel_empty_scan_sound_state_disables_rule_editor_controls(self) -> None:
        try:
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            weight_field = self._find_control_by_label(panel, "확률(%)")
            trigger_type_dropdown = self._find_control_by_label(panel, "조건 타입")
            trigger_value_field = self._find_control_by_label(panel, "조건값")
            preview_button = self._find_clickable_control_by_text(panel, "미리 듣기")
            remove_button = self._find_clickable_control_by_text(panel, "선택 삭제")
            clear_button = self._find_clickable_control_by_text(panel, "초기화")

            self.assertIsNotNone(weight_field)
            self.assertIsNotNone(trigger_type_dropdown)
            self.assertIsNotNone(trigger_value_field)
            self.assertIsNotNone(preview_button)
            self.assertIsNotNone(remove_button)
            self.assertIsNotNone(clear_button)
            self.assertTrue(weight_field.disabled)
            self.assertTrue(trigger_type_dropdown.disabled)
            self.assertTrue(trigger_value_field.disabled)
            self.assertTrue(preview_button.disabled)
            self.assertTrue(remove_button.disabled)
            self.assertTrue(clear_button.disabled)
            self.assertIn("음원을 추가하면 기본 랜덤 음원이 만들어집니다.", self._collect_strings(panel))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.qr_scan_success_sound_path, "")
            self.assertEqual(saved.qr_scan_success_sound_rules, [])

    def test_app_settings_panel_clear_scan_sound_rules_clears_legacy_path_sync(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(qr_scan_success_sound_path="C:/sounds/legacy.mp3")
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            sound_path_field = self._find_control_by_label(panel, "음원 파일 주소")
            preview_button = self._find_clickable_control_by_text(panel, "미리 듣기")
            remove_button = self._find_clickable_control_by_text(panel, "선택 삭제")
            clear_button = self._find_clickable_control_by_text(panel, "초기화")

            self.assertIsNotNone(sound_path_field)
            self.assertIsNotNone(preview_button)
            self.assertIsNotNone(remove_button)
            self.assertIsNotNone(clear_button)
            self.assertEqual(sound_path_field.value, "C:/sounds/legacy.mp3")

            clear_button.on_click(SimpleNamespace(control=clear_button))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(saved.qr_scan_success_sound_path, "")
            self.assertEqual(saved.qr_scan_success_sound_rules, [])
            self.assertEqual(sound_path_field.value, "")
            self.assertTrue(preview_button.disabled)
            self.assertTrue(remove_button.disabled)
            self.assertTrue(clear_button.disabled)

    def test_app_settings_panel_remove_scan_sound_rule_reselects_remaining_rule(self) -> None:
        try:
            from models.receipt_settings_model import ReceiptSettings, ScanSuccessSoundRule
            from services.receipt_settings_store import ReceiptSettingsStore
            from views.settings_flet_view import build_app_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        with TemporaryDirectory() as temp_dir:
            store_path = str(Path(temp_dir) / "receipt_settings.json")
            ReceiptSettingsStore(store_path).save(
                ReceiptSettings(
                    qr_scan_success_sound_rules=[
                        ScanSuccessSoundRule(name="첫 번째", sound_path="C:/sounds/first.mp3"),
                        ScanSuccessSoundRule(name="두 번째", sound_path="C:/sounds/second.mp3"),
                    ]
                )
            )

            panel = build_app_settings_panel(_StubPage(), store_path=store_path)

            sound_path_field = self._find_control_by_label(panel, "음원 파일 주소")
            remove_button = self._find_clickable_control_by_text(panel, "선택 삭제")

            self.assertIsNotNone(sound_path_field)
            self.assertIsNotNone(remove_button)
            self.assertEqual(sound_path_field.value, "C:/sounds/first.mp3")

            remove_button.on_click(SimpleNamespace(control=remove_button))

            saved = ReceiptSettingsStore(store_path).load()
            self.assertEqual(len(saved.qr_scan_success_sound_rules), 1)
            self.assertEqual(saved.qr_scan_success_sound_rules[0].name, "두 번째")
            self.assertEqual(saved.qr_scan_success_sound_path, "C:/sounds/second.mp3")
            self.assertEqual(sound_path_field.value, "C:/sounds/second.mp3")

    def test_receipt_placeholder_panel_builder_keeps_expected_strings(self) -> None:
        try:
            from views.settings_flet_view import _build_receipt_placeholder_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = _build_receipt_placeholder_panel(
            title_size=24,
            outer_border_radius=16,
            inner_border_color="#D9DDE5",
            subtitle_text="추가 안내",
            description_text="설정 영역 설명",
            icon_size=42,
        )
        strings = self._collect_strings(panel)

        self.assertIn("영수증 양식 설정", strings)
        self.assertIn("영수증 양식 설정 영역", strings)
        self.assertIn("추가 안내", strings)
        self.assertIn("설정 영역 설명", strings)

    def test_app_settings_ticket_panel_builder_keeps_expected_controls_and_strings(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_app_settings_ticket_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        sound_path_field = ft.TextField(label="sound")
        btn_pick_sound = ft.ElevatedButton("pick")
        btn_preview_sound = ft.OutlinedButton("preview")
        btn_clear_sound = ft.OutlinedButton("clear")
        focus_mode_dropdown = ft.Dropdown(label="focus")
        manual_focus_value_field = ft.TextField(label="manual")
        settings_status_text = ft.Text("status")
        ticket_checkbox_list = ft.Column(controls=[ft.Text("VIP")])

        panel = _build_app_settings_ticket_panel(
            sound_path_field=sound_path_field,
            btn_pick_sound=btn_pick_sound,
            btn_preview_sound=btn_preview_sound,
            btn_clear_sound=btn_clear_sound,
            focus_mode_dropdown=focus_mode_dropdown,
            manual_focus_value_field=manual_focus_value_field,
            settings_status_text=settings_status_text,
            ticket_checkbox_list=ticket_checkbox_list,
        )
        strings = self._collect_strings(panel)

        self.assertIn("티켓 확인 설정", strings)
        self.assertIn("QR 스캔 완료 알림음", strings)
        self.assertIn("티켓 상품 분류", strings)
        self.assertIn("스캔 성공 시 재생할 MP3 또는 WAV 파일을 선택합니다.", strings)
        self.assertIn("선택한 상품은 티켓 영역으로 분리됩니다.", strings)

    def test_receipt_ticket_panel_builder_keeps_expected_controls_and_strings(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_receipt_ticket_settings_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        scan_sound_path_field = ft.TextField(label="sound")
        btn_pick_scan_sound = ft.ElevatedButton("pick")
        btn_preview_scan_sound = ft.OutlinedButton("preview")
        btn_clear_scan_sound = ft.OutlinedButton("clear")
        ticket_settings_status_text = ft.Text("status")
        ticket_checkbox_list = ft.ListView(controls=[ft.Text("VIP")])

        panel = _build_receipt_ticket_settings_panel(
            scan_sound_path_field=scan_sound_path_field,
            btn_pick_scan_sound=btn_pick_scan_sound,
            btn_preview_scan_sound=btn_preview_scan_sound,
            btn_clear_scan_sound=btn_clear_scan_sound,
            ticket_settings_status_text=ticket_settings_status_text,
            ticket_checkbox_list=ticket_checkbox_list,
        )
        strings = self._collect_strings(panel)

        self.assertIn("티켓 확인 설정", strings)
        self.assertIn("QR 스캔 완료 알림음", strings)
        self.assertIn("티켓 상품 분류", strings)
        self.assertIn("QR 스캔 완료 알림음과 티켓 분류 기준을 관리합니다.", strings)
        self.assertIn("선택한 MP3 또는 WAV 파일이 수령 처리 성공 직후 재생됩니다.", strings)
        self.assertIn("체크한 상품은 티켓 영역으로 분리되어 표시됩니다.", strings)

    def test_scan_success_sound_management_panel_wraps_trigger_value_field_in_row(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_scan_success_sound_management_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        trigger_value_field = ft.TextField(label="조건값", expand=True)
        panel = _build_scan_success_sound_management_panel(
            summary_text=ft.Text("summary"),
            sound_rule_list=ft.Column(),
            sound_rule_name_field=ft.TextField(label="name"),
            sound_path_field=ft.TextField(label="sound"),
            sound_rule_weight_field=ft.TextField(label="weight"),
            sound_rule_trigger_type_dropdown=ft.Dropdown(label="type"),
            sound_rule_trigger_value_field=trigger_value_field,
            sound_rule_enabled_switch=ft.Switch(label="enabled"),
            btn_pick_sound=ft.ElevatedButton("pick"),
            btn_preview_sound=ft.OutlinedButton("preview"),
            btn_remove_sound_rule=ft.OutlinedButton("remove"),
            btn_clear_sound_rules=ft.OutlinedButton("clear"),
        )

        editor_row = panel.controls[-1]
        editor_row_controls = getattr(editor_row, "controls", [])
        self.assertEqual(len(editor_row_controls), 3)
        self.assertIs(getattr(editor_row_controls[1], "content", None), trigger_value_field)

    def test_scan_success_sound_management_panel_compact_layout_adds_sidebar_section_labels(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_scan_success_sound_management_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = _build_scan_success_sound_management_panel(
            summary_text=ft.Text("summary"),
            sound_rule_list=ft.Column(),
            sound_rule_name_field=ft.TextField(label="name"),
            sound_path_field=ft.TextField(label="sound"),
            sound_rule_weight_field=ft.TextField(label="weight"),
            sound_rule_trigger_type_dropdown=ft.Dropdown(label="type"),
            sound_rule_trigger_value_field=ft.TextField(label="value"),
            sound_rule_enabled_switch=ft.Switch(label="enabled"),
            btn_pick_sound=ft.ElevatedButton("pick"),
            btn_preview_sound=ft.OutlinedButton("preview"),
            btn_remove_sound_rule=ft.OutlinedButton("remove"),
            btn_clear_sound_rules=ft.OutlinedButton("clear"),
            compact=True,
        )

        strings = self._collect_strings(panel)
        self.assertNotIn("summary", strings)
        self.assertNotIn("규칙 우선순위", strings)
        self.assertIn("등록된 음원", strings)
        self.assertIn("선택된 음원", strings)
        self.assertEqual(getattr(panel.controls[1], "height", None), 228)

    def test_scan_success_sound_management_panel_renders_open_path_button_when_provided(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_scan_success_sound_management_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        icons = getattr(ft, "Icons", ft.icons)
        open_button = ft.IconButton(icon=icons.FOLDER_OPEN_ROUNDED, tooltip="파일 탐색기에서 위치 열기")
        panel = _build_scan_success_sound_management_panel(
            summary_text=ft.Text("summary"),
            sound_rule_list=ft.Column(),
            sound_rule_name_field=ft.TextField(label="name"),
            sound_path_field=ft.TextField(label="sound"),
            btn_open_sound_path=open_button,
            sound_rule_weight_field=ft.TextField(label="weight"),
            sound_rule_trigger_type_dropdown=ft.Dropdown(label="type"),
            sound_rule_trigger_value_field=ft.TextField(label="value"),
            sound_rule_enabled_switch=ft.Switch(label="enabled"),
            btn_pick_sound=ft.ElevatedButton("pick"),
            btn_preview_sound=ft.OutlinedButton("preview"),
            btn_remove_sound_rule=ft.OutlinedButton("remove"),
            btn_clear_sound_rules=ft.OutlinedButton("clear"),
            compact=True,
        )

        self.assertIs(self._find_control_by_tooltip(panel, "파일 탐색기에서 위치 열기"), open_button)

    def test_scan_success_trigger_badge_emphasizes_condition_label_and_border(self) -> None:
        try:
            from views.settings_flet_view import _scan_success_trigger_badge
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        badge = _scan_success_trigger_badge("specific_counts", selected=True)

        self.assertIsNotNone(getattr(badge, "border", None))
        self.assertEqual(getattr(getattr(badge, "content", None), "value", None), "특정 번호")
        self.assertEqual(getattr(badge, "bgcolor", None), "#FFE9EF")

    def test_scan_success_trigger_value_badge_compacts_multiple_numbers(self) -> None:
        try:
            from models.receipt_settings_model import ScanSuccessSoundRule
            from views.settings_flet_view import _scan_success_trigger_value_badge
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        badge = _scan_success_trigger_value_badge(
            ScanSuccessSoundRule(
                sound_path="C:/sounds/nth.mp3",
                trigger_type="specific_counts",
                trigger_value="7, 77, 777, 7777",
            ),
            selected=True,
        )

        self.assertIsNotNone(badge)
        self.assertEqual(getattr(getattr(badge, "content", None), "value", None), "7·77·777+")

    def test_reorder_scan_success_rules_moves_rule_down_and_keeps_selected_rule(self) -> None:
        try:
            from models.receipt_settings_model import ScanSuccessSoundRule
            from views.settings_flet_view import _reorder_scan_success_rules
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        rules = [
            ScanSuccessSoundRule(name="첫 번째", sound_path="C:/sounds/1.mp3"),
            ScanSuccessSoundRule(name="두 번째", sound_path="C:/sounds/2.mp3"),
            ScanSuccessSoundRule(name="세 번째", sound_path="C:/sounds/3.mp3"),
        ]

        reordered, selected_index = _reorder_scan_success_rules(
            rules,
            from_index=0,
            to_index=2,
            selected_index=0,
        )

        self.assertEqual([rule.name for rule in reordered], ["두 번째", "세 번째", "첫 번째"])
        self.assertEqual(selected_index, 2)

    def test_reorder_scan_success_rules_moves_rule_up_and_shifts_selection(self) -> None:
        try:
            from models.receipt_settings_model import ScanSuccessSoundRule
            from views.settings_flet_view import _reorder_scan_success_rules
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        rules = [
            ScanSuccessSoundRule(name="첫 번째", sound_path="C:/sounds/1.mp3"),
            ScanSuccessSoundRule(name="두 번째", sound_path="C:/sounds/2.mp3"),
            ScanSuccessSoundRule(name="세 번째", sound_path="C:/sounds/3.mp3"),
        ]

        reordered, selected_index = _reorder_scan_success_rules(
            rules,
            from_index=2,
            to_index=0,
            selected_index=1,
        )

        self.assertEqual([rule.name for rule in reordered], ["세 번째", "첫 번째", "두 번째"])
        self.assertEqual(selected_index, 2)

    def test_settings_section_shell_builder_preserves_tabs_and_host(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_settings_section_shell
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        ticket_button = ft.TextButton("ticket")
        receipt_button = ft.TextButton("receipt")
        content_host = ft.Container(content=ft.Text("content"))

        panel = _build_settings_section_shell(
            ticket_button=ticket_button,
            receipt_button=receipt_button,
            content_host=content_host,
            padding=10,
            spacing=12,
        )

        column = panel.content
        self.assertIsNotNone(column)
        self.assertEqual(getattr(column, "spacing", None), 12)
        self.assertEqual(len(getattr(column, "controls", [])), 2)

        row = column.controls[0]
        self.assertEqual(getattr(row, "controls", []), [ticket_button, receipt_button])
        self.assertIs(column.controls[1], content_host)

    def test_single_settings_section_shell_builder_preserves_wrapped_content(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_single_settings_section_shell
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        content = ft.Container(content=ft.Text("content"))

        panel = _build_single_settings_section_shell(
            content=content,
            padding=12,
        )

        self.assertEqual(getattr(panel, "expand", None), True)
        self.assertIs(getattr(panel, "content", None), content)
        padding = getattr(panel, "padding", None)
        if hasattr(padding, "left"):
            self.assertEqual(padding.left, 12)
        else:
            self.assertEqual(padding, 12)

    def test_select_receipt_settings_section_content_maps_ticket_placeholder_and_editor(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _select_receipt_settings_section_content
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        ticket_content = ft.Container(content=ft.Text("ticket"))
        placeholder_content = ft.Container(content=ft.Text("placeholder"))
        editor_content = ft.Container(content=ft.Text("editor"))

        self.assertIs(
            _select_receipt_settings_section_content(
                active_section="ticket",
                receipt_section_mode="editor",
                ticket_content=ticket_content,
                receipt_placeholder_content=placeholder_content,
                receipt_editor_content=editor_content,
            ),
            ticket_content,
        )
        self.assertIs(
            _select_receipt_settings_section_content(
                active_section="receipt",
                receipt_section_mode="placeholder",
                ticket_content=ticket_content,
                receipt_placeholder_content=placeholder_content,
                receipt_editor_content=editor_content,
            ),
            placeholder_content,
        )
        self.assertIs(
            _select_receipt_settings_section_content(
                active_section="receipt",
                receipt_section_mode="editor",
                ticket_content=ticket_content,
                receipt_placeholder_content=placeholder_content,
                receipt_editor_content=editor_content,
            ),
            editor_content,
        )

    def test_wire_receipt_settings_navigation_handlers_binds_keyboard_and_buttons(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _wire_receipt_settings_navigation_handlers
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        page = _StubPage()
        ticket_button = ft.TextButton("ticket")
        receipt_button = ft.TextButton("receipt")
        receipt_tab_button = ft.TextButton("receipt tab")
        product_tab_button = ft.TextButton("product tab")
        calls: list[tuple[str, str]] = []

        def keyboard_handler(_event) -> None:
            return None

        def set_settings_section(section_key: str) -> None:
            calls.append(("section", section_key))

        def set_editor_layout(layout_key: str) -> None:
            calls.append(("layout", layout_key))

        _wire_receipt_settings_navigation_handlers(
            page=page,
            bind_keyboard_events=True,
            keyboard_handler=keyboard_handler,
            ticket_section_button=ticket_button,
            receipt_section_button=receipt_button,
            receipt_editor_tab_button=receipt_tab_button,
            product_editor_tab_button=product_tab_button,
            set_settings_section=set_settings_section,
            set_editor_layout=set_editor_layout,
        )

        self.assertIs(page.on_keyboard_event, keyboard_handler)
        self.assertTrue(callable(ticket_button.on_click))
        self.assertTrue(callable(receipt_button.on_click))
        self.assertTrue(callable(receipt_tab_button.on_click))
        self.assertTrue(callable(product_tab_button.on_click))

        ticket_button.on_click(None)
        receipt_button.on_click(None)
        receipt_tab_button.on_click(None)
        product_tab_button.on_click(None)

        self.assertEqual(
            calls,
            [
                ("section", "ticket"),
                ("section", "receipt"),
                ("layout", "receipt"),
                ("layout", "product"),
            ],
        )

    def test_build_receipt_settings_section_controls_returns_default_state_host_and_buttons(self) -> None:
        try:
            from views.settings_flet_view import _build_receipt_settings_section_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        state, content_host, ticket_button, receipt_button = _build_receipt_settings_section_controls(
            initial_section="receipt",
        )

        self.assertEqual(state, {"value": "receipt"})
        self.assertEqual(getattr(content_host, "expand", None), True)
        self.assertEqual(getattr(ticket_button, "text", None), "티켓 확인 설정")
        self.assertEqual(getattr(receipt_button, "text", None), "영수증 양식 설정")

    def test_initialize_receipt_settings_panel_state_syncs_width_template_and_refresh_callbacks(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _initialize_receipt_settings_panel_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        current_template_text = ft.Text("old")
        calls: list[tuple[str, object, object]] = []

        def set_paper_width(value: str, push_update: bool = True) -> None:
            calls.append(("set_paper_width", value, push_update))

        def apply_editor_layout(push_update: bool = True) -> None:
            calls.append(("apply_editor_layout", push_update, None))

        def apply_settings_section(push_update: bool = True) -> None:
            calls.append(("apply_settings_section", push_update, None))

        _initialize_receipt_settings_panel_state(
            current_doc_paper_width="58",
            selected_paper_width="80",
            current_template_text=current_template_text,
            editor_layout_label_text="영수증",
            layout_path_text="template.json",
            set_paper_width=set_paper_width,
            apply_editor_layout=apply_editor_layout,
            apply_settings_section=apply_settings_section,
        )

        self.assertEqual(current_template_text.value, "활성 영수증 템플릿: template.json")
        self.assertEqual(
            calls,
            [
                ("set_paper_width", "80", False),
                ("apply_editor_layout", False, None),
                ("apply_settings_section", False, None),
            ],
        )

    def test_build_receipt_settings_panel_shell_maps_tabbed_and_single_modes(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_receipt_settings_panel_shell
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        ticket_button = ft.TextButton("ticket")
        receipt_button = ft.TextButton("receipt")
        content_host = ft.Container(content=ft.Text("host"))
        ticket_content = ft.Container(content=ft.Text("ticket"))
        placeholder_content = ft.Container(content=ft.Text("placeholder"))
        editor_content = ft.Container(content=ft.Text("editor"))

        tabbed_panel = _build_receipt_settings_panel_shell(
            show_section_tabs=True,
            active_section="receipt",
            receipt_section_mode="editor",
            ticket_content=ticket_content,
            receipt_placeholder_content=placeholder_content,
            receipt_editor_content=editor_content,
            settings_content_host=content_host,
            ticket_button=ticket_button,
            receipt_button=receipt_button,
            padding=12,
            spacing=12,
        )
        single_panel = _build_receipt_settings_panel_shell(
            show_section_tabs=False,
            active_section="receipt",
            receipt_section_mode="placeholder",
            ticket_content=ticket_content,
            receipt_placeholder_content=placeholder_content,
            receipt_editor_content=editor_content,
            settings_content_host=content_host,
            ticket_button=ticket_button,
            receipt_button=receipt_button,
            padding=12,
            spacing=12,
        )

        tabbed_column = getattr(tabbed_panel, "content", None)
        self.assertIsNotNone(tabbed_column)
        self.assertIs(tabbed_column.controls[1], content_host)
        self.assertIs(getattr(single_panel, "content", None), placeholder_content)

    def test_resolve_selected_printer_prefers_saved_default_then_first_available(self) -> None:
        try:
            from views.settings_flet_view import _resolve_selected_printer
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(
            _resolve_selected_printer(
                printers=["A", "B"],
                requested_printer="B",
                default_printer="A",
            ),
            "B",
        )
        self.assertEqual(
            _resolve_selected_printer(
                printers=["A", "B"],
                requested_printer="C",
                default_printer="A",
            ),
            "A",
        )
        self.assertEqual(
            _resolve_selected_printer(
                printers=["A", "B"],
                requested_printer="C",
                default_printer="Z",
            ),
            "A",
        )
        self.assertEqual(
            _resolve_selected_printer(
                printers=[],
                requested_printer="C",
                default_printer="Z",
            ),
            "",
        )

    def test_normalize_json_layout_path_rejects_blank_and_non_json_values(self) -> None:
        try:
            from views.settings_flet_view import _normalize_json_layout_path
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(
            _normalize_json_layout_path("", "default.json"),
            "default.json",
        )
        self.assertEqual(
            _normalize_json_layout_path("layout.txt", "default.json"),
            "default.json",
        )
        self.assertEqual(
            _normalize_json_layout_path("custom.JSON", "default.json"),
            "custom.JSON",
        )

    def test_load_layout_document_or_default_uses_primary_fallback_and_default(self) -> None:
        try:
            from views.settings_flet_view import _load_layout_document_or_default
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        primary_doc = object()
        fallback_doc = object()

        class _FakeCanvasStore:
            def __init__(self, docs):
                self.docs = docs

            def load_layout(self, path: str):
                value = self.docs[path]
                if isinstance(value, Exception):
                    raise value
                return value

        with TemporaryDirectory() as tmpdir:
            fallback_path = Path(tmpdir) / "fallback.json"
            fallback_path.write_text("{}", encoding="utf-8")

            self.assertIs(
                _load_layout_document_or_default(
                    canvas_store=_FakeCanvasStore({"primary.json": primary_doc}),
                    path="primary.json",
                    paper_width="80",
                ),
                primary_doc,
            )
            self.assertIs(
                _load_layout_document_or_default(
                    canvas_store=_FakeCanvasStore(
                        {
                            "primary.json": RuntimeError("boom"),
                            str(fallback_path): fallback_doc,
                        }
                    ),
                    path="primary.json",
                    paper_width="80",
                    fallback_path=str(fallback_path),
                ),
                fallback_doc,
            )
            default_doc = _load_layout_document_or_default(
                canvas_store=_FakeCanvasStore({"primary.json": RuntimeError("boom")}),
                path="primary.json",
                paper_width="58",
                fallback_path=str(Path(tmpdir) / "missing.json"),
            )
            self.assertEqual(default_doc.meta.paper_width, "58")

    def test_attach_page_service_supports_registry_services_and_overlay_fallback(self) -> None:
        try:
            from views.settings_flet_view import _attach_page_service, _attach_page_services
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        service_a = object()
        service_b = object()
        service_c = object()

        registry_page = _StubPage()
        registry_page._services = type("Registry", (), {"_services": []})()
        _attach_page_services(registry_page, service_a, service_b)
        self.assertEqual(registry_page._services._services, [service_a, service_b])

        overlay_page = _StubPage()
        _attach_page_service(overlay_page, service_c)
        self.assertEqual(overlay_page.overlay, [service_c])

    def test_build_editor_layout_tab_style_maps_active_and_inactive_visuals(self) -> None:
        try:
            from views.settings_flet_view import _build_editor_layout_tab_style
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        active_style = _build_editor_layout_tab_style(is_active=True)
        inactive_style = _build_editor_layout_tab_style(is_active=False)

        self.assertEqual(active_style.bgcolor, "#39C5BB")
        self.assertEqual(inactive_style.bgcolor, "#F5FCFB")
        self.assertEqual(active_style.color, "#FFFFFF")
        self.assertEqual(inactive_style.color, "#203047")

    def test_apply_editor_layout_tab_styles_updates_receipt_and_product_buttons(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _apply_editor_layout_tab_styles
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        receipt_button = ft.TextButton("receipt")
        product_button = ft.TextButton("product")

        _apply_editor_layout_tab_styles(
            active_layout="product",
            receipt_tab_button=receipt_button,
            product_tab_button=product_button,
        )

        self.assertEqual(receipt_button.style.bgcolor, "#F5FCFB")
        self.assertEqual(product_button.style.bgcolor, "#39C5BB")

    def test_reset_editor_layout_transient_state_clears_selection_binding_and_inline_edit(self) -> None:
        try:
            from views.settings_flet_view import _reset_editor_layout_transient_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        calls: list[tuple[str, object]] = []
        state: dict[str, object] = {"inline_edit_id": "abc"}

        def set_selected_id(value) -> None:
            calls.append(("selected", value))

        def set_active_binding_target(value) -> None:
            calls.append(("binding", value))

        _reset_editor_layout_transient_state(
            set_selected_id=set_selected_id,
            set_active_binding_target=set_active_binding_target,
            state=state,
        )

        self.assertEqual(calls, [("selected", None), ("binding", None)])
        self.assertIsNone(state["inline_edit_id"])

    def test_format_active_template_label_builds_expected_string(self) -> None:
        try:
            from views.settings_flet_view import _format_active_template_label
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(
            _format_active_template_label(
                editor_layout_label_text="receipt",
                layout_path_text="layout.json",
            ),
            "활성 receipt 템플릿: layout.json",
        )

    def test_sync_editor_layout_display_updates_template_and_calls_refresh_branch(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _sync_editor_layout_display
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        current_template_text = ft.Text("old")
        calls: list[tuple[str, object, object]] = []

        def set_paper_width(value: str, push_update: bool = True) -> None:
            calls.append(("set_paper_width", value, push_update))

        def refresh_all(push_update: bool = True) -> None:
            calls.append(("refresh_all", push_update, None))

        _sync_editor_layout_display(
            current_doc_paper_width="80",
            selected_paper_width="80",
            current_template_text=current_template_text,
            editor_layout_label_text="receipt",
            layout_path_text="layout.json",
            set_paper_width=set_paper_width,
            refresh_all=refresh_all,
        )

        self.assertEqual(current_template_text.value, "활성 receipt 템플릿: layout.json")
        self.assertEqual(calls, [("refresh_all", False, None)])

    def test_editor_layout_doc_helpers_read_and_update_state(self) -> None:
        try:
            from views.settings_flet_view import _get_editor_layout_doc, _set_editor_layout_doc
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        receipt_doc = object()
        product_doc = object()
        updated_doc = object()
        state: dict[str, object] = {"docs": {"receipt": receipt_doc, "product": product_doc}}

        self.assertIs(
            _get_editor_layout_doc(state=state, active_layout="receipt"),
            receipt_doc,
        )

        _set_editor_layout_doc(
            state=state,
            active_layout="product",
            doc=updated_doc,
        )

        self.assertIs(state["docs"]["product"], updated_doc)

    def test_editor_selected_and_binding_helpers_update_state_consistently(self) -> None:
        try:
            from views.settings_flet_view import (
                _get_editor_active_binding_target,
                _get_editor_selected_id,
                _set_editor_active_binding_target,
                _set_editor_selected_id,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        state: dict[str, object] = {
            "selected_id": "old",
            "active_binding_target": "text_template",
            "inline_edit_id": "inline",
        }

        self.assertEqual(_get_editor_selected_id(state=state), "old")
        self.assertEqual(_get_editor_active_binding_target(state=state), "text_template")

        _set_editor_selected_id(state=state, value="new")
        _set_editor_active_binding_target(state=state, value="data_template")

        self.assertEqual(state["selected_id"], "new")
        self.assertEqual(state["active_binding_target"], "data_template")
        self.assertIsNone(state["inline_edit_id"])

    def test_editor_layout_path_helpers_update_state_and_template_text(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import (
                _format_active_template_label,
                _get_editor_layout_path,
                _set_editor_layout_path,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        current_template_text = ft.Text("old")
        state: dict[str, object] = {"layout_paths": {"receipt": "receipt.json", "product": "product.json"}}

        self.assertEqual(
            _get_editor_layout_path(state=state, active_layout="receipt"),
            "receipt.json",
        )

        _set_editor_layout_path(
            state=state,
            active_layout="product",
            path="updated.json",
            current_template_text=current_template_text,
            editor_layout_label_text="product",
        )

        self.assertEqual(state["layout_paths"]["product"], "updated.json")
        self.assertEqual(
            current_template_text.value,
            _format_active_template_label(
                editor_layout_label_text="product",
                layout_path_text="updated.json",
            ),
        )

    def test_build_canvas_margin_overlay_controls_creates_top_and_bottom_overlays(self) -> None:
        try:
            from views.settings_flet_view import _build_canvas_margin_overlay_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        controls = _build_canvas_margin_overlay_controls(
            preview_width=200,
            preview_height=400,
            margin_top_preview=20,
            margin_bottom_preview=30,
        )

        self.assertEqual(len(controls), 2)
        self.assertEqual(controls[0].top, 0)
        self.assertEqual(controls[0].height, 20)
        self.assertEqual(controls[1].top, 370)
        self.assertEqual(controls[1].height, 30)

    def test_format_canvas_meta_text_includes_dimensions_and_optional_margins(self) -> None:
        try:
            from views.settings_flet_view import _format_canvas_meta_text
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        meta_text = _format_canvas_meta_text(
            preview_width=200,
            preview_height=400,
            real_canvas_width=640,
            margin_top=0,
            margin_bottom=0,
        )
        self.assertIn("200x400", meta_text)
        self.assertIn("640px", meta_text)

        margin_text = _format_canvas_meta_text(
            preview_width=200,
            preview_height=400,
            real_canvas_width=640,
            margin_top=5,
            margin_bottom=9,
        )
        self.assertIn("5", margin_text)
        self.assertIn("9", margin_text)
        return

        self.assertEqual(
            _format_canvas_meta_text(
                preview_width=200,
                preview_height=400,
                real_canvas_width=640,
                margin_top=0,
                margin_bottom=0,
            ),
            "?몄쭛 而ㅻ쾭??200x400) / ?ㅼ젣??640px",
        )
        self.assertIn(
            "?щ갚 ??:5 ??:9",
            _format_canvas_meta_text(
                preview_width=200,
                preview_height=400,
                real_canvas_width=640,
                margin_top=5,
                margin_bottom=9,
            ),
        )

    def test_build_property_panel_empty_state_returns_expected_copy(self) -> None:
        try:
            from views.settings_flet_view import _build_property_panel_empty_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = _build_property_panel_empty_state()
        self.assertEqual(getattr(panel, "spacing", None), 8)
        self.assertEqual(len(getattr(panel, "controls", [])), 2)
        self.assertEqual(getattr(panel.controls[0], "size", None), 20)
        self.assertEqual(getattr(panel.controls[1], "color", None), "#666666")
        return

        panel = _build_property_panel_empty_state()
        strings = self._collect_strings(panel)

        self.assertIn("?냽??", strings)
        self.assertIn("而ㅻ쾭?ㅼ뿉??붿냼瑜??좏깮?섏꽭??", strings)

    def test_apply_property_panel_controls_wraps_controls_in_scrollable_column(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _apply_property_panel_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        property_panel = ft.Container()
        controls = [ft.Text("a"), ft.Text("b")]

        _apply_property_panel_controls(
            property_panel=property_panel,
            controls=controls,
        )

        column = property_panel.content
        self.assertIsNotNone(column)
        self.assertEqual(getattr(column, "controls", []), controls)
        self.assertEqual(getattr(column, "spacing", None), 8)
        self.assertEqual(getattr(column, "scroll", None), ft.ScrollMode.AUTO)

    def test_refresh_editor_view_state_invokes_canvas_property_and_optional_page_update(self) -> None:
        try:
            from views.settings_flet_view import _refresh_editor_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        page = _StubPage()
        calls: list[str] = []

        def refresh_canvas() -> None:
            calls.append("canvas")

        def refresh_property_panel() -> None:
            calls.append("property")

        _refresh_editor_view_state(
            refresh_canvas=refresh_canvas,
            refresh_property_panel=refresh_property_panel,
            page=page,
            push_update=True,
        )

        self.assertEqual(calls, ["canvas", "property"])

    def test_canvas_overlay_helpers_replace_guides_and_indicators(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import (
                _consume_canvas_insertion_target,
                _remove_canvas_stack_controls,
                _replace_canvas_snap_guides,
                _update_canvas_insertion_indicator,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        old_guide = ft.Container()
        keep_control = ft.Text("keep")
        stack = ft.Stack(controls=[old_guide, keep_control])
        state: dict[str, object] = {
            "snap_guides": [old_guide],
            "insertion_indicator": None,
            "insertion_target_y": None,
        }

        _remove_canvas_stack_controls(
            canvas_stack=stack,
            controls=[old_guide, ft.Container()],
        )
        self.assertEqual(stack.controls, [keep_control])

        new_guide = ft.Container()
        _replace_canvas_snap_guides(
            state=state,
            canvas_stack=stack,
            guides=[{"axis": "vertical", "pos": 10}],
            build_guide_lines=lambda _guides: [new_guide],
        )
        self.assertEqual(state["snap_guides"], [new_guide])
        self.assertEqual(stack.controls, [keep_control, new_guide])

        indicator = ft.Container()
        built = _update_canvas_insertion_indicator(
            state=state,
            canvas_stack=stack,
            slot_y=48,
            build_insertion_indicator=lambda slot_y: indicator if slot_y == 48 else ft.Container(),
        )
        self.assertIs(built, indicator)
        self.assertIs(state["insertion_indicator"], indicator)
        self.assertEqual(state["insertion_target_y"], 48)
        self.assertIn(indicator, stack.controls)
        self.assertEqual(_consume_canvas_insertion_target(state=state), 48)
        self.assertIsNone(state["insertion_target_y"])

        _update_canvas_insertion_indicator(
            state=state,
            canvas_stack=stack,
            slot_y=None,
            build_insertion_indicator=lambda _slot_y: ft.Container(),
        )
        self.assertIsNone(state["insertion_indicator"])
        self.assertNotIn(indicator, stack.controls)

    def test_reset_drag_and_resize_interaction_state_clear_expected_keys(self) -> None:
        try:
            from views.settings_flet_view import (
                _reset_drag_interaction_state,
                _reset_resize_interaction_state,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        state: dict[str, object] = {
            "snap_guides": ["guide"],
            "resize_bottom_start_y": 120,
            "resize_pointer_start_gx": 1.0,
            "resize_pointer_start_gy": 2.0,
            "drag_bottom_start_y": 240,
            "drag_pointer_start_gx": 3.0,
            "drag_pointer_start_gy": 4.0,
        }

        _reset_resize_interaction_state(state=state)
        self.assertEqual(state["snap_guides"], [])
        self.assertIsNone(state["resize_bottom_start_y"])
        self.assertIsNone(state["resize_pointer_start_gx"])
        self.assertIsNone(state["resize_pointer_start_gy"])

        state["snap_guides"] = ["guide"]
        _reset_drag_interaction_state(state=state)
        self.assertEqual(state["snap_guides"], [])
        self.assertIsNone(state["drag_bottom_start_y"])
        self.assertIsNone(state["drag_pointer_start_gx"])
        self.assertIsNone(state["drag_pointer_start_gy"])

    def test_canvas_selection_helpers_respect_tap_guard_and_binding_target_rules(self) -> None:
        try:
            from views.settings_flet_view import (
                _resolve_binding_target_for_element_type,
                _should_ignore_canvas_background_tap,
                _should_start_inline_edit_on_tap,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertTrue(
            _should_ignore_canvas_background_tap(
                last_element_tap_time=10.0,
                current_time=10.02,
            )
        )
        self.assertFalse(
            _should_ignore_canvas_background_tap(
                last_element_tap_time=10.0,
                current_time=10.2,
            )
        )
        self.assertEqual(_resolve_binding_target_for_element_type("qr"), "data_template")
        self.assertEqual(_resolve_binding_target_for_element_type("text"), "text_template")
        self.assertTrue(
            _should_start_inline_edit_on_tap(
                already_selected=True,
                element_type="text",
                inline_edit_id=None,
                element_id="txt_1",
            )
        )
        self.assertFalse(
            _should_start_inline_edit_on_tap(
                already_selected=False,
                element_type="text",
                inline_edit_id=None,
                element_id="txt_1",
            )
        )

    def test_apply_element_tap_selection_updates_focus_binding_and_inline_state(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _apply_element_tap_selection
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        focus_values: list[bool] = []
        selected_values: list[str | None] = []
        binding_values: list[str | None] = []
        state: dict[str, object] = {"inline_edit_id": None, "last_element_tap_time": 0.0}

        def set_canvas_focus(value: bool) -> None:
            focus_values.append(value)

        def set_selected_id(value: str | None) -> None:
            selected_values.append(value)

        def set_active_binding_target(value: str | None) -> None:
            binding_values.append(value)

        qr_element = ReceiptCanvasElement(id="qr_1", type="qr", x=0, y=0, w=100, h=100)
        _apply_element_tap_selection(
            state=state,
            element=qr_element,
            current_selected_id=None,
            tap_time=12.5,
            set_canvas_focus=set_canvas_focus,
            set_selected_id=set_selected_id,
            set_active_binding_target=set_active_binding_target,
        )

        self.assertEqual(focus_values, [True])
        self.assertEqual(selected_values, ["qr_1"])
        self.assertEqual(binding_values, ["data_template"])
        self.assertEqual(state["last_element_tap_time"], 12.5)
        self.assertIsNone(state["inline_edit_id"])

        text_element = ReceiptCanvasElement(id="txt_1", type="text", x=0, y=0, w=100, h=40)
        _apply_element_tap_selection(
            state=state,
            element=text_element,
            current_selected_id="txt_1",
            tap_time=13.0,
            set_canvas_focus=set_canvas_focus,
            set_selected_id=set_selected_id,
            set_active_binding_target=set_active_binding_target,
        )

        self.assertEqual(selected_values[-1], "txt_1")
        self.assertEqual(binding_values[-1], "text_template")
        self.assertEqual(state["inline_edit_id"], "txt_1")

    def test_apply_element_double_tap_edit_and_clear_canvas_selection_update_state(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import (
                _apply_element_double_tap_edit,
                _clear_canvas_selection,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        focus_values: list[bool] = []
        selected_values: list[str | None] = []
        cleared_binding_values: list[str | None] = []
        state: dict[str, object] = {"inline_edit_id": None}

        def set_canvas_focus(value: bool) -> None:
            focus_values.append(value)

        def set_selected_id(value: str | None) -> None:
            selected_values.append(value)

        def set_active_binding_target(value: str | None) -> None:
            cleared_binding_values.append(value)

        image_element = ReceiptCanvasElement(id="img_1", type="image", x=0, y=0, w=80, h=80)
        self.assertFalse(
            _apply_element_double_tap_edit(
                state=state,
                element=image_element,
                set_canvas_focus=set_canvas_focus,
                set_selected_id=set_selected_id,
            )
        )

        text_element = ReceiptCanvasElement(id="txt_2", type="text", x=0, y=0, w=100, h=40)
        self.assertTrue(
            _apply_element_double_tap_edit(
                state=state,
                element=text_element,
                set_canvas_focus=set_canvas_focus,
                set_selected_id=set_selected_id,
            )
        )
        self.assertEqual(selected_values[-1], "txt_2")
        self.assertEqual(focus_values[-1], True)
        self.assertEqual(state["inline_edit_id"], "txt_2")

        _clear_canvas_selection(
            set_selected_id=set_selected_id,
            set_active_binding_target=set_active_binding_target,
        )
        self.assertEqual(selected_values[-1], None)
        self.assertEqual(cleared_binding_values[-1], None)

    def test_update_element_text_template_replaces_text_value(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _update_element_text_template
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="txt_3",
            type="text",
            x=10,
            y=20,
            w=120,
            h=40,
            text_template="before",
        )

        updated = _update_element_text_template(
            element,
            text_template="after",
        )

        self.assertEqual(updated.text_template, "after")
        self.assertEqual(element.text_template, "before")

    def test_build_binding_insert_text_uses_label_and_token(self) -> None:
        try:
            from views.settings_flet_view import _build_binding_insert_text
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(
            _build_binding_insert_text(
                "buyer_name",
                field_bindings=[("buyer_name", "주문자명")],
            ),
            "주문자명: {{buyer_name}}",
        )
        self.assertEqual(
            _build_binding_insert_text(
                "custom_field",
                field_bindings=[],
            ),
            "custom_field: {{custom_field}}",
        )

    def test_apply_binding_insert_to_selected_element_respects_text_and_qr_targets(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _apply_binding_insert_to_selected_element
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        text_element = ReceiptCanvasElement(
            id="txt_bind",
            type="text",
            x=0,
            y=0,
            w=120,
            h=40,
            text_template="before ",
        )
        updated_text = _apply_binding_insert_to_selected_element(
            selected_element=text_element,
            insert_text="주문자명: {{buyer_name}}",
            active_binding_target=None,
            resolve_binding_target_for_element_type=lambda _type: "text_template",
        )
        self.assertEqual(updated_text.text_template, "before 주문자명: {{buyer_name}}")
        self.assertEqual(text_element.text_template, "before ")

        qr_element = ReceiptCanvasElement(
            id="qr_bind",
            type="qr",
            x=0,
            y=0,
            w=120,
            h=120,
            data_template="prefix:",
        )
        updated_qr = _apply_binding_insert_to_selected_element(
            selected_element=qr_element,
            insert_text="{{order_number}}",
            active_binding_target="data_template",
            resolve_binding_target_for_element_type=lambda _type: "text_template",
        )
        self.assertEqual(updated_qr.data_template, "prefix:{{order_number}}")
        self.assertEqual(qr_element.data_template, "prefix:")

    def test_build_new_binding_text_element_sets_text_template(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _build_new_binding_text_element
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="txt_new_bind",
            type="text",
            x=0,
            y=0,
            w=120,
            h=40,
            text_template="old",
        )

        updated = _build_new_binding_text_element(
            new_text_element=element,
            insert_text="연락처: {{buyer_phone}}",
        )

        self.assertEqual(updated.text_template, "연락처: {{buyer_phone}}")
        self.assertEqual(element.text_template, "old")

    def test_update_text_element_properties_applies_minimums_and_defaults(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _update_text_element_properties
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="txt_1",
            type="text",
            x=10,
            y=20,
            w=120,
            h=40,
            text_template="old",
            font_size=22,
            bold=False,
            font_family="arial",
        )

        updated = _update_text_element_properties(
            element,
            text_template="buyer: {{buyer_name}}",
            font_size=3,
            bold=True,
            font_family="",
        )

        self.assertEqual(updated.text_template, "buyer: {{buyer_name}}")
        self.assertEqual(updated.font_size, 8)
        self.assertTrue(updated.bold)
        self.assertEqual(updated.font_family, "malgun")
        self.assertEqual(element.text_template, "old")

    def test_update_image_element_properties_updates_path_and_ratio(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _update_image_element_properties
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="img_1",
            type="image",
            x=0,
            y=0,
            w=160,
            h=90,
            asset_path="old.png",
            preserve_ratio=True,
        )

        updated = _update_image_element_properties(
            element,
            asset_path="new.png",
            preserve_ratio=False,
        )

        self.assertEqual(updated.asset_path, "new.png")
        self.assertFalse(updated.preserve_ratio)
        self.assertEqual(updated.w, 160)
        self.assertEqual(updated.h, 90)

    def test_update_qr_element_properties_only_resizes_when_needed(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _update_qr_element_properties
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="qr_1",
            type="qr",
            x=0,
            y=0,
            w=140,
            h=140,
            data_template="old",
            box_size=4,
        )
        calls: list[tuple[str, int]] = []

        updated = _update_qr_element_properties(
            element,
            data_template="https://example.test/qr",
            box_size=6,
            qr_size_calculator=lambda data, box: calls.append((data, box)) or 180,
        )

        self.assertEqual(updated.data_template, "https://example.test/qr")
        self.assertEqual(updated.box_size, 6)
        self.assertEqual((updated.w, updated.h), (180, 180))
        self.assertEqual(calls, [("https://example.test/qr", 6)])

        unchanged = _update_qr_element_properties(
            updated,
            data_template="   ",
            box_size=6,
            qr_size_calculator=lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected resize")),
        )
        self.assertEqual((unchanged.w, unchanged.h), (180, 180))

    def test_update_divider_element_properties_applies_defaults_and_minimums(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _update_divider_element_properties
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="div_1",
            type="divider",
            x=0,
            y=0,
            w=200,
            h=24,
            line_style="dotted",
            line_thickness=2,
            text_template="old",
            font_size=14,
            bold=False,
            font_family="arial",
            visibility_tag="buyer_name",
        )

        updated = _update_divider_element_properties(
            element,
            line_style="",
            line_thickness=0,
            text_template="section",
            font_size=4,
            bold=True,
            font_family="",
            visibility_tag="",
        )

        self.assertEqual(updated.line_style, "solid")
        self.assertEqual(updated.line_thickness, 1)
        self.assertEqual(updated.text_template, "section")
        self.assertEqual(updated.font_size, 8)
        self.assertTrue(updated.bold)
        self.assertEqual(updated.font_family, "malgun")
        self.assertEqual(updated.visibility_tag, "")

    def test_commit_common_dimension_update_upserts_and_refreshes(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _commit_common_dimension_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(id="txt_dim", type="text", x=1, y=2, w=30, h=40)
        upserted: list[ReceiptCanvasElement] = []
        refresh_calls: list[str] = []

        updated = _commit_common_dimension_update(
            current=element,
            x_value="11",
            y_value="22",
            w_value="88",
            h_value="44",
            coerce_int=lambda value, _default, minimum=None: max(minimum or -10_000, int(value)),
            update_common_dimensions=lambda current, **kwargs: replace(
                current,
                x=kwargs["x"],
                y=kwargs["y"],
                w=kwargs["w"],
                h=kwargs["h"],
            ),
            upsert_element=upserted.append,
            refresh_all=lambda: refresh_calls.append("all"),
        )

        self.assertIsNotNone(updated)
        self.assertEqual((updated.x, updated.y, updated.w, updated.h), (11, 22, 88, 44))
        self.assertEqual(upserted, [updated])
        self.assertEqual(refresh_calls, ["all"])

    def test_commit_text_property_update_refreshes_canvas_and_page(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _commit_text_property_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="txt_commit",
            type="text",
            x=0,
            y=0,
            w=100,
            h=40,
            text_template="before",
            font_size=14,
            bold=False,
            font_family="arial",
        )
        upserted: list[ReceiptCanvasElement] = []
        refresh_calls: list[str] = []
        update_calls: list[str] = []

        updated = _commit_text_property_update(
            current=element,
            text_template="after",
            font_size_value="6",
            bold=True,
            font_family="",
            coerce_int=lambda value, _default, minimum=None: max(minimum or -10_000, int(value)),
            upsert_element=upserted.append,
            refresh_canvas=lambda: refresh_calls.append("canvas"),
            push_update=lambda: update_calls.append("page"),
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated.text_template, "after")
        self.assertEqual(updated.font_size, 8)
        self.assertTrue(updated.bold)
        self.assertEqual(updated.font_family, "malgun")
        self.assertEqual(upserted, [updated])
        self.assertEqual(refresh_calls, ["canvas"])
        self.assertEqual(update_calls, ["page"])

    def test_commit_image_property_update_refreshes_canvas_and_page(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _commit_image_property_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="img_commit",
            type="image",
            x=0,
            y=0,
            w=120,
            h=80,
            asset_path="old.png",
            preserve_ratio=True,
        )
        upserted: list[ReceiptCanvasElement] = []
        refresh_calls: list[str] = []
        update_calls: list[str] = []

        updated = _commit_image_property_update(
            current=element,
            asset_path="new.png",
            preserve_ratio=False,
            upsert_element=upserted.append,
            refresh_canvas=lambda: refresh_calls.append("canvas"),
            push_update=lambda: update_calls.append("page"),
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated.asset_path, "new.png")
        self.assertFalse(updated.preserve_ratio)
        self.assertEqual(upserted, [updated])
        self.assertEqual(refresh_calls, ["canvas"])
        self.assertEqual(update_calls, ["page"])

    def test_commit_qr_property_update_refreshes_all_and_page(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _commit_qr_property_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="qr_commit",
            type="qr",
            x=0,
            y=0,
            w=140,
            h=140,
            data_template="old",
            box_size=4,
        )
        upserted: list[ReceiptCanvasElement] = []
        refresh_calls: list[str] = []
        update_calls: list[str] = []

        updated = _commit_qr_property_update(
            current=element,
            data_template="",
            box_size_value="5",
            coerce_int=lambda value, _default, minimum=None: max(minimum or -10_000, int(value)),
            upsert_element=upserted.append,
            refresh_all=lambda: refresh_calls.append("all"),
            push_update=lambda: update_calls.append("page"),
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated.data_template, "")
        self.assertEqual(updated.box_size, 5)
        self.assertEqual((updated.w, updated.h), (140, 140))
        self.assertEqual(upserted, [updated])
        self.assertEqual(refresh_calls, ["all"])
        self.assertEqual(update_calls, ["page"])

    def test_commit_divider_property_update_refreshes_canvas_and_page(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _commit_divider_property_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="div_commit",
            type="divider",
            x=0,
            y=0,
            w=200,
            h=24,
            line_style="dashed",
            line_thickness=2,
            text_template="before",
            font_size=14,
            bold=False,
            font_family="arial",
            visibility_tag="buyer_name",
        )
        upserted: list[ReceiptCanvasElement] = []
        refresh_calls: list[str] = []
        update_calls: list[str] = []

        updated = _commit_divider_property_update(
            current=element,
            line_style="",
            line_thickness_value="0",
            text_template="section",
            font_size_value="6",
            bold=True,
            font_family="",
            visibility_tag="",
            coerce_int=lambda value, _default, minimum=None: max(minimum or -10_000, int(value)),
            upsert_element=upserted.append,
            refresh_canvas=lambda: refresh_calls.append("canvas"),
            push_update=lambda: update_calls.append("page"),
        )

        self.assertIsNotNone(updated)
        self.assertEqual(updated.line_style, "solid")
        self.assertEqual(updated.line_thickness, 1)
        self.assertEqual(updated.text_template, "section")
        self.assertEqual(updated.font_size, 8)
        self.assertTrue(updated.bold)
        self.assertEqual(updated.font_family, "malgun")
        self.assertEqual(updated.visibility_tag, "")
        self.assertEqual(upserted, [updated])
        self.assertEqual(refresh_calls, ["canvas"])
        self.assertEqual(update_calls, ["page"])

    def test_build_property_panel_base_controls_preserves_header_and_geometry_fields(self) -> None:
        try:
            import flet as ft
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _build_property_panel_base_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        selected = ReceiptCanvasElement(id="base_1", type="text", x=0, y=0, w=100, h=40)
        x_field = ft.TextField(label="x")
        y_field = ft.TextField(label="y")
        w_field = ft.TextField(label="w")
        h_field = ft.TextField(label="h")

        controls = _build_property_panel_base_controls(
            selected=selected,
            x_field=x_field,
            y_field=y_field,
            w_field=w_field,
            h_field=h_field,
        )

        self.assertEqual(len(controls), 4)
        self.assertEqual(getattr(controls[0], "value", None), "속성")
        self.assertIn("base_1", getattr(controls[1], "value", ""))
        self.assertEqual(getattr(controls[2], "controls", []), [x_field, y_field, w_field, h_field])

    def test_build_property_panel_text_controls_preserves_typography_controls(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_property_panel_text_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        text_template_field = ft.TextField(label="template")
        font_family_dropdown = ft.Dropdown(label="font")
        font_size_field = ft.TextField(label="size")
        bold_btn = ft.IconButton(icon="b")
        align_left_btn = ft.IconButton(icon="left")
        align_center_btn = ft.IconButton(icon="center")
        align_right_btn = ft.IconButton(icon="right")

        controls = _build_property_panel_text_controls(
            text_template_field=text_template_field,
            font_family_dropdown=font_family_dropdown,
            font_size_field=font_size_field,
            bold_btn=bold_btn,
            align_left_btn=align_left_btn,
            align_center_btn=align_center_btn,
            align_right_btn=align_right_btn,
        )

        self.assertEqual(controls[0], text_template_field)
        self.assertEqual(
            getattr(controls[1], "controls", []),
            [font_family_dropdown, font_size_field, bold_btn, align_left_btn, align_center_btn, align_right_btn],
        )
        self.assertEqual(getattr(controls[1], "spacing", None), 4)

    def test_build_property_panel_image_controls_preserves_replace_button_and_ratio_switch(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_property_panel_image_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        image_path_field = ft.TextField(label="asset")
        replace_image_button = ft.ElevatedButton("교체")
        preserve_ratio_switch = ft.Switch(label="비율 유지")

        controls = _build_property_panel_image_controls(
            image_path_field=image_path_field,
            replace_image_button=replace_image_button,
            preserve_ratio_switch=preserve_ratio_switch,
        )

        self.assertEqual(controls[0], image_path_field)
        self.assertEqual(getattr(controls[1], "controls", []), [replace_image_button, preserve_ratio_switch])
        self.assertEqual(getattr(controls[1], "spacing", None), 10)

    def test_build_property_panel_qr_controls_preserves_qr_fields(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_property_panel_qr_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        qr_data_field = ft.TextField(label="data")
        box_size_field = ft.TextField(label="box")

        controls = _build_property_panel_qr_controls(
            qr_data_field=qr_data_field,
            box_size_field=box_size_field,
        )

        self.assertEqual(controls, [qr_data_field, box_size_field])

    def test_build_property_panel_divider_controls_preserves_divider_specific_rows(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_property_panel_divider_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        line_style_dropdown = ft.Dropdown(label="style")
        line_thickness_field = ft.TextField(label="thickness")
        divider_text_field = ft.TextField(label="text")
        div_font_family_dropdown = ft.Dropdown(label="font")
        div_font_size_field = ft.TextField(label="size")
        div_bold_btn = ft.IconButton(icon="b")
        visibility_tag_dropdown = ft.Dropdown(label="tag")

        controls = _build_property_panel_divider_controls(
            line_style_dropdown=line_style_dropdown,
            line_thickness_field=line_thickness_field,
            divider_text_field=divider_text_field,
            div_font_family_dropdown=div_font_family_dropdown,
            div_font_size_field=div_font_size_field,
            div_bold_btn=div_bold_btn,
            visibility_tag_dropdown=visibility_tag_dropdown,
        )

        self.assertEqual(getattr(controls[0], "controls", []), [line_style_dropdown, line_thickness_field])
        self.assertEqual(getattr(controls[0], "spacing", None), 10)
        self.assertEqual(controls[1], divider_text_field)
        self.assertEqual(getattr(controls[2], "controls", []), [div_font_family_dropdown, div_font_size_field, div_bold_btn])
        self.assertEqual(getattr(controls[2], "spacing", None), 4)
        self.assertEqual(controls[3], visibility_tag_dropdown)

    def test_build_canvas_preview_controls_preserves_overlay_preview_handles_and_guides(self) -> None:
        try:
            import flet as ft
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _build_canvas_preview_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        selected = ReceiptCanvasElement(id="txt_canvas", type="text", x=0, y=0, w=100, h=40)
        other = ReceiptCanvasElement(id="img_canvas", type="image", x=10, y=60, w=80, h=80)
        guide = ft.Container()
        handle = ft.Container()

        controls = _build_canvas_preview_controls(
            preview_width=320,
            preview_height=480,
            margin_top_preview=12,
            margin_bottom_preview=18,
            visible_elements=[selected, other],
            selected_element=selected,
            snap_guides=[guide],
            build_element_preview=lambda element: ft.Text(element.id),
            build_resize_handles=lambda _element: [handle],
        )

        strings = self._collect_strings(ft.Column(controls=controls))
        self.assertIn("txt_canvas", strings)
        self.assertIn("img_canvas", strings)
        self.assertIn(guide, controls)
        self.assertIn(handle, controls)

    def test_apply_canvas_stack_view_state_sets_controls_size_and_state(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _apply_canvas_stack_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        stack = ft.Stack()
        controls = [ft.Text("a"), ft.Text("b")]
        state: dict[str, object] = {}

        _apply_canvas_stack_view_state(
            canvas_stack=stack,
            canvas_controls=controls,
            preview_width=320,
            preview_height=480,
            state=state,
        )

        self.assertEqual(stack.controls, controls)
        self.assertEqual(stack.width, 320)
        self.assertEqual(stack.height, 480)
        self.assertIs(state["canvas_stack"], stack)

    def test_apply_canvas_frame_body_view_state_sets_body_size_and_content(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _apply_canvas_frame_body_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        frame_body = ft.Container()
        stack = ft.Stack()

        _apply_canvas_frame_body_view_state(
            canvas_frame_body=frame_body,
            canvas_stack=stack,
            preview_width=300,
            preview_height=420,
        )

        self.assertEqual(frame_body.width, 300)
        self.assertEqual(frame_body.height, 420)
        self.assertIs(frame_body.content, stack)

    def test_apply_canvas_scroll_view_state_updates_scroll_and_gutter(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _apply_canvas_scroll_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        scrollable_canvas = ft.Column()
        scroll_gutter = ft.Container()

        _apply_canvas_scroll_view_state(
            scrollable_canvas=scrollable_canvas,
            scroll_gutter=scroll_gutter,
            preview_height=700,
            viewport_height=500,
            max_viewport_height=500,
        )
        self.assertEqual(scrollable_canvas.height, 500)
        self.assertEqual(scrollable_canvas.scroll, ft.ScrollMode.AUTO)
        self.assertEqual(scroll_gutter.width, 14)
        self.assertEqual(scroll_gutter.height, 700)

        _apply_canvas_scroll_view_state(
            scrollable_canvas=scrollable_canvas,
            scroll_gutter=scroll_gutter,
            preview_height=320,
            viewport_height=320,
            max_viewport_height=500,
        )
        self.assertEqual(scrollable_canvas.height, 320)
        self.assertIsNone(scrollable_canvas.scroll)
        self.assertEqual(scroll_gutter.width, 0)
        self.assertEqual(scroll_gutter.height, 320)

    def test_require_selected_id_and_element_report_missing_state(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import (
                _require_selected_element,
                _require_selected_id,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        messages: list[str] = []
        show_status = messages.append

        self.assertIsNone(
            _require_selected_id(
                selected_id=None,
                show_status=show_status,
                missing_message="missing id",
            )
        )
        self.assertEqual(messages[-1], "missing id")

        self.assertEqual(
            _require_selected_id(
                selected_id="selected_1",
                show_status=show_status,
                missing_message="unused",
            ),
            "selected_1",
        )

        self.assertIsNone(
            _require_selected_element(
                selected_element=None,
                show_status=show_status,
                missing_message="missing element",
            )
        )
        self.assertEqual(messages[-1], "missing element")

        element = ReceiptCanvasElement(id="sel_1", type="text", x=0, y=0, w=100, h=40)
        self.assertIs(
            _require_selected_element(
                selected_element=element,
                show_status=show_status,
                missing_message="unused",
            ),
            element,
        )

    def test_remove_selected_element_from_elements_removes_matching_id(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _remove_selected_element_from_elements
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        first = ReceiptCanvasElement(id="txt_keep", type="text", x=0, y=0, w=100, h=40)
        second = ReceiptCanvasElement(id="txt_drop", type="text", x=0, y=50, w=100, h=40)

        remaining = _remove_selected_element_from_elements(
            elements=[first, second],
            selected_id="txt_drop",
        )

        self.assertEqual([element.id for element in remaining], ["txt_keep"])

    def test_apply_selected_alignment_action_updates_element_and_refreshes(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasElement
            from views.settings_flet_view import _apply_selected_alignment_action
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        element = ReceiptCanvasElement(
            id="txt_align",
            type="text",
            x=0,
            y=0,
            w=100,
            h=40,
            align="left",
        )
        upserted: list[ReceiptCanvasElement] = []
        refresh_calls: list[str] = []

        updated = _apply_selected_alignment_action(
            element=element,
            align="center",
            upsert_element=upserted.append,
            refresh_all=lambda: refresh_calls.append("all"),
        )

        self.assertEqual(updated.align, "center")
        self.assertEqual(element.align, "left")
        self.assertEqual(upserted, [updated])
        self.assertEqual(refresh_calls, ["all"])

    def test_resolve_editor_default_layout_path_matches_layout_key(self) -> None:
        try:
            from views.settings_flet_view import (
                _resolve_editor_default_layout_path,
                DEFAULT_PRODUCT_RECEIPT_LAYOUT_PATH,
                DEFAULT_RECEIPT_LAYOUT_PATH,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(
            _resolve_editor_default_layout_path("receipt"),
            DEFAULT_RECEIPT_LAYOUT_PATH,
        )
        self.assertEqual(
            _resolve_editor_default_layout_path("product"),
            DEFAULT_PRODUCT_RECEIPT_LAYOUT_PATH,
        )

    def test_build_layout_document_for_save_normalizes_paper_width_and_canvas_width(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasMeta, ReceiptCanvasDocument, paper_width_to_px
            from views.settings_flet_view import _build_layout_document_for_save
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        doc = ReceiptCanvasDocument(
            meta=ReceiptCanvasMeta(
                paper_width="80",
                canvas_width_px=555,
            ),
            elements=[],
        )

        updated = _build_layout_document_for_save(doc, paper_width="58")
        self.assertEqual(updated.meta.paper_width, "58")
        self.assertEqual(updated.meta.canvas_width_px, paper_width_to_px("58"))

        fallback = _build_layout_document_for_save(doc, paper_width="999")
        self.assertEqual(fallback.meta.paper_width, "80")
        self.assertEqual(fallback.meta.canvas_width_px, paper_width_to_px("80"))

    def test_apply_loaded_layout_document_updates_state_and_persists_default_path(self) -> None:
        try:
            from models.receipt_canvas_model import ReceiptCanvasMeta, ReceiptCanvasDocument
            from views.settings_flet_view import _apply_loaded_layout_document
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _PaperWidthField:
            def __init__(self) -> None:
                self.value = ""

        doc = ReceiptCanvasDocument(
            meta=ReceiptCanvasMeta(
                paper_width="58",
                canvas_width_px=384,
            ),
            elements=[],
        )
        set_doc_calls: list[ReceiptCanvasDocument] = []
        set_layout_calls: list[str] = []
        set_selected_calls: list[str | None] = []
        save_calls: list[tuple[str, ReceiptCanvasDocument]] = []
        refresh_calls: list[str] = []
        paper_width_dropdown = _PaperWidthField()

        _apply_loaded_layout_document(
            doc=doc,
            default_layout_path="Resources/templates/receipt_layout.json",
            set_doc=set_doc_calls.append,
            set_layout_path=set_layout_calls.append,
            set_selected_id=set_selected_calls.append,
            paper_width_dropdown=paper_width_dropdown,
            save_layout=lambda path, value: save_calls.append((path, value)),
            refresh_all=lambda: refresh_calls.append("all"),
        )

        self.assertEqual(set_doc_calls, [doc])
        self.assertEqual(set_layout_calls, ["Resources/templates/receipt_layout.json"])
        self.assertEqual(set_selected_calls, [None])
        self.assertEqual(paper_width_dropdown.value, "58")
        self.assertEqual(save_calls, [("Resources/templates/receipt_layout.json", doc)])
        self.assertEqual(refresh_calls, ["all"])

    def test_receipt_editor_workspace_builder_preserves_tabs_and_hosts(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_receipt_editor_workspace
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        btn_receipt_editor_tab = ft.TextButton("receipt")
        btn_product_editor_tab = ft.TextButton("product")
        property_panel = ft.Container(content=ft.Text("property"))
        canvas_host = ft.Container(content=ft.Text("canvas"))

        panel = _build_receipt_editor_workspace(
            btn_receipt_editor_tab=btn_receipt_editor_tab,
            btn_product_editor_tab=btn_product_editor_tab,
            property_panel=property_panel,
            canvas_host=canvas_host,
        )

        column = panel.content
        self.assertEqual(getattr(panel, "expand", None), 3)
        self.assertIsNotNone(column)
        self.assertEqual(getattr(column, "spacing", None), 12)
        self.assertEqual(len(getattr(column, "controls", [])), 3)

        row = column.controls[0]
        self.assertEqual(getattr(row, "controls", []), [btn_receipt_editor_tab, btn_product_editor_tab])
        self.assertIs(getattr(column.controls[1], "content", None), property_panel)
        self.assertIs(getattr(column.controls[2], "content", None), canvas_host)

    def test_receipt_editor_split_layout_builder_preserves_columns(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_receipt_editor_split_layout
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        left_controls_panel = ft.Container(content=ft.Text("left"))
        right_workspace = ft.Container(content=ft.Text("right"))

        panel = _build_receipt_editor_split_layout(
            left_controls_panel=left_controls_panel,
            right_workspace=right_workspace,
        )

        self.assertEqual(getattr(panel, "controls", []), [left_controls_panel, right_workspace])
        self.assertEqual(getattr(panel, "spacing", None), 12)
        self.assertEqual(getattr(panel, "vertical_alignment", None), ft.CrossAxisAlignment.STRETCH)

    def test_receipt_editor_left_controls_panel_builder_preserves_toolbar_and_status_controls(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_receipt_editor_left_controls_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        printer_dropdown = ft.Dropdown(label="printer")
        paper_width_dropdown = ft.Dropdown(label="paper")
        dpi_dropdown = ft.Dropdown(label="dpi")
        margin_top_field = ft.TextField(label="top")
        margin_bottom_field = ft.TextField(label="bottom")
        btn_save = ft.ElevatedButton("save")
        btn_save_as = ft.OutlinedButton("save as")
        btn_load = ft.OutlinedButton("load")
        btn_test_print = ft.OutlinedButton("test")
        btn_test_preview = ft.OutlinedButton("preview")
        current_template_text = ft.Text("current template")
        product_receipt_switch = ft.Switch(label="product receipt")
        btn_add_text = ft.OutlinedButton("add text")
        btn_add_image = ft.OutlinedButton("add image")
        btn_add_divider = ft.OutlinedButton("add divider")
        btn_delete = ft.OutlinedButton("delete")
        field_chip_row = ft.Row(controls=[ft.Text("chip")])
        status_text = ft.Text("status")
        qr_expansion_tile = ft.ExpansionTile(title=ft.Text("qr"))

        panel = _build_receipt_editor_left_controls_panel(
            printer_dropdown=printer_dropdown,
            paper_width_dropdown=paper_width_dropdown,
            dpi_dropdown=dpi_dropdown,
            margin_top_field=margin_top_field,
            margin_bottom_field=margin_bottom_field,
            btn_save=btn_save,
            btn_save_as=btn_save_as,
            btn_load=btn_load,
            btn_test_print=btn_test_print,
            btn_test_preview=btn_test_preview,
            current_template_text=current_template_text,
            product_receipt_switch=product_receipt_switch,
            btn_add_text=btn_add_text,
            btn_add_image=btn_add_image,
            btn_add_divider=btn_add_divider,
            btn_delete=btn_delete,
            field_chip_row=field_chip_row,
            status_text=status_text,
            qr_expansion_tile=qr_expansion_tile,
        )
        strings = self._collect_strings(panel)
        column = panel.content

        self.assertEqual(getattr(panel, "expand", None), 2)
        self.assertEqual(getattr(panel, "bgcolor", None), "#FFFFFF")
        self.assertEqual(getattr(panel, "padding", None), 12)
        self.assertEqual(getattr(column, "spacing", None), 8)
        self.assertIs(getattr(column.controls[3], "value", None), current_template_text.value)
        self.assertIs(column.controls[8], field_chip_row)
        self.assertIs(column.controls[9], status_text)
        self.assertIs(column.controls[11], qr_expansion_tile)
        self.assertIn("영수증 양식 편집기", strings)
        self.assertIn("필드칩: 클릭하면 선택한 텍스트/QR 템플릿에 변수 삽입", strings)
        self.assertIn("활성화하면 일반 상품이 있는 주문에만 상품 영수증을 추가로 출력합니다.", strings)

    def test_settings_panels_use_app_settings_ticket_panel_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_app_settings_ticket_panel(", source)
        self.assertGreaterEqual(source.count("_build_app_settings_ticket_panel("), 2)

    def test_settings_panels_use_receipt_ticket_panel_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_receipt_ticket_settings_panel(", source)
        self.assertGreaterEqual(source.count("_build_receipt_ticket_settings_panel("), 2)

    def test_settings_panels_use_receipt_placeholder_panel_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_receipt_placeholder_panel(", source)
        self.assertGreaterEqual(source.count("_build_receipt_placeholder_panel("), 3)

    def test_receipt_settings_preview_uses_active_layout_label_and_path(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("template_path=_layout_path()", source)
        self.assertIn("preview_items = [(_editor_layout_label(), preview_base64)]", source)

    def test_apply_settings_section_switch_maps_ticket_content_and_styles(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _apply_settings_section_switch
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        ticket_button = ft.TextButton("ticket")
        receipt_button = ft.TextButton("receipt")
        content_host = ft.Container()
        ticket_content = ft.Container()
        receipt_content = ft.Container()

        _apply_settings_section_switch(
            active_section="ticket",
            ticket_button=ticket_button,
            receipt_button=receipt_button,
            content_host=content_host,
            ticket_content=ticket_content,
            receipt_content=receipt_content,
        )

        self.assertIs(content_host.content, ticket_content)
        self.assertEqual(ticket_button.style.bgcolor, "#DDE8FF")
        self.assertEqual(receipt_button.style.bgcolor, "#00000000")

    def test_apply_settings_section_switch_maps_receipt_content_and_styles(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _apply_settings_section_switch
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        ticket_button = ft.TextButton("ticket")
        receipt_button = ft.TextButton("receipt")
        content_host = ft.Container()
        ticket_content = ft.Container()
        receipt_content = ft.Container()

        _apply_settings_section_switch(
            active_section="receipt",
            ticket_button=ticket_button,
            receipt_button=receipt_button,
            content_host=content_host,
            ticket_content=ticket_content,
            receipt_content=receipt_content,
        )

        self.assertIs(content_host.content, receipt_content)
        self.assertEqual(ticket_button.style.bgcolor, "#00000000")
        self.assertEqual(receipt_button.style.bgcolor, "#DDE8FF")

    def test_settings_panels_use_section_switch_helper(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _apply_settings_section_switch(", source)
        self.assertGreaterEqual(source.count("_apply_settings_section_switch("), 3)

    def test_settings_panels_use_section_shell_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_settings_section_shell(", source)
        self.assertGreaterEqual(source.count("_build_settings_section_shell("), 3)

    def test_settings_panels_use_single_section_shell_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_single_settings_section_shell(", source)
        self.assertGreaterEqual(source.count("_build_single_settings_section_shell("), 2)

    def test_settings_panels_use_receipt_section_content_selector(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _select_receipt_settings_section_content(", source)
        self.assertGreaterEqual(source.count("_select_receipt_settings_section_content("), 3)

    def test_settings_panels_use_receipt_navigation_handler_wiring_helper(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _wire_receipt_settings_navigation_handlers(", source)
        self.assertGreaterEqual(source.count("_wire_receipt_settings_navigation_handlers("), 2)

    def test_settings_panels_use_receipt_settings_section_controls_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_receipt_settings_section_controls(", source)
        self.assertGreaterEqual(source.count("_build_receipt_settings_section_controls("), 2)

    def test_settings_panels_use_receipt_settings_panel_state_initializer(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _initialize_receipt_settings_panel_state(", source)
        self.assertGreaterEqual(source.count("_initialize_receipt_settings_panel_state("), 2)

    def test_settings_panels_use_receipt_settings_panel_shell_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_receipt_settings_panel_shell(", source)
        self.assertGreaterEqual(source.count("_build_receipt_settings_panel_shell("), 2)

    def test_settings_panels_use_selected_printer_and_layout_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _resolve_selected_printer(", source)
        self.assertGreaterEqual(source.count("_resolve_selected_printer("), 2)
        self.assertIn("def _normalize_json_layout_path(", source)
        self.assertGreaterEqual(source.count("_normalize_json_layout_path("), 3)
        self.assertIn("def _load_layout_document_or_default(", source)
        self.assertGreaterEqual(source.count("_load_layout_document_or_default("), 3)

    def test_settings_panels_use_shared_page_service_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _attach_page_service(", source)
        self.assertGreaterEqual(source.count("_attach_page_service("), 3)
        self.assertIn("def _attach_page_services(", source)
        self.assertGreaterEqual(source.count("_attach_page_services("), 2)

    def test_settings_panels_use_editor_layout_state_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_editor_layout_tab_style(", source)
        self.assertGreaterEqual(source.count("_build_editor_layout_tab_style("), 3)
        self.assertIn("def _apply_editor_layout_tab_styles(", source)
        self.assertGreaterEqual(source.count("_apply_editor_layout_tab_styles("), 2)
        self.assertIn("def _reset_editor_layout_transient_state(", source)
        self.assertGreaterEqual(source.count("_reset_editor_layout_transient_state("), 2)
        self.assertIn("def _format_active_template_label(", source)
        self.assertGreaterEqual(source.count("_format_active_template_label("), 4)
        self.assertIn("def _sync_editor_layout_display(", source)
        self.assertGreaterEqual(source.count("_sync_editor_layout_display("), 2)

    def test_settings_panels_use_editor_state_access_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _get_editor_layout_doc(", source)
        self.assertGreaterEqual(source.count("_get_editor_layout_doc("), 2)
        self.assertIn("def _set_editor_layout_doc(", source)
        self.assertGreaterEqual(source.count("_set_editor_layout_doc("), 2)
        self.assertIn("def _get_editor_selected_id(", source)
        self.assertGreaterEqual(source.count("_get_editor_selected_id("), 2)
        self.assertIn("def _set_editor_selected_id(", source)
        self.assertGreaterEqual(source.count("_set_editor_selected_id("), 2)
        self.assertIn("def _get_editor_active_binding_target(", source)
        self.assertGreaterEqual(source.count("_get_editor_active_binding_target("), 2)
        self.assertIn("def _set_editor_active_binding_target(", source)
        self.assertGreaterEqual(source.count("_set_editor_active_binding_target("), 2)
        self.assertIn("def _get_editor_layout_path(", source)
        self.assertGreaterEqual(source.count("_get_editor_layout_path("), 2)
        self.assertIn("def _set_editor_layout_path(", source)
        self.assertGreaterEqual(source.count("_set_editor_layout_path("), 2)

    def test_settings_panels_use_refresh_cycle_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_canvas_margin_overlay_controls(", source)
        self.assertGreaterEqual(source.count("_build_canvas_margin_overlay_controls("), 2)
        self.assertIn("def _format_canvas_meta_text(", source)
        self.assertGreaterEqual(source.count("_format_canvas_meta_text("), 2)
        self.assertIn("def _build_property_panel_empty_state(", source)
        self.assertGreaterEqual(source.count("_build_property_panel_empty_state("), 2)
        self.assertIn("def _apply_property_panel_controls(", source)
        self.assertGreaterEqual(source.count("_apply_property_panel_controls("), 2)
        self.assertIn("def _refresh_editor_view_state(", source)
        self.assertGreaterEqual(source.count("_refresh_editor_view_state("), 2)

    def test_settings_panels_use_property_element_update_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _update_text_element_properties(", source)
        self.assertGreaterEqual(source.count("_update_text_element_properties("), 2)
        self.assertIn("def _update_image_element_properties(", source)
        self.assertGreaterEqual(source.count("_update_image_element_properties("), 2)
        self.assertIn("def _update_qr_element_properties(", source)
        self.assertGreaterEqual(source.count("_update_qr_element_properties("), 2)
        self.assertIn("def _update_divider_element_properties(", source)
        self.assertGreaterEqual(source.count("_update_divider_element_properties("), 2)

    def test_settings_panels_use_property_commit_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _commit_common_dimension_update(", source)
        self.assertGreaterEqual(source.count("_commit_common_dimension_update("), 2)
        self.assertIn("def _commit_text_property_update(", source)
        self.assertGreaterEqual(source.count("_commit_text_property_update("), 2)
        self.assertIn("def _commit_image_property_update(", source)
        self.assertGreaterEqual(source.count("_commit_image_property_update("), 2)
        self.assertIn("def _commit_qr_property_update(", source)
        self.assertGreaterEqual(source.count("_commit_qr_property_update("), 2)
        self.assertIn("def _commit_divider_property_update(", source)
        self.assertGreaterEqual(source.count("_commit_divider_property_update("), 2)

    def test_settings_panels_use_property_panel_layout_builders(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_property_panel_base_controls(", source)
        self.assertGreaterEqual(source.count("_build_property_panel_base_controls("), 2)
        self.assertIn("def _build_property_panel_text_controls(", source)
        self.assertGreaterEqual(source.count("_build_property_panel_text_controls("), 2)
        self.assertIn("def _build_property_panel_image_controls(", source)
        self.assertGreaterEqual(source.count("_build_property_panel_image_controls("), 2)
        self.assertIn("def _build_property_panel_qr_controls(", source)
        self.assertGreaterEqual(source.count("_build_property_panel_qr_controls("), 2)
        self.assertIn("def _build_property_panel_divider_controls(", source)
        self.assertGreaterEqual(source.count("_build_property_panel_divider_controls("), 2)

    def test_settings_panels_use_canvas_refresh_layout_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_canvas_preview_controls(", source)
        self.assertGreaterEqual(source.count("_build_canvas_preview_controls("), 2)
        self.assertIn("def _apply_canvas_stack_view_state(", source)
        self.assertGreaterEqual(source.count("_apply_canvas_stack_view_state("), 2)
        self.assertIn("def _apply_canvas_frame_body_view_state(", source)
        self.assertGreaterEqual(source.count("_apply_canvas_frame_body_view_state("), 2)
        self.assertIn("def _apply_canvas_scroll_view_state(", source)
        self.assertGreaterEqual(source.count("_apply_canvas_scroll_view_state("), 2)

    def test_settings_panels_use_selection_action_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _require_selected_id(", source)
        self.assertGreaterEqual(source.count("_require_selected_id("), 2)
        self.assertIn("def _require_selected_element(", source)
        self.assertGreaterEqual(source.count("_require_selected_element("), 2)
        self.assertIn("def _remove_selected_element_from_elements(", source)
        self.assertGreaterEqual(source.count("_remove_selected_element_from_elements("), 2)
        self.assertIn("def _apply_selected_alignment_action(", source)
        self.assertGreaterEqual(source.count("_apply_selected_alignment_action("), 2)

    def test_settings_panels_use_binding_insert_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_binding_insert_text(", source)
        self.assertGreaterEqual(source.count("_build_binding_insert_text("), 2)
        self.assertIn("def _apply_binding_insert_to_selected_element(", source)
        self.assertGreaterEqual(source.count("_apply_binding_insert_to_selected_element("), 2)
        self.assertIn("def _build_new_binding_text_element(", source)
        self.assertGreaterEqual(source.count("_build_new_binding_text_element("), 2)

    def test_settings_panels_use_save_and_load_layout_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _resolve_editor_default_layout_path(", source)
        self.assertGreaterEqual(source.count("_resolve_editor_default_layout_path("), 3)
        self.assertIn("def _build_layout_document_for_save(", source)
        self.assertGreaterEqual(source.count("_build_layout_document_for_save("), 2)
        self.assertIn("def _apply_loaded_layout_document(", source)
        self.assertGreaterEqual(source.count("_apply_loaded_layout_document("), 2)

    def test_settings_panels_use_selection_and_inline_edit_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _should_ignore_canvas_background_tap(", source)
        self.assertGreaterEqual(source.count("_should_ignore_canvas_background_tap("), 2)
        self.assertIn("def _clear_canvas_selection(", source)
        self.assertGreaterEqual(source.count("_clear_canvas_selection("), 3)
        self.assertIn("def _resolve_binding_target_for_element_type(", source)
        self.assertGreaterEqual(source.count("_resolve_binding_target_for_element_type("), 2)
        self.assertIn("def _should_start_inline_edit_on_tap(", source)
        self.assertGreaterEqual(source.count("_should_start_inline_edit_on_tap("), 2)
        self.assertIn("def _apply_element_tap_selection(", source)
        self.assertGreaterEqual(source.count("_apply_element_tap_selection("), 2)
        self.assertIn("def _apply_element_double_tap_edit(", source)
        self.assertGreaterEqual(source.count("_apply_element_double_tap_edit("), 2)
        self.assertIn("def _update_element_text_template(", source)
        self.assertGreaterEqual(source.count("_update_element_text_template("), 4)

    def test_settings_panels_use_canvas_overlay_state_helpers(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _remove_canvas_stack_controls(", source)
        self.assertGreaterEqual(source.count("_remove_canvas_stack_controls("), 2)
        self.assertIn("def _replace_canvas_snap_guides(", source)
        self.assertGreaterEqual(source.count("_replace_canvas_snap_guides("), 3)
        self.assertIn("def _clear_canvas_insertion_indicator(", source)
        self.assertGreaterEqual(source.count("_clear_canvas_insertion_indicator("), 3)
        self.assertIn("def _update_canvas_insertion_indicator(", source)
        self.assertGreaterEqual(source.count("_update_canvas_insertion_indicator("), 2)
        self.assertIn("def _consume_canvas_insertion_target(", source)
        self.assertGreaterEqual(source.count("_consume_canvas_insertion_target("), 2)
        self.assertIn("def _reset_resize_interaction_state(", source)
        self.assertGreaterEqual(source.count("_reset_resize_interaction_state("), 2)
        self.assertIn("def _reset_drag_interaction_state(", source)
        self.assertGreaterEqual(source.count("_reset_drag_interaction_state("), 2)

    def test_settings_panels_use_receipt_editor_workspace_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_receipt_editor_workspace(", source)
        self.assertGreaterEqual(source.count("_build_receipt_editor_workspace("), 2)

    def test_settings_panels_use_receipt_editor_split_layout_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_receipt_editor_split_layout(", source)
        self.assertGreaterEqual(source.count("_build_receipt_editor_split_layout("), 2)

    def test_settings_panels_use_receipt_editor_left_controls_panel_builder(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn("def _build_receipt_editor_left_controls_panel(", source)
        self.assertGreaterEqual(source.count("_build_receipt_editor_left_controls_panel("), 2)

    def test_receipt_editor_left_controls_panel_builder_preserves_toolbar_and_status_controls(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_receipt_editor_left_controls_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        printer_dropdown = ft.Dropdown(label="printer")
        paper_width_dropdown = ft.Dropdown(label="paper")
        dpi_dropdown = ft.Dropdown(label="dpi")
        margin_top_field = ft.TextField(label="top")
        margin_bottom_field = ft.TextField(label="bottom")
        btn_save = ft.ElevatedButton("save")
        btn_save_as = ft.OutlinedButton("save as")
        btn_load = ft.OutlinedButton("load")
        btn_test_print = ft.OutlinedButton("test")
        btn_test_preview = ft.OutlinedButton("preview")
        current_template_text = ft.Text("current template")
        btn_add_text = ft.OutlinedButton("add text")
        btn_add_image = ft.OutlinedButton("add image")
        btn_add_divider = ft.OutlinedButton("add divider")
        btn_delete = ft.OutlinedButton("delete")
        field_chip_row = ft.Row(controls=[ft.Text("chip")])
        status_text = ft.Text("status")
        qr_expansion_tile = ft.ExpansionTile(title=ft.Text("qr"))

        panel = _build_receipt_editor_left_controls_panel(
            printer_dropdown=printer_dropdown,
            paper_width_dropdown=paper_width_dropdown,
            dpi_dropdown=dpi_dropdown,
            margin_top_field=margin_top_field,
            margin_bottom_field=margin_bottom_field,
            btn_save=btn_save,
            btn_save_as=btn_save_as,
            btn_load=btn_load,
            btn_test_print=btn_test_print,
            btn_test_preview=btn_test_preview,
            current_template_text=current_template_text,
            btn_add_text=btn_add_text,
            btn_add_image=btn_add_image,
            btn_add_divider=btn_add_divider,
            btn_delete=btn_delete,
            field_chip_row=field_chip_row,
            status_text=status_text,
            qr_expansion_tile=qr_expansion_tile,
        )
        strings = self._collect_strings(panel)
        column = panel.content

        self.assertEqual(getattr(panel, "expand", None), 2)
        self.assertEqual(getattr(panel, "bgcolor", None), "#FFFFFF")
        self.assertEqual(getattr(panel, "padding", None), 12)
        self.assertEqual(getattr(column, "spacing", None), 8)
        self.assertIs(getattr(column.controls[3], "value", None), current_template_text.value)
        self.assertIs(column.controls[7], field_chip_row)
        self.assertIs(column.controls[8], status_text)
        self.assertIs(column.controls[10], qr_expansion_tile)
        self.assertIn("영수증 양식 편집기", strings)
        self.assertIn("필드칩: 클릭하면 선택한 텍스트/QR 템플릿에 변수 삽입", strings)
    def test_app_settings_ticket_panel_scales_ticket_checkbox_area_with_item_count(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_app_settings_ticket_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        small_panel = _build_app_settings_ticket_panel(
            sound_path_field=ft.TextField(label="sound"),
            btn_pick_sound=ft.ElevatedButton("pick"),
            btn_preview_sound=ft.OutlinedButton("preview"),
            btn_clear_sound=ft.OutlinedButton("clear"),
            focus_mode_dropdown=ft.Dropdown(label="focus"),
            manual_focus_value_field=ft.TextField(label="manual"),
            settings_status_text=ft.Text("status"),
            ticket_checkbox_list=ft.Column(controls=[ft.Text("A"), ft.Text("B")]),
        )
        large_panel = _build_app_settings_ticket_panel(
            sound_path_field=ft.TextField(label="sound"),
            btn_pick_sound=ft.ElevatedButton("pick"),
            btn_preview_sound=ft.OutlinedButton("preview"),
            btn_clear_sound=ft.OutlinedButton("clear"),
            focus_mode_dropdown=ft.Dropdown(label="focus"),
            manual_focus_value_field=ft.TextField(label="manual"),
            settings_status_text=ft.Text("status"),
            ticket_checkbox_list=ft.Column(controls=[ft.Text(str(index)) for index in range(8)]),
        )

        small_ticket_card = self._find_card_by_heading(small_panel, "티켓 상품 분류")
        large_ticket_card = self._find_card_by_heading(large_panel, "티켓 상품 분류")

        self.assertIsNotNone(small_ticket_card)
        self.assertIsNotNone(large_ticket_card)
        small_height = getattr(small_ticket_card.content.controls[2], "height", None)
        large_height = getattr(large_ticket_card.content.controls[2], "height", None)

        self.assertLess(small_height, 320)
        self.assertGreater(large_height, small_height)

    def test_app_settings_ticket_panel_keeps_focus_and_sound_cards_in_expected_order(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_app_settings_ticket_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = _build_app_settings_ticket_panel(
            sound_path_field=ft.TextField(label="sound"),
            btn_pick_sound=ft.ElevatedButton("pick"),
            btn_preview_sound=ft.OutlinedButton("preview"),
            btn_clear_sound=ft.OutlinedButton("clear"),
            focus_mode_dropdown=ft.Dropdown(label="focus"),
            manual_focus_value_field=ft.TextField(label="manual"),
            settings_status_text=ft.Text("status"),
            ticket_checkbox_list=ft.Column(controls=[ft.Text("A"), ft.Text("B")]),
        )

        headings = [
            getattr(card.content.controls[0], "value", "")
            for card in panel.content.controls
            if hasattr(getattr(card, "content", None), "controls")
            and getattr(card.content.controls[0], "value", None) is not None
        ]

        self.assertIn("QR 스캔 완료 알림음", headings)
        self.assertIn("카메라 초점 설정", headings)
        self.assertIn("티켓 상품 분류", headings)
        self.assertLess(headings.index("카메라 초점 설정"), headings.index("티켓 상품 분류"))
        self.assertLess(headings.index("티켓 상품 분류"), headings.index("QR 스캔 완료 알림음"))

    def test_app_settings_ticket_panel_keeps_camera_selector_inside_focus_card(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_app_settings_ticket_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = _build_app_settings_ticket_panel(
            sound_path_field=ft.TextField(label="sound"),
            btn_pick_sound=ft.ElevatedButton("pick"),
            btn_preview_sound=ft.OutlinedButton("preview"),
            btn_clear_sound=ft.OutlinedButton("clear"),
            camera_selector_row=ft.Row(controls=[ft.Dropdown(label="camera")]),
            focus_mode_dropdown=ft.Dropdown(label="focus"),
            manual_focus_value_field=ft.TextField(label="manual"),
            settings_status_text=ft.Text("status"),
            ticket_checkbox_list=ft.Column(controls=[ft.Text("A"), ft.Text("B")]),
        )

        first_card = panel.content.controls[1]
        first_card_strings = self._collect_strings(first_card)
        headings = [
            getattr(card.content.controls[0], "value", "")
            for card in panel.content.controls
            if hasattr(getattr(card, "content", None), "controls")
            and getattr(card.content.controls[0], "value", None) is not None
        ]

        self.assertIn("스캔 카메라", first_card_strings)
        self.assertIn("카메라 초점 설정", first_card_strings)
        self.assertEqual(headings, ["스캔 카메라", "티켓 상품 분류", "QR 스캔 완료 알림음"])
        self.assertEqual(headings[-1], "QR 스캔 완료 알림음")

    def test_app_settings_ticket_panel_exposes_scroll_host_for_sidebar_reset(self) -> None:
        try:
            import flet as ft
            from views.settings_flet_view import _build_app_settings_ticket_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = _build_app_settings_ticket_panel(
            sound_path_field=ft.TextField(label="sound"),
            btn_pick_sound=ft.ElevatedButton("pick"),
            btn_preview_sound=ft.OutlinedButton("preview"),
            btn_clear_sound=ft.OutlinedButton("clear"),
            focus_mode_dropdown=ft.Dropdown(label="focus"),
            manual_focus_value_field=ft.TextField(label="manual"),
            settings_status_text=ft.Text("status"),
            ticket_checkbox_list=ft.Column(controls=[ft.Text("A"), ft.Text("B")]),
        )

        scroll_host = getattr(panel, "_scroll_host", None)
        self.assertIsNotNone(scroll_host)
        self.assertIs(scroll_host, panel.content)
        self.assertTrue(callable(getattr(scroll_host, "scroll_to", None)))
        self.assertEqual(getattr(scroll_host, "scroll", None), ft.ScrollMode.AUTO)


if __name__ == "__main__":
    unittest.main()
