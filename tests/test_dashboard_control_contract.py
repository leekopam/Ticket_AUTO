"""Dashboard control mapping tests."""
from __future__ import annotations

from types import SimpleNamespace
import unittest


class DashboardControlContractTest(unittest.TestCase):
    def _import_dashboard(self):
        try:
            import flet as ft
            from views import dashboard_flet_view as dashboard
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")
        return ft, dashboard

    def test_build_receipt_preview_dialog_uses_high_visibility_type_headers(self) -> None:
        _ft, dashboard = self._import_dashboard()

        dialog = dashboard.build_receipt_preview_dialog(
            preview_items=[
                ("영수증", "ZmFrZQ=="),
                ("상품 영수증", "ZmFrZQ=="),
            ],
            on_close=lambda _e: None,
        )

        self.assertEqual(getattr(dialog.title, "value", None), "영수증 미리보기 (2장)")

        content_column = dialog.content.content
        card_values = []
        for control in content_column.controls:
            content = getattr(control, "content", None)
            if content is None:
                continue
            header_row = content.controls[0]
            badge_text = header_row.controls[0].content.value
            title_text = content.controls[1].value
            card_values.append((badge_text, title_text))

        self.assertIn(("영수증", "영수증"), card_values)
        self.assertIn(("상품 영수증", "상품 영수증"), card_values)

    def test_format_next_special_rule_text_includes_target_count(self) -> None:
        _ft, dashboard = self._import_dashboard()

        view_state = dashboard.format_next_special_rule_text(
            SimpleNamespace(
                remaining_count=3,
                trigger_label="특정 번호 39",
                progress_value=0.7,
                next_target_count=39,
                current_count=36,
                trigger_type="specific_counts",
            )
        )

        self.assertTrue(view_state.visible)
        self.assertEqual(view_state.title_text, "특정 번호 39")
        self.assertEqual(view_state.hint_text, "다음 재생까지 3명 · 특정 번호 39")
        self.assertEqual(view_state.progress_value, 0.7)
        self.assertEqual(view_state.target_text, "39")
        self.assertEqual(view_state.badge_text, "특정 번호")
        self.assertIn("현재 36명 처리완료", view_state.tooltip_text)

    def test_format_next_special_rule_text_without_special_rule_uses_dash_target(self) -> None:
        _ft, dashboard = self._import_dashboard()

        view_state = dashboard.format_next_special_rule_text(None)

        self.assertFalse(view_state.visible)
        self.assertEqual(view_state.title_text, "다음 특수 규칙 없음")
        self.assertEqual(view_state.hint_text, "설정된 N 번마다 / 특정 번호 규칙이 없습니다.")
        self.assertEqual(view_state.progress_value, 0.0)
        self.assertEqual(view_state.target_text, "-")
        self.assertEqual(view_state.badge_text, "")

    def test_format_next_special_rule_text_prefers_configured_sound_name(self) -> None:
        _ft, dashboard = self._import_dashboard()

        view_state = dashboard.format_next_special_rule_text(
            SimpleNamespace(
                remaining_count=1,
                trigger_label="N 번마다 10",
                progress_value=0.9,
                next_target_count=40,
                current_count=39,
                trigger_type="every_n",
                sound_name="[테토] 감사합니다",
            )
        )

        self.assertEqual(view_state.title_text, "[테토] 감사합니다")
        self.assertEqual(view_state.badge_text, "N 번마다")

    def test_build_runtime_controls_state_maps_running_state_to_stop_button(self) -> None:
        _ft, dashboard = self._import_dashboard()

        controls_state = dashboard.build_runtime_controls_state("RUNNING")

        self.assertEqual(controls_state.badge_bgcolor, "#D8F4E3")
        self.assertEqual(controls_state.primary_text, "중지")
        self.assertEqual(controls_state.primary_bgcolor, "#D80000")
        self.assertFalse(controls_state.primary_disabled)
        self.assertFalse(controls_state.relogin_disabled)
        self.assertTrue(controls_state.uses_stop_action)

    def test_build_runtime_controls_state_maps_error_state_to_start_button(self) -> None:
        _ft, dashboard = self._import_dashboard()

        controls_state = dashboard.build_runtime_controls_state("ERROR")

        self.assertEqual(controls_state.badge_bgcolor, "#FFE0E0")
        self.assertEqual(controls_state.primary_text, "티켓 확인 시작")
        self.assertEqual(controls_state.primary_bgcolor, "#39C5BB")
        self.assertFalse(controls_state.primary_disabled)
        self.assertTrue(controls_state.relogin_disabled)
        self.assertFalse(controls_state.uses_stop_action)

    def test_build_runtime_status_view_state_formats_runtime_texts_and_badge(self) -> None:
        _ft, dashboard = self._import_dashboard()

        view_state = dashboard.build_runtime_status_view_state(
            "RUNNING",
            "스캔 중",
            "2026-03-29 12:00:00",
        )

        self.assertEqual(view_state.state_text, "RUNNING")
        self.assertEqual(view_state.badge_bgcolor, "#D8F4E3")
        self.assertEqual(view_state.runtime_hint_text, "스캔 중")
        self.assertEqual(view_state.last_event_text, "마지막 이벤트: 2026-03-29 12:00:00")
        self.assertTrue(view_state.controls_state.uses_stop_action)

    def test_apply_runtime_controls_state_sets_callbacks(self) -> None:
        _ft, dashboard = self._import_dashboard()

        class _FakeButton:
            def __init__(self) -> None:
                self.text = ""
                self.icon = None
                self.style = None
                self.disabled = True
                self.on_click = None

        def _on_start(_event=None) -> None:
            return None

        def _on_stop(_event=None) -> None:
            return None

        btn_relogin = _FakeButton()
        btn_start_stop = _FakeButton()

        dashboard.apply_runtime_controls_state(
            dashboard.build_runtime_controls_state("RUNNING"),
            btn_relogin=btn_relogin,
            btn_start_stop=btn_start_stop,
            on_start=_on_start,
            on_stop=_on_stop,
        )

        self.assertFalse(btn_relogin.disabled)
        self.assertEqual(btn_start_stop.text, "중지")
        self.assertFalse(btn_start_stop.disabled)
        self.assertIs(btn_start_stop.on_click, _on_stop)
        self.assertIsNotNone(btn_start_stop.style)

    def test_build_camera_focus_panel_keeps_controls_and_strings(self) -> None:
        ft, dashboard = self._import_dashboard()

        camera_selector_row = ft.Row(controls=[ft.Dropdown(label="camera")])
        focus_mode_dropdown = ft.Dropdown(label="focus")
        manual_focus_value_field = ft.TextField(label="manual")
        capability_badge = dashboard.build_camera_focus_capability_badge(
            text="현재 카메라는 수동 초점을 지원하지 않습니다.",
            visible=False,
        )

        panel = dashboard.build_camera_focus_panel(
            on_close=lambda _event=None: None,
            camera_selector_row=camera_selector_row,
            focus_mode_dropdown=focus_mode_dropdown,
            manual_focus_value_field=manual_focus_value_field,
            capability_badge=capability_badge,
        )

        strings = []
        for control in panel.content.controls[:2]:
            strings.append(getattr(control, "value", ""))

        self.assertEqual(strings, ["카메라 초점 기능", "현재 스캔용 웹캠의 초점 모드와 수동 값을 조정합니다."])
        self.assertIs(panel.content.controls[3], camera_selector_row)
        self.assertIs(panel.content.controls[4], capability_badge)
        self.assertIs(panel.content.controls[5], focus_mode_dropdown)
        self.assertIs(panel.content.controls[6], manual_focus_value_field)

    def test_build_camera_focus_side_handle_keeps_open_action_and_strings(self) -> None:
        ft, dashboard = self._import_dashboard()

        def _on_open(_event=None) -> None:
            return None

        def _on_hover(_event=None) -> None:
            return None

        handle = dashboard.build_camera_focus_side_handle(on_open=_on_open, on_hover=_on_hover)
        content = handle.content

        self.assertIs(handle.on_click, _on_open)
        self.assertEqual(handle.right, 0)
        self.assertEqual(handle.top, 332)
        self.assertEqual(handle.width, 28)
        self.assertEqual(handle.height, 116)
        self.assertEqual(handle.tooltip, "설정 열기")
        self.assertEqual(handle.bgcolor, "#DCE4EC")
        self.assertIs(handle.on_hover, _on_hover)
        self.assertIsInstance(content.controls[0].content, ft.Icon)
        self.assertEqual(content.controls[2].value, "설")
        self.assertEqual(content.controls[3].value, "정")

    def test_build_dashboard_sidebar_keeps_width_and_footer(self) -> None:
        ft, dashboard = self._import_dashboard()

        sidebar = dashboard.build_dashboard_sidebar(
            btn_ticket_tab=ft.TextButton("티켓 확인"),
            btn_receipt_tab=ft.TextButton("설정"),
        )

        column = sidebar.content
        footer_text = column.controls[-1].content

        self.assertEqual(sidebar.width, 244)
        self.assertEqual(sidebar.bgcolor, "#F5F6F8")
        self.assertEqual(footer_text.value, "v1 Control Center")


if __name__ == "__main__":
    unittest.main()
