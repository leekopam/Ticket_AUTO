"""Dashboard search auto-refresh contract tests."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class DashboardSearchRefreshContractTest(unittest.TestCase):
    def setUp(self) -> None:
        source = Path("views/dashboard_flet_view.py").read_text(encoding="utf-8-sig")
        self.source = source
        self.tree = ast.parse(source)

    def test_search_field_assigns_on_change_handler(self) -> None:
        found = False
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Attribute):
                continue
            target = node.targets[0]
            if (
                isinstance(target.value, ast.Name)
                and target.value.id == "search_field"
                and target.attr == "on_change"
            ):
                found = True
                break
        self.assertTrue(found)

    def test_runtime_event_uses_order_refresh_policy(self) -> None:
        found = False
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "should_auto_refresh_order_views":
                continue
            found = True
            break
        self.assertTrue(found)

    def test_runtime_event_dispatch_uses_dashboard_helper_with_search_refresh_callback(self) -> None:
        self.assertIn("dispatch_runtime_event_dashboard_state(", self.source)
        self.assertIn("lambda: refresh_active_order_views(push_update=False),", self.source)

    def test_runtime_status_refresh_uses_runtime_controls_helper(self) -> None:
        self.assertIn("dispatch_runtime_status_refresh(", self.source)
        self.assertIn("apply_runtime_controls_state(", self.source)

    def test_set_tab_uses_sidebar_dispatch_helper(self) -> None:
        self.assertIn("dispatch_sidebar_tab_change(", self.source)
        self.assertIn("refresh_search_results=lambda: refresh_active_order_views(push_update=False),", self.source)

    def test_do_search_uses_order_search_dashboard_helper(self) -> None:
        self.assertIn("apply_order_search_dashboard_state(", self.source)

    def test_watch_excel_changes_uses_watch_tick_helper(self) -> None:
        self.assertIn("process_search_refresh_watch_tick(", self.source)

    def test_update_buyer_info_uses_buyer_dashboard_helper(self) -> None:
        self.assertIn("apply_buyer_event_dashboard_state(", self.source)
        self.assertIn("refresh_print_controls=refresh_print_controls", self.source)

    def test_ticket_panel_uses_order_search_panel_helper(self) -> None:
        self.assertIn("build_order_search_panel(", self.source)

    def test_ticket_panel_uses_ticket_dashboard_panel_helper(self) -> None:
        self.assertIn("build_ticket_dashboard_panel(", self.source)

    def test_dashboard_uses_sidebar_builder_helper(self) -> None:
        self.assertIn("build_dashboard_sidebar(", self.source)

    def test_dashboard_uses_settings_dialog_builder_helper(self) -> None:
        self.assertIn("build_settings_dialog(", self.source)

    def test_dashboard_uses_shell_builder_helper(self) -> None:
        self.assertIn("build_dashboard_shell(", self.source)

    def test_dashboard_uses_bootstrap_helper(self) -> None:
        self.assertIn("bootstrap_dashboard_page(", self.source)

    def test_dashboard_uses_camera_focus_drawer_builders(self) -> None:
        self.assertIn("build_camera_focus_panel(", self.source)
        self.assertIn("build_camera_focus_drawer(", self.source)
        self.assertIn("build_camera_focus_side_handle(", self.source)
        self.assertIn("build_dashboard_overlay_host(", self.source)

    def test_dashboard_passes_runtime_focus_apply_callback_to_app_settings_panel(self) -> None:
        self.assertIn(
            "on_apply_scanner_focus_settings=self._runtime_manager.apply_scanner_focus_settings",
            self.source,
        )

    def test_dashboard_passes_focus_capability_and_native_settings_callbacks(self) -> None:
        self.assertIn(
            "focus_capability_getter=self._runtime_manager.get_scanner_focus_capability",
            self.source,
        )
        self.assertIn(
            "on_open_camera_settings=self._runtime_manager.open_scanner_camera_settings",
            self.source,
        )

    def test_dashboard_passes_ticket_product_refresh_callback_to_app_settings_panel(self) -> None:
        self.assertIn(
            "on_ticket_products_changed=_on_ticket_product_names_changed",
            self.source,
        )

    def test_dashboard_uses_offset_instead_of_right_jump_for_settings_handle(self) -> None:
        self.assertIn(
            "camera_focus_overlay_group.offset = (",
            self.source,
        )
        self.assertIn(
            "camera_focus_side_handle.right = CAMERA_SETTINGS_DRAWER_WIDTH",
            self.source,
        )

    def test_dashboard_bootstrap_mounts_overlay_shell_content(self) -> None:
        self.assertIn("shell_content=dashboard_overlay_host", self.source)

    def test_dashboard_starts_excel_watch_thread(self) -> None:
        found = False
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "Thread":
                continue
            for keyword in node.keywords:
                if keyword.arg != "target":
                    continue
                if isinstance(keyword.value, ast.Name) and keyword.value.id == "watch_excel_changes":
                    found = True
                    break
            if found:
                break
        self.assertTrue(found)

    def test_initial_ticket_tab_is_set_before_runtime_subscription(self) -> None:
        set_tab_index = self.source.find('set_tab("ticket", push_update=True)')
        subscribe_index = self.source.find("runtime_manager.subscribe(on_runtime_event)")
        self.assertNotEqual(set_tab_index, -1)
        self.assertNotEqual(subscribe_index, -1)
        self.assertLess(set_tab_index, subscribe_index)

    def test_dashboard_performs_initial_search_after_bootstrap(self) -> None:
        bootstrap_index = self.source.find("bootstrap_dashboard_page(")
        initial_search_index = self.source.find("do_search(push_update=True)")
        self.assertNotEqual(bootstrap_index, -1)
        self.assertNotEqual(initial_search_index, -1)
        self.assertLess(bootstrap_index, initial_search_index)

    def test_dashboard_unsubscribes_runtime_listener_before_stop_on_close(self) -> None:
        unsubscribe_index = self.source.find("runtime_manager.unsubscribe(on_runtime_event)")
        stop_index = self.source.find("runtime_manager.stop(timeout_sec=4.0)")
        self.assertNotEqual(unsubscribe_index, -1)
        self.assertNotEqual(stop_index, -1)
        self.assertLess(unsubscribe_index, stop_index)

    def test_camera_frame_refresh_uses_close_signal_when_updating_control(self) -> None:
        self.assertIn(
            "dispatch_camera_frame_update(page, camera_view, b64_str, search_refresh_stop)",
            self.source,
        )
        self.assertIn(
            "safe_page_update(camera_view, closing_event)",
            self.source,
        )

    def test_dashboard_tracks_search_blocked_state_separately_from_feedback_control(self) -> None:
        self.assertIn('search_blocked_state = {"value": False}', self.source)
        self.assertIn('search_blocked_state["value"] = search_view_state.search_blocked', self.source)

    def test_order_print_and_buyer_update_use_search_blocked_state(self) -> None:
        self.assertIn('refresh_print_controls(search_blocked=search_blocked_state["value"])', self.source)
        self.assertIn('search_blocked=search_blocked_state["value"]', self.source)
        self.assertNotIn('refresh_print_controls(search_blocked=bool(search_feedback_text.visible))', self.source)


    def test_dashboard_restores_receipt_panel_for_second_sidebar_tab(self) -> None:
        self.assertIn('btn_receipt_tab = ft.TextButton("영수증 양식"', self.source)
        self.assertIn("receipt_settings_panel=receipt_settings_panel", self.source)
        self.assertIn("receipt_panel = receipt_settings_panel_ref[\"value\"]", self.source)
        self.assertIn('if tab_key == "receipt" and receipt_panel is None:', self.source)
        self.assertIn("receipt_panel or receipt_settings_panel", self.source)

    def test_dashboard_uses_ticket_only_settings_panel_inside_ticket_sidebar(self) -> None:
        self.assertIn("show_section_tabs=False", self.source)
        self.assertIn("show_receipt_section=False", self.source)
        self.assertIn('focus_section_title="카메라 초점 기능"', self.source)

    def test_dashboard_lazily_builds_sidebar_settings_panels(self) -> None:
        self.assertIn('ticket_settings_sidebar_panel_ref: dict[str, ft.Control | None] = {"value": None}', self.source)
        self.assertIn('receipt_settings_sidebar_panel_ref: dict[str, ft.Control | None] = {"value": None}', self.source)
        self.assertIn("settings_sidebar_content_host.content = active_panel", self.source)
        self.assertIn("if is_open", self.source)

    def test_dashboard_resets_ticket_settings_scroll_after_mounting_sidebar_panel(self) -> None:
        assign_index = self.source.find("settings_sidebar_content_host.content = active_panel")
        reset_index = self.source.find("_reset_settings_panel_scroll(active_panel)")
        self.assertNotEqual(assign_index, -1)
        self.assertNotEqual(reset_index, -1)
        self.assertLess(assign_index, reset_index)

    def test_dashboard_handle_hover_updates_only_handle_style_without_reapplying_drawer(self) -> None:
        hover_index = self.source.find("def _on_camera_focus_handle_hover(e: ft.ControlEvent) -> None:")
        self.assertNotEqual(hover_index, -1)
        snippet = self.source[hover_index:hover_index + 260]
        self.assertIn("_apply_camera_focus_side_handle_style()", snippet)
        self.assertIn("safe_page_update(camera_focus_side_handle, search_refresh_stop)", snippet)
        self.assertNotIn("_apply_camera_focus_drawer()", snippet)

    def test_dashboard_receipt_sidebar_uses_lightweight_receipt_sidebar_settings_panel(self) -> None:
        self.assertIn("build_receipt_sidebar_settings_panel(", self.source)
        self.assertNotIn("bind_keyboard_events=False", self.source)

    def test_dashboard_sidebar_no_longer_wires_bottom_settings_button(self) -> None:
        self.assertNotIn('text="Settings"', self.source)
        self.assertNotIn("on_open_settings=", self.source)

    def test_dashboard_exposes_processed_count_reset_button(self) -> None:
        self.assertIn('"초기화"', self.source)
        self.assertIn("tooltip=\"처리완료 누적 카운트를 0으로 초기화\"", self.source)

    def test_dashboard_reset_button_resets_scan_success_count_store(self) -> None:
        self.assertIn("def _reset_processed_success_count", self.source)
        self.assertIn("scan_success_count_store.save_success_count(0)", self.source)


class NetworkSearchFocusContractTest(unittest.TestCase):
    """네트워크 탭 검색창 — 입력 중 포커스 유지 계약.

    버그 재발 방지: on_change/주기 갱신이 전체 page.update를 호출하면
    텍스트 필드 포커스가 날아가 한 글자씩만 입력되는 문제가 있었다.
    """

    def setUp(self) -> None:
        self.source = Path("views/dashboard_flet_view.py").read_text(encoding="utf-8-sig")

    def test_search_change_uses_partial_update(self) -> None:
        handler_index = self.source.find("def _on_network_search(")
        self.assertNotEqual(handler_index, -1)
        snippet = self.source[handler_index:handler_index + 400]
        self.assertIn("_update_network_search_results", snippet)

    def test_partial_update_targets_result_area_not_page(self) -> None:
        fn_index = self.source.find("def _update_network_search_results(")
        self.assertNotEqual(fn_index, -1)
        snippet = self.source[fn_index:fn_index + 900]
        # 결과 영역 컨트롤만 개별 갱신하고 필드 자체는 건드리지 않는다
        for key in ("device_list", "empty_box", "empty_text", "empty_reset"):
            self.assertIn(f'"{key}"', snippet)
        self.assertNotIn('controls["search_field"].update()', snippet)

    def test_focus_is_restored_after_partial_update(self) -> None:
        fn_index = self.source.find("def _update_network_search_results(")
        snippet = self.source[fn_index:fn_index + 900]
        self.assertIn('_search_editing["active"]', snippet)
        self.assertIn('network_controls["search_field"].focus()', snippet)

    def test_periodic_refresh_keeps_focus_while_editing(self) -> None:
        loop_index = self.source.find("def _network_watch_loop(")
        self.assertNotEqual(loop_index, -1)
        snippet = self.source[loop_index:loop_index + 1400]
        self.assertIn('_search_editing["active"]', snippet)
        self.assertIn("_update_network_search_results", snippet)

    def test_search_field_tracks_focus_state(self) -> None:
        self.assertIn('network_controls["search_field"].on_focus', self.source)
        self.assertIn('network_controls["search_field"].on_blur', self.source)


if __name__ == "__main__":
    unittest.main()
