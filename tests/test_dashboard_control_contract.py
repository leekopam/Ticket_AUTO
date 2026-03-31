"""Dashboard control mapping tests."""
from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from models.order_model import Order


class DashboardControlContractTest(unittest.TestCase):
    def test_build_receipt_preview_dialog_uses_high_visibility_type_headers(self) -> None:
        try:
            from views.dashboard_flet_view import build_receipt_preview_dialog
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        dialog = build_receipt_preview_dialog(
            preview_items=[
                ("?ìˆ˜ì¦?, "ZmFrZQ=="),
                ("?í’ˆ ?ìˆ˜ì¦?, "ZmFrZQ=="),
            ],
            on_close=lambda _e: None,
        )

        title = dialog.title
        self.assertEqual(getattr(title, "value", None), "?ìˆ˜ì¦?ë¯¸ë¦¬ë³´ê¸° (2??")

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

        self.assertIn(("?ìˆ˜ì¦?, "?ìˆ˜ì¦?), card_values)
        self.assertIn(("?í’ˆ ?ìˆ˜ì¦?, "?í’ˆ ?ìˆ˜ì¦?), card_values)

    def test_format_next_special_rule_text_includes_target_count(self) -> None:
        try:
            from views.dashboard_flet_view import format_next_special_rule_text
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        view_state = format_next_special_rule_text(
            SimpleNamespace(
                remaining_count=3,
                trigger_label="?¹ì • ë²ˆí˜¸ 39",
                progress_value=0.7,
                next_target_count=39,
                current_count=36,
                trigger_type="specific_counts",
            )
        )

        self.assertTrue(view_state.visible)
        self.assertEqual(view_state.title_text, "?¹ì • ë²ˆí˜¸ 39")
        self.assertEqual(view_state.hint_text, "?¤ìŒ ?¬ìƒê¹Œì? 3ëª?Â· ?¹ì • ë²ˆí˜¸ 39")
        self.assertEqual(view_state.progress_value, 0.7)
        self.assertEqual(view_state.target_text, "39")
        self.assertEqual(view_state.badge_text, "?¹ì • ë²ˆí˜¸")
        self.assertEqual(view_state.badge_bgcolor, "#DCE4EC")
        self.assertEqual(view_state.card_bgcolor, "#E9F8F6")
        self.assertEqual(view_state.progress_color, "#39C5BB")
        self.assertIn("?¹ì • ë²ˆí˜¸ 39: ?„ìž¬ 36ëª?ì²˜ë¦¬?„ë£Œ", view_state.tooltip_text)

    def test_format_next_special_rule_text_without_special_rule_uses_dash_target(self) -> None:
        try:
            from views.dashboard_flet_view import format_next_special_rule_text
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        view_state = format_next_special_rule_text(None)

        self.assertFalse(view_state.visible)
        self.assertEqual(view_state.title_text, "?¤ìŒ ?¹ìˆ˜ ê·œì¹™ ?†ìŒ")
        self.assertEqual(view_state.hint_text, "?¤ì •??N ë²ˆë§ˆ??/ ?¹ì • ë²ˆí˜¸ ê·œì¹™???†ìŠµ?ˆë‹¤.")
        self.assertEqual(view_state.progress_value, 0.0)
        self.assertEqual(view_state.target_text, "-")
        self.assertEqual(view_state.badge_text, "")
        self.assertEqual(view_state.card_bgcolor, "#F5F9FF")
        self.assertIn("?¤ì •?˜ì? ?Šì•˜?µë‹ˆ??, view_state.tooltip_text)

    def test_format_next_special_rule_text_marks_every_n_with_repeat_badge(self) -> None:
        try:
            from views.dashboard_flet_view import format_next_special_rule_text
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        view_state = format_next_special_rule_text(
            SimpleNamespace(
                remaining_count=4,
                trigger_label="N ë²ˆë§ˆ??10",
                progress_value=0.6,
                next_target_count=20,
                current_count=16,
                trigger_type="every_n",
            )
        )

        self.assertTrue(view_state.visible)
        self.assertEqual(view_state.badge_text, "N ë²ˆë§ˆ??)
        self.assertEqual(view_state.badge_bgcolor, "#DFF5E4")
        self.assertEqual(view_state.card_bgcolor, "#F3FBF5")

    def test_format_next_special_rule_text_prefers_configured_sound_name(self) -> None:
        try:
            from views.dashboard_flet_view import format_next_special_rule_text
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        view_state = format_next_special_rule_text(
            SimpleNamespace(
                remaining_count=1,
                trigger_label="N \ubc88\ub9c8\ub2e4 10",
                progress_value=0.9,
                next_target_count=40,
                current_count=39,
                trigger_type="every_n",
                sound_name="[?Œí† ] ê°ì‚¬?©ë‹ˆ??,
            )
        )

        self.assertEqual(view_state.title_text, "[?Œí† ] ê°ì‚¬?©ë‹ˆ??)

    def test_build_special_rule_progress_card_renders_visible_card(self) -> None:
        try:
            from views.dashboard_flet_view import build_special_rule_progress_card, format_next_special_rule_text
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        view_state = format_next_special_rule_text(
            SimpleNamespace(
                remaining_count=2,
                trigger_label="?¹ì • ë²ˆí˜¸ 39",
                progress_value=0.8,
                next_target_count=39,
                current_count=37,
                trigger_type="specific_counts",
            )
        )

        card = build_special_rule_progress_card(view_state)

        self.assertTrue(card.visible)
        self.assertEqual(card.bgcolor, "#E9F8F6")
        self.assertEqual(card.tooltip, "?¹ì • ë²ˆí˜¸ 39: ?„ìž¬ 37ëª?ì²˜ë¦¬?„ë£Œ, ?¤ìŒ ëª©í‘œ 39ëª?)
        self.assertEqual(view_state.progress_color, "#39C5BB")

    def test_build_special_rule_progress_sections_groups_cards_by_trigger_type(self) -> None:
        try:
            from views.dashboard_flet_view import (
                build_special_rule_progress_sections,
                format_next_special_rule_text,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        specific_view_state = format_next_special_rule_text(
            SimpleNamespace(
                remaining_count=2,
                trigger_label="?¹ì • ë²ˆí˜¸ 39",
                progress_value=0.8,
                next_target_count=39,
                current_count=37,
                trigger_type="specific_counts",
            )
        )
        recurring_view_state = format_next_special_rule_text(
            SimpleNamespace(
                remaining_count=3,
                trigger_label="N ë²ˆë§ˆ??10",
                progress_value=0.7,
                next_target_count=40,
                current_count=37,
                trigger_type="every_n",
            )
        )

        sections = build_special_rule_progress_sections([specific_view_state, recurring_view_state])

        self.assertEqual(len(sections), 2)
        first_header = sections[0].content.controls[0].content.controls[1]
        second_header = sections[1].content.controls[0].content.controls[1]
        self.assertEqual(first_header.value, "?¹ì • ë²ˆí˜¸ ê·œì¹™")
        self.assertEqual(second_header.value, "N ë²ˆë§ˆ??ê·œì¹™")
        self.assertEqual(len(sections[0].content.controls[1].controls), 1)
        self.assertEqual(len(sections[1].content.controls[1].controls), 1)

    def test_build_special_rule_progress_sections_wraps_cards_by_three_per_row(self) -> None:
        try:
            from views.dashboard_flet_view import (
                build_special_rule_progress_sections,
                format_next_special_rule_text,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        view_states = [
            format_next_special_rule_text(
                SimpleNamespace(
                    remaining_count=index + 1,
                    trigger_label=f"?¹ì • ë²ˆí˜¸ {39 + index}",
                    progress_value=0.2 * (index + 1),
                    next_target_count=39 + index,
                    current_count=35,
                    trigger_type="specific_counts",
                    sound_name=f"sound-{index}.mp3",
                )
            )
            for index in range(4)
        ]

        sections = build_special_rule_progress_sections(view_states)

        self.assertEqual(len(sections), 1)
        card_rows = sections[0].content.controls[1].controls
        self.assertEqual(len(card_rows), 2)
        self.assertEqual(len(card_rows[0].controls), 3)
        self.assertEqual(len(card_rows[1].controls), 1)

    def test_button_state_mapping(self) -> None:
        try:
            from views.dashboard_flet_view import compute_button_enabled
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(compute_button_enabled("IDLE"), (True, False, False))
        self.assertEqual(compute_button_enabled("RUNNING"), (False, True, True))
        self.assertEqual(compute_button_enabled("RECOVERING"), (False, True, True))
        self.assertEqual(compute_button_enabled("ERROR"), (True, False, False))

    def test_runtime_controls_state_maps_running_state_to_stop_button(self) -> None:
        try:
            from views.dashboard_flet_view import build_runtime_controls_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        controls_state = build_runtime_controls_state("RUNNING")

        self.assertEqual(controls_state.badge_bgcolor, "#D8F4E3")
        self.assertEqual(controls_state.primary_text, "ì¤‘ì?")
        self.assertEqual(controls_state.primary_bgcolor, "#DD4C4C")
        self.assertFalse(controls_state.primary_disabled)
        self.assertFalse(controls_state.relogin_disabled)
        self.assertTrue(controls_state.uses_stop_action)

    def test_runtime_controls_state_maps_error_state_to_start_button(self) -> None:
        try:
            from views.dashboard_flet_view import build_runtime_controls_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        controls_state = build_runtime_controls_state("ERROR")

        self.assertEqual(controls_state.badge_bgcolor, "#FFD6D6")
        self.assertEqual(controls_state.primary_text, "?°ì¼“ ?•ì¸ ?œìž‘")
        self.assertEqual(controls_state.primary_bgcolor, "#39C5BB")
        self.assertFalse(controls_state.primary_disabled)
        self.assertTrue(controls_state.relogin_disabled)
        self.assertFalse(controls_state.uses_stop_action)

    def test_runtime_status_view_state_formats_runtime_texts_and_badge(self) -> None:
        try:
            from views.dashboard_flet_view import build_runtime_status_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        view_state = build_runtime_status_view_state(
            "RUNNING",
            "?¤ìº” ì¤?,
            "2026-03-29 12:00:00",
        )

        self.assertEqual(view_state.state_text, "RUNNING")
        self.assertEqual(view_state.badge_bgcolor, "#D8F4E3")
        self.assertEqual(view_state.runtime_hint_text, "?¤ìº” ì¤?)
        self.assertEqual(view_state.last_event_text, "ë§ˆì?ë§??´ë²¤?? 2026-03-29 12:00:00")
        self.assertTrue(view_state.controls_state.uses_stop_action)

    def test_runtime_status_view_state_reuses_error_control_mapping(self) -> None:
        try:
            from views.dashboard_flet_view import build_runtime_status_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        view_state = build_runtime_status_view_state(
            "ERROR",
            "?¤ë¥˜ ë°œìƒ",
            "-",
        )

        self.assertEqual(view_state.badge_bgcolor, "#FFD6D6")
        self.assertEqual(view_state.runtime_hint_text, "?¤ë¥˜ ë°œìƒ")
        self.assertEqual(view_state.last_event_text, "ë§ˆì?ë§??´ë²¤?? -")
        self.assertFalse(view_state.controls_state.uses_stop_action)
        self.assertEqual(view_state.controls_state.primary_bgcolor, "#39C5BB")

    def test_apply_runtime_status_view_state_updates_runtime_controls(self) -> None:
        try:
            from views.dashboard_flet_view import apply_runtime_status_view_state, build_runtime_status_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.bgcolor = ""

        current_state = {"value": "IDLE"}
        state_text = _FakeControl()
        state_badge = _FakeControl()
        runtime_hint_text = _FakeControl()
        last_event_text = _FakeControl()
        view_state = build_runtime_status_view_state("RUNNING", "?¤ìº” ì¤?, "2026-03-29 12:00:00")

        apply_runtime_status_view_state(
            view_state,
            current_state=current_state,
            state_text=state_text,
            state_badge=state_badge,
            runtime_hint_text=runtime_hint_text,
            last_event_text=last_event_text,
        )

        self.assertEqual(current_state["value"], "RUNNING")
        self.assertEqual(state_text.value, "RUNNING")
        self.assertEqual(state_badge.bgcolor, "#D8F4E3")
        self.assertEqual(runtime_hint_text.value, "?¤ìº” ì¤?)
        self.assertEqual(last_event_text.value, "ë§ˆì?ë§??´ë²¤?? 2026-03-29 12:00:00")

    def test_apply_runtime_status_view_state_handles_error_values(self) -> None:
        try:
            from views.dashboard_flet_view import apply_runtime_status_view_state, build_runtime_status_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = "old"
                self.bgcolor = "#000000"

        current_state = {"value": "RUNNING"}
        state_text = _FakeControl()
        state_badge = _FakeControl()
        runtime_hint_text = _FakeControl()
        last_event_text = _FakeControl()
        view_state = build_runtime_status_view_state("ERROR", "?¤ë¥˜ ë°œìƒ", "-")

        apply_runtime_status_view_state(
            view_state,
            current_state=current_state,
            state_text=state_text,
            state_badge=state_badge,
            runtime_hint_text=runtime_hint_text,
            last_event_text=last_event_text,
        )

        self.assertEqual(current_state["value"], "ERROR")
        self.assertEqual(state_text.value, "ERROR")
        self.assertEqual(state_badge.bgcolor, "#FFD6D6")
        self.assertEqual(runtime_hint_text.value, "?¤ë¥˜ ë°œìƒ")
        self.assertEqual(last_event_text.value, "ë§ˆì?ë§??´ë²¤?? -")

    def test_apply_runtime_controls_state_sets_stop_action_and_enabled_buttons(self) -> None:
        try:
            from views.dashboard_flet_view import apply_runtime_controls_state, build_runtime_controls_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

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

        apply_runtime_controls_state(
            build_runtime_controls_state("RUNNING"),
            btn_relogin=btn_relogin,
            btn_start_stop=btn_start_stop,
            on_start=_on_start,
            on_stop=_on_stop,
        )

        self.assertFalse(btn_relogin.disabled)
        self.assertEqual(btn_start_stop.text, "ì¤‘ì?")
        self.assertFalse(btn_start_stop.disabled)
        self.assertIs(btn_start_stop.on_click, _on_stop)
        self.assertIsNotNone(btn_start_stop.style)

    def test_apply_runtime_controls_state_sets_start_action_for_error_state(self) -> None:
        try:
            from views.dashboard_flet_view import apply_runtime_controls_state, build_runtime_controls_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeButton:
            def __init__(self) -> None:
                self.text = ""
                self.icon = None
                self.style = None
                self.disabled = False
                self.on_click = None

        def _on_start(_event=None) -> None:
            return None

        def _on_stop(_event=None) -> None:
            return None

        btn_relogin = _FakeButton()
        btn_start_stop = _FakeButton()

        apply_runtime_controls_state(
            build_runtime_controls_state("ERROR"),
            btn_relogin=btn_relogin,
            btn_start_stop=btn_start_stop,
            on_start=_on_start,
            on_stop=_on_stop,
        )

        self.assertTrue(btn_relogin.disabled)
        self.assertEqual(btn_start_stop.text, "?°ì¼“ ?•ì¸ ?œìž‘")
        self.assertFalse(btn_start_stop.disabled)
        self.assertIs(btn_start_stop.on_click, _on_start)
        self.assertIsNotNone(btn_start_stop.style)

    def test_dispatch_runtime_status_refresh_builds_running_view_state_and_updates_page(self) -> None:
        try:
            from views.dashboard_flet_view import dispatch_runtime_status_refresh, build_runtime_controls_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.bgcolor = ""
                self.text = ""
                self.icon = None
                self.style = None
                self.disabled = True
                self.on_click = None

        class _FakePage:
            def __init__(self) -> None:
                self.update_calls = 0

            def update(self) -> None:
                self.update_calls += 1

        def _on_start(_event=None) -> None:
            return None

        def _on_stop(_event=None) -> None:
            return None

        current_state = {"value": "IDLE"}
        state_text = _FakeControl()
        state_badge = _FakeControl()
        runtime_hint_text = _FakeControl()
        last_event_text = _FakeControl()
        btn_relogin = _FakeControl()
        btn_start_stop = _FakeControl()
        page = _FakePage()
        controls_state = build_runtime_controls_state("RUNNING")

        dispatch_runtime_status_refresh(
            "RUNNING",
            "?¤ìº” ì¤?,
            "2026-03-29 12:00:00",
            current_state=current_state,
            state_text=state_text,
            state_badge=state_badge,
            runtime_hint_text=runtime_hint_text,
            last_event_text=last_event_text,
            btn_relogin=btn_relogin,
            btn_start_stop=btn_start_stop,
            on_start=_on_start,
            on_stop=_on_stop,
            page=page,
        )

        self.assertEqual(current_state["value"], "RUNNING")
        self.assertEqual(state_text.value, "RUNNING")
        self.assertEqual(state_badge.bgcolor, "#D8F4E3")
        self.assertEqual(runtime_hint_text.value, "?¤ìº” ì¤?)
        self.assertEqual(btn_start_stop.text, controls_state.primary_text)
        self.assertIs(btn_start_stop.on_click, _on_stop)
        self.assertEqual(page.update_calls, 1)

    def test_dispatch_runtime_status_refresh_reuses_provided_view_state(self) -> None:
        try:
            from views.dashboard_flet_view import (
                build_runtime_status_view_state,
                dispatch_runtime_status_refresh,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.bgcolor = ""
                self.text = ""
                self.icon = None
                self.style = None
                self.disabled = True
                self.on_click = None

        class _FakePage:
            def __init__(self) -> None:
                self.update_calls = 0

            def update(self) -> None:
                self.update_calls += 1

        def _on_start(_event=None) -> None:
            return None

        def _on_stop(_event=None) -> None:
            return None

        current_state = {"value": "IDLE"}
        state_text = _FakeControl()
        state_badge = _FakeControl()
        runtime_hint_text = _FakeControl()
        last_event_text = _FakeControl()
        btn_relogin = _FakeControl()
        btn_start_stop = _FakeControl()
        page = _FakePage()
        provided_view_state = build_runtime_status_view_state("ERROR", "?¤ë¥˜ ë°œìƒ", "-")

        dispatch_runtime_status_refresh(
            "RUNNING",
            "?¤ìº” ì¤?,
            "2026-03-29 12:00:00",
            current_state=current_state,
            state_text=state_text,
            state_badge=state_badge,
            runtime_hint_text=runtime_hint_text,
            last_event_text=last_event_text,
            btn_relogin=btn_relogin,
            btn_start_stop=btn_start_stop,
            on_start=_on_start,
            on_stop=_on_stop,
            page=page,
            status_view_state=provided_view_state,
            push_update=False,
        )

        self.assertEqual(current_state["value"], "ERROR")
        self.assertEqual(state_text.value, "ERROR")
        self.assertEqual(runtime_hint_text.value, "?¤ë¥˜ ë°œìƒ")
        self.assertEqual(last_event_text.value, "ë§ˆì?ë§??´ë²¤?? -")
        self.assertIs(btn_start_stop.on_click, _on_start)
        self.assertEqual(page.update_calls, 0)

    def test_tab_resolution_switches_content(self) -> None:
        try:
            from views.dashboard_flet_view import resolve_tab_content
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        ticket = object()
        receipt = object()
        self.assertIs(resolve_tab_content("ticket", ticket, receipt), ticket)
        self.assertIs(resolve_tab_content("receipt", ticket, receipt), receipt)

    def test_sidebar_tab_state_selects_ticket_panel_and_refreshes_search(self) -> None:
        try:
            from views.dashboard_flet_view import build_sidebar_tab_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        ticket = object()
        receipt = object()

        state = build_sidebar_tab_state("ticket", ticket, receipt)

        self.assertIs(state.content, ticket)
        self.assertEqual(state.ticket_tab_bgcolor, "#E6EEFF")
        self.assertEqual(state.receipt_tab_bgcolor, "#00000000")
        self.assertTrue(state.should_refresh_search)

    def test_sidebar_tab_state_selects_receipt_panel_without_refresh(self) -> None:
        try:
            from views.dashboard_flet_view import build_sidebar_tab_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        ticket = object()
        receipt = object()

        state = build_sidebar_tab_state("receipt", ticket, receipt)

        self.assertIs(state.content, receipt)
        self.assertEqual(state.ticket_tab_bgcolor, "#00000000")
        self.assertEqual(state.receipt_tab_bgcolor, "#E6EEFF")
        self.assertFalse(state.should_refresh_search)

    def test_apply_sidebar_tab_view_state_updates_ticket_selection_and_content(self) -> None:
        try:
            from views.dashboard_flet_view import apply_sidebar_tab_view_state, build_sidebar_tab_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.content = None
                self.style = None

        ticket = object()
        receipt = object()
        current_tab = {"value": "receipt"}
        content_host = _FakeControl()
        btn_ticket_tab = _FakeControl()
        btn_receipt_tab = _FakeControl()
        tab_state = build_sidebar_tab_state("ticket", ticket, receipt)

        apply_sidebar_tab_view_state(
            tab_state,
            current_tab=current_tab,
            tab_key="ticket",
            content_host=content_host,
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
        )

        self.assertEqual(current_tab["value"], "ticket")
        self.assertIs(content_host.content, ticket)
        self.assertIsNotNone(btn_ticket_tab.style)
        self.assertIsNotNone(btn_receipt_tab.style)

    def test_apply_sidebar_tab_view_state_updates_receipt_selection_and_content(self) -> None:
        try:
            from views.dashboard_flet_view import apply_sidebar_tab_view_state, build_sidebar_tab_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.content = None
                self.style = None

        ticket = object()
        receipt = object()
        current_tab = {"value": "ticket"}
        content_host = _FakeControl()
        btn_ticket_tab = _FakeControl()
        btn_receipt_tab = _FakeControl()
        tab_state = build_sidebar_tab_state("receipt", ticket, receipt)

        apply_sidebar_tab_view_state(
            tab_state,
            current_tab=current_tab,
            tab_key="receipt",
            content_host=content_host,
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
        )

        self.assertEqual(current_tab["value"], "receipt")
        self.assertIs(content_host.content, receipt)
        self.assertIsNotNone(btn_ticket_tab.style)
        self.assertIsNotNone(btn_receipt_tab.style)

    def test_dispatch_sidebar_tab_change_refreshes_search_for_ticket_tab(self) -> None:
        try:
            from views.dashboard_flet_view import build_sidebar_tab_state, dispatch_sidebar_tab_change
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.content = None
                self.style = None

        class _FakePage:
            def __init__(self) -> None:
                self.update_calls = 0

            def update(self) -> None:
                self.update_calls += 1

        ticket = object()
        receipt = object()
        current_tab = {"value": "receipt"}
        content_host = _FakeControl()
        btn_ticket_tab = _FakeControl()
        btn_receipt_tab = _FakeControl()
        page = _FakePage()
        refresh_calls: list[str] = []
        tab_state = build_sidebar_tab_state("ticket", ticket, receipt)

        dispatch_sidebar_tab_change(
            "ticket",
            tab_state,
            current_tab=current_tab,
            content_host=content_host,
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
            refresh_search_results=lambda: refresh_calls.append("search"),
            page=page,
        )

        self.assertEqual(current_tab["value"], "ticket")
        self.assertIs(content_host.content, ticket)
        self.assertEqual(btn_ticket_tab.style.bgcolor, "#E9F8F6")
        self.assertEqual(btn_receipt_tab.style.bgcolor, "#00000000")
        self.assertEqual(refresh_calls, ["search"])
        self.assertEqual(page.update_calls, 1)

    def test_dispatch_sidebar_tab_change_skips_search_for_receipt_tab(self) -> None:
        try:
            from views.dashboard_flet_view import build_sidebar_tab_state, dispatch_sidebar_tab_change
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.content = None
                self.style = None

        class _FakePage:
            def __init__(self) -> None:
                self.update_calls = 0

            def update(self) -> None:
                self.update_calls += 1

        ticket = object()
        receipt = object()
        current_tab = {"value": "ticket"}
        content_host = _FakeControl()
        btn_ticket_tab = _FakeControl()
        btn_receipt_tab = _FakeControl()
        page = _FakePage()
        refresh_calls: list[str] = []
        tab_state = build_sidebar_tab_state("receipt", ticket, receipt)

        dispatch_sidebar_tab_change(
            "receipt",
            tab_state,
            current_tab=current_tab,
            content_host=content_host,
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
            refresh_search_results=lambda: refresh_calls.append("search"),
            page=page,
        )

        self.assertEqual(current_tab["value"], "receipt")
        self.assertIs(content_host.content, receipt)
        self.assertEqual(btn_ticket_tab.style.bgcolor, "#00000000")
        self.assertEqual(btn_receipt_tab.style.bgcolor, "#E9F8F6")
        self.assertEqual(refresh_calls, [])
        self.assertEqual(page.update_calls, 1)

    def test_buyer_event_view_state_shows_detail_and_propagates_search_block(self) -> None:
        try:
            from views.dashboard_flet_view import build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="A-100",
            name="Kim",
            phone="010-0000-0000",
            seat="A1",
            goods=["VIP x1", "Poster x1"],
            received_at="2026-03-29 10:30:00",
        )

        state = build_buyer_event_view_state(
            order,
            ["VIP"],
            search_blocked=True,
        )

        self.assertIs(state.order, order)
        self.assertTrue(state.search_blocked)
        self.assertTrue(state.buyer_detail_visible)
        self.assertFalse(state.buyer_empty_hint_visible)
        self.assertTrue(state.panel_state.ticket_visible)
        self.assertTrue(state.panel_state.received_visible)

    def test_buyer_event_view_state_keeps_optional_sections_hidden_for_plain_goods(self) -> None:
        try:
            from views.dashboard_flet_view import build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="B-200",
            name="Lee",
            phone="010-1111-2222",
            seat="B2",
            goods=["General x1"],
            received_at="",
        )

        state = build_buyer_event_view_state(
            order,
            ["VIP"],
            search_blocked=False,
        )

        self.assertFalse(state.search_blocked)
        self.assertTrue(state.buyer_detail_visible)
        self.assertFalse(state.buyer_empty_hint_visible)
        self.assertFalse(state.panel_state.ticket_visible)
        self.assertFalse(state.panel_state.received_visible)

    def test_apply_buyer_event_view_state_updates_text_and_visibility_controls(self) -> None:
        try:
            from views.dashboard_flet_view import apply_buyer_event_view_state, build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.visible = False

        order = Order(
            order_number="A-100",
            name="Kim",
            phone="010-0000-0000",
            seat="A1",
            goods=["VIP x1", "Poster x1"],
            received_at="2026-03-29 10:30:00",
        )
        view_state = build_buyer_event_view_state(order, ["VIP"], search_blocked=False)
        current_buyer_order: dict[str, Order | None] = {"value": None}

        buyer_name_text = _FakeControl()
        buyer_phone_text = _FakeControl()
        buyer_seat_text = _FakeControl()
        buyer_goods_text = _FakeControl()
        buyer_ticket_text = _FakeControl()
        buyer_received_text = _FakeControl()
        buyer_detail_col = _FakeControl()
        buyer_empty_hint = _FakeControl()
        buyer_empty_hint.visible = True

        apply_buyer_event_view_state(
            view_state,
            current_buyer_order=current_buyer_order,
            buyer_name_text=buyer_name_text,
            buyer_phone_text=buyer_phone_text,
            buyer_seat_text=buyer_seat_text,
            buyer_goods_text=buyer_goods_text,
            buyer_ticket_text=buyer_ticket_text,
            buyer_received_text=buyer_received_text,
            buyer_detail_col=buyer_detail_col,
            buyer_empty_hint=buyer_empty_hint,
        )

        self.assertIs(current_buyer_order["value"], order)
        self.assertEqual(buyer_name_text.value, "ì£¼ë¬¸?ëª…: Kim")
        self.assertEqual(buyer_phone_text.value, "?°ë½ì²? 010-0000-0000")
        self.assertEqual(buyer_goods_text.value, "?í’ˆ: Poster x1")
        self.assertEqual(buyer_ticket_text.value, "?°ì¼“: VIP x1")
        self.assertTrue(buyer_ticket_text.visible)
        self.assertEqual(buyer_received_text.value, "?˜ë ¹?„ë£Œ: 2026-03-29 10:30:00")
        self.assertTrue(buyer_received_text.visible)
        self.assertTrue(buyer_detail_col.visible)
        self.assertFalse(buyer_empty_hint.visible)

    def test_apply_buyer_event_view_state_hides_optional_sections_for_plain_goods(self) -> None:
        try:
            from views.dashboard_flet_view import apply_buyer_event_view_state, build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.visible = True

        order = Order(
            order_number="B-200",
            name="Lee",
            phone="010-1111-2222",
            seat="B2",
            goods=["General x1"],
            received_at="",
        )
        view_state = build_buyer_event_view_state(order, ["VIP"], search_blocked=True)
        current_buyer_order: dict[str, Order | None] = {"value": None}

        buyer_name_text = _FakeControl()
        buyer_phone_text = _FakeControl()
        buyer_seat_text = _FakeControl()
        buyer_goods_text = _FakeControl()
        buyer_ticket_text = _FakeControl()
        buyer_received_text = _FakeControl()
        buyer_detail_col = _FakeControl()
        buyer_empty_hint = _FakeControl()

        apply_buyer_event_view_state(
            view_state,
            current_buyer_order=current_buyer_order,
            buyer_name_text=buyer_name_text,
            buyer_phone_text=buyer_phone_text,
            buyer_seat_text=buyer_seat_text,
            buyer_goods_text=buyer_goods_text,
            buyer_ticket_text=buyer_ticket_text,
            buyer_received_text=buyer_received_text,
            buyer_detail_col=buyer_detail_col,
            buyer_empty_hint=buyer_empty_hint,
        )

        self.assertIs(current_buyer_order["value"], order)
        self.assertEqual(buyer_goods_text.value, "?í’ˆ: General x1")
        self.assertEqual(buyer_ticket_text.value, "")
        self.assertFalse(buyer_ticket_text.visible)
        self.assertEqual(buyer_received_text.value, "")
        self.assertFalse(buyer_received_text.visible)
        self.assertTrue(buyer_detail_col.visible)
        self.assertFalse(buyer_empty_hint.visible)

    def test_apply_buyer_event_dashboard_state_updates_controls_and_refresh_state(self) -> None:
        try:
            from views.dashboard_flet_view import apply_buyer_event_dashboard_state, build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.visible = False

        order = Order(
            order_number="A-100",
            name="Kim",
            phone="010-0000-0000",
            seat="A1",
            goods=["VIP x1", "Poster x1"],
            received_at="2026-03-29 10:30:00",
        )
        view_state = build_buyer_event_view_state(order, ["VIP"], search_blocked=False)
        current_buyer_order: dict[str, Order | None] = {"value": None}
        refresh_calls: list[bool] = []

        buyer_name_text = _FakeControl()
        buyer_phone_text = _FakeControl()
        buyer_seat_text = _FakeControl()
        buyer_goods_text = _FakeControl()
        buyer_ticket_text = _FakeControl()
        buyer_received_text = _FakeControl()
        buyer_detail_col = _FakeControl()
        buyer_empty_hint = _FakeControl()
        buyer_empty_hint.visible = True

        apply_buyer_event_dashboard_state(
            view_state,
            current_buyer_order=current_buyer_order,
            buyer_name_text=buyer_name_text,
            buyer_phone_text=buyer_phone_text,
            buyer_seat_text=buyer_seat_text,
            buyer_goods_text=buyer_goods_text,
            buyer_ticket_text=buyer_ticket_text,
            buyer_received_text=buyer_received_text,
            buyer_detail_col=buyer_detail_col,
            buyer_empty_hint=buyer_empty_hint,
            refresh_print_controls=lambda *, search_blocked: refresh_calls.append(search_blocked),
        )

        self.assertIs(current_buyer_order["value"], order)
        self.assertEqual(buyer_name_text.value, "ì£¼ë¬¸?ëª…: Kim")
        self.assertEqual(buyer_goods_text.value, "?í’ˆ: Poster x1")
        self.assertTrue(buyer_ticket_text.visible)
        self.assertTrue(buyer_received_text.visible)
        self.assertTrue(buyer_detail_col.visible)
        self.assertFalse(buyer_empty_hint.visible)
        self.assertEqual(refresh_calls, [False])

    def test_apply_buyer_event_dashboard_state_forwards_blocked_state_to_print_controls(self) -> None:
        try:
            from views.dashboard_flet_view import apply_buyer_event_dashboard_state, build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.visible = True

        order = Order(
            order_number="B-200",
            name="Lee",
            phone="010-1111-2222",
            seat="B2",
            goods=["General x1"],
            received_at="",
        )
        view_state = build_buyer_event_view_state(order, ["VIP"], search_blocked=True)
        current_buyer_order: dict[str, Order | None] = {"value": None}
        refresh_calls: list[bool] = []

        buyer_name_text = _FakeControl()
        buyer_phone_text = _FakeControl()
        buyer_seat_text = _FakeControl()
        buyer_goods_text = _FakeControl()
        buyer_ticket_text = _FakeControl()
        buyer_received_text = _FakeControl()
        buyer_detail_col = _FakeControl()
        buyer_empty_hint = _FakeControl()

        apply_buyer_event_dashboard_state(
            view_state,
            current_buyer_order=current_buyer_order,
            buyer_name_text=buyer_name_text,
            buyer_phone_text=buyer_phone_text,
            buyer_seat_text=buyer_seat_text,
            buyer_goods_text=buyer_goods_text,
            buyer_ticket_text=buyer_ticket_text,
            buyer_received_text=buyer_received_text,
            buyer_detail_col=buyer_detail_col,
            buyer_empty_hint=buyer_empty_hint,
            refresh_print_controls=lambda *, search_blocked: refresh_calls.append(search_blocked),
        )

        self.assertIs(current_buyer_order["value"], order)
        self.assertEqual(buyer_ticket_text.value, "")
        self.assertFalse(buyer_ticket_text.visible)
        self.assertFalse(buyer_received_text.visible)
        self.assertEqual(refresh_calls, [True])

    def test_dashboard_runtime_manager_disables_tk_order_window(self) -> None:
        try:
            from views.dashboard_flet_view import create_dashboard_runtime_manager
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        manager = create_dashboard_runtime_manager()
        app_factory = manager._app_factory
        self.assertEqual(app_factory.keywords["show_order_window"], False)

    def test_order_refresh_policy_only_refreshes_ticket_tab_outside_transient_states(self) -> None:
        try:
            from views.dashboard_flet_view import should_auto_refresh_order_views
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertTrue(should_auto_refresh_order_views("ticket", "RUNNING"))
        self.assertTrue(should_auto_refresh_order_views("ticket", "ERROR"))
        self.assertTrue(should_auto_refresh_order_views("ticket", "STARTING"))
        self.assertTrue(should_auto_refresh_order_views("ticket", "STOPPING"))
        self.assertFalse(should_auto_refresh_order_views("receipt", "RUNNING"))

    def test_preserved_dropdown_selection_is_kept_only_when_order_still_exists(self) -> None:
        try:
            from views.dashboard_flet_view import resolve_preserved_order_selection
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        available = {"A-100", "B-200"}
        self.assertEqual(resolve_preserved_order_selection("A-100", available), "A-100")
        self.assertIsNone(resolve_preserved_order_selection("C-300", available))
        self.assertIsNone(resolve_preserved_order_selection(None, available))

    def test_file_timestamp_helper_only_flags_real_changes_after_initial_snapshot(self) -> None:
        try:
            from views.dashboard_flet_view import has_file_timestamp_changed
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertFalse(has_file_timestamp_changed(None, 100))
        self.assertFalse(has_file_timestamp_changed(100, None))
        self.assertFalse(has_file_timestamp_changed(100, 100))
        self.assertTrue(has_file_timestamp_changed(100, 101))

    def test_order_search_signature_changes_when_received_status_changes(self) -> None:
        try:
            from views.dashboard_flet_view import build_order_search_signature
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        before = [
            Order(
                order_number="A-100",
                name="?ê¸¸??,
                phone="010-0000-0000",
                seat="A1",
                goods=["?ŒìŠ¤???°ì¼“ x1"],
                received_at="",
            )
        ]
        after = [
            Order(
                order_number="A-100",
                name="?ê¸¸??,
                phone="010-0000-0000",
                seat="A1",
                goods=["?ŒìŠ¤???°ì¼“ x1"],
                received_at="2026-03-09 13:00:00",
            )
        ]

        self.assertNotEqual(
            build_order_search_signature("", "?„ì²´", ["?ŒìŠ¤???°ì¼“"], before),
            build_order_search_signature("", "?„ì²´", ["?ŒìŠ¤???°ì¼“"], after),
        )

    def test_split_order_goods_keeps_ticket_suffix_items_consistent(self) -> None:
        try:
            from views.dashboard_flet_view import split_order_goods
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        general_goods, ticket_goods = split_order_goods(
            ["?ŒìŠ¤???°ì¼“ x2", "?¼ë°˜ êµ¿ì¦ˆ x1"],
            ["?ŒìŠ¤???°ì¼“"],
        )

        self.assertEqual(general_goods, ["?¼ë°˜ êµ¿ì¦ˆ x1"])
        self.assertEqual(ticket_goods, ["?ŒìŠ¤???°ì¼“ x2"])

    def test_split_order_goods_preserves_contains_matching_for_ticket_variants(self) -> None:
        try:
            from views.dashboard_flet_view import split_order_goods
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        general_goods, ticket_goods = split_order_goods(
            ["VIP ?…ìž¥ê¶??±ì¸) x1", "?¬í† ì¹´ë“œ x1"],
            ["VIP ?…ìž¥ê¶?],
        )

        self.assertEqual(general_goods, ["?¬í† ì¹´ë“œ x1"])
        self.assertEqual(ticket_goods, ["VIP ?…ìž¥ê¶??±ì¸) x1"])

    def test_split_order_goods_ignores_empty_entries_and_matches_case_insensitively(self) -> None:
        try:
            from views.dashboard_flet_view import split_order_goods
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        general_goods, ticket_goods = split_order_goods(
            ["", " vip pass x1 ", None, "?¬í† ì¹´ë“œ x1"],
            ["VIP PASS"],
        )

        self.assertEqual(general_goods, ["?¬í† ì¹´ë“œ x1"])
        self.assertEqual(ticket_goods, ["vip pass x1"])

    def test_buyer_panel_state_formats_ticket_and_received_sections(self) -> None:
        try:
            from views.dashboard_flet_view import build_buyer_panel_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="A-100",
            name="?ê¸¸??,
            phone="010-0000-0000",
            seat="A1",
            goods=["VIP ?…ìž¥ê¶?x1", "?¬í† ì¹´ë“œ x1"],
            received_at="2026-03-29 10:30:00",
        )

        panel_state = build_buyer_panel_state(order, ["VIP ?…ìž¥ê¶?])

        self.assertEqual(panel_state.name_text, "ì£¼ë¬¸?ëª…: ?ê¸¸??)
        self.assertEqual(panel_state.phone_text, "?°ë½ì²? 010-0000-0000")
        self.assertEqual(panel_state.seat_text, "ì¢Œì„ë²ˆí˜¸: A1")
        self.assertEqual(panel_state.goods_text, "?í’ˆ: ?¬í† ì¹´ë“œ x1")
        self.assertEqual(panel_state.ticket_text, "?°ì¼“: VIP ?…ìž¥ê¶?x1")
        self.assertTrue(panel_state.ticket_visible)
        self.assertEqual(panel_state.received_text, "?˜ë ¹?„ë£Œ: 2026-03-29 10:30:00")
        self.assertTrue(panel_state.received_visible)

    def test_buyer_panel_state_hides_optional_sections_when_empty(self) -> None:
        try:
            from views.dashboard_flet_view import build_buyer_panel_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="A-200",
            name="ê¹€ë¯¼ìˆ˜",
            phone="010-1111-2222",
            seat="B2",
            goods=["?¼ë°˜ êµ¿ì¦ˆ x1"],
            received_at="",
        )

        panel_state = build_buyer_panel_state(order, ["VIP ?…ìž¥ê¶?])

        self.assertEqual(panel_state.goods_text, "?í’ˆ: ?¼ë°˜ êµ¿ì¦ˆ x1")
        self.assertEqual(panel_state.ticket_text, "")
        self.assertFalse(panel_state.ticket_visible)
        self.assertEqual(panel_state.received_text, "")
        self.assertFalse(panel_state.received_visible)

    def test_build_buyer_goods_card_controls_separates_name_and_quantity_badge(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_buyer_goods_card_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        controls = build_buyer_goods_card_controls(("?ŒìŠ¤???°ì¼“2 x4",))

        self.assertEqual(len(controls), 1)
        card = controls[0]
        self.assertIsInstance(card, ft.Container)

        row = card.content
        self.assertIsInstance(row, ft.Row)
        title_column = row.controls[1]
        quantity_badge = row.controls[2]

        self.assertEqual(title_column.controls[0].value, "?ŒìŠ¤???°ì¼“2")
        self.assertTrue(quantity_badge.visible)
        self.assertEqual(quantity_badge.content.value, "4ê°?)

    def test_build_buyer_goods_card_controls_hides_quantity_badge_when_missing(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_buyer_goods_card_controls
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        controls = build_buyer_goods_card_controls(("?¬í† ì¹´ë“œ",))

        self.assertEqual(len(controls), 1)
        card = controls[0]
        self.assertIsInstance(card, ft.Container)

        row = card.content
        quantity_badge = row.controls[2]
        self.assertFalse(quantity_badge.visible)

    def test_search_result_row_state_formats_ticket_goods_received_text_and_even_row_color(self) -> None:
        try:
            from views.dashboard_flet_view import build_search_result_row_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="A-100",
            name="?ê¸¸??,
            phone="010-0000-0000",
            seat="A1",
            goods=["VIP ?…ìž¥ê¶?x1", "?¬í† ì¹´ë“œ x1"],
            received_at="2026-03-29 10:30:00",
        )

        row_state = build_search_result_row_state(order, ["VIP ?…ìž¥ê¶?], 0)

        self.assertEqual(row_state.goods_text, "?¬í† ì¹´ë“œ x1")
        self.assertEqual(row_state.ticket_text, "VIP ?…ìž¥ê¶?x1")
        self.assertEqual(row_state.received_text, "2026-03-29 10:30:00")
        self.assertEqual(row_state.row_bg, "#FFFFFF")

    def test_search_result_row_state_uses_defaults_for_empty_ticket_and_odd_row(self) -> None:
        try:
            from views.dashboard_flet_view import build_search_result_row_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="B-200",
            name="ê¹€ë¯¼ìˆ˜",
            phone="010-1111-2222",
            seat="B2",
            goods=["?¼ë°˜ êµ¿ì¦ˆ x1"],
            received_at="",
        )

        row_state = build_search_result_row_state(order, ["VIP ?…ìž¥ê¶?], 1)

        self.assertEqual(row_state.goods_text, "?¼ë°˜ êµ¿ì¦ˆ x1")
        self.assertEqual(row_state.ticket_text, "-")
        self.assertEqual(row_state.received_text, "-")
        self.assertEqual(row_state.row_bg, "#FAFAFA")

    def test_search_result_row_state_marks_goods_and_ticket_columns_for_refresh_highlight(self) -> None:
        try:
            from views.dashboard_flet_view import build_search_result_row_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="C-300",
            name="ë°•í•˜??,
            phone="010-2222-3333",
            seat="C3",
            goods=["VIP ?…ìž¥ê¶?x1", "?¬í† ì¹´ë“œ x2"],
            received_at="",
        )

        row_state = build_search_result_row_state(
            order,
            ["VIP ?…ìž¥ê¶?],
            0,
            highlight_ticket_split=True,
        )

        self.assertTrue(row_state.goods_highlight)
        self.assertTrue(row_state.ticket_highlight)

    def test_build_search_result_rows_renders_goods_and_ticket_as_persistent_chips(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import SearchResultRowState, build_search_result_rows
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        rows = build_search_result_rows((
            SearchResultRowState(
                order_number="A-100",
                name="?ê¸¸??,
                phone="010-0000-0000",
                seat="A1",
                goods_text="?¬í† ì¹´ë“œ x1",
                goods_items=("?¬í† ì¹´ë“œ x1",),
                ticket_text="VIP ?…ìž¥ê¶?x1",
                ticket_items=("VIP ?…ìž¥ê¶?x1",),
                received_text="-",
                row_bg="#FFFFFF",
            ),
        ))

        row = rows[0]
        data_row = row.content
        goods_cell = data_row.controls[4]
        ticket_cell = data_row.controls[5]

        self.assertIsInstance(goods_cell.content, ft.Column)
        self.assertIsInstance(ticket_cell.content, ft.Column)
        self.assertEqual(goods_cell.content.controls[0].content.value, "?¬í† ì¹´ë“œ x1")
        self.assertEqual(ticket_cell.content.controls[0].content.value, "VIP ?…ìž¥ê¶?x1")

    def test_order_search_view_state_applies_unreceived_filter_and_preserves_selection(self) -> None:
        try:
            from views.dashboard_flet_view import build_order_search_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        orders = [
            Order(
                order_number="A-100",
                name="?ê¸¸??,
                phone="010-0000-0000",
                seat="A1",
                goods=["VIP ?…ìž¥ê¶?x1"],
                received_at="2026-03-29 10:30:00",
            ),
            Order(
                order_number="B-200",
                name="ê¹€ë¯¼ìˆ˜",
                phone="010-1111-2222",
                seat="B2",
                goods=["?¼ë°˜ êµ¿ì¦ˆ x1"],
                received_at="",
            ),
        ]

        view_state = build_order_search_view_state(
            "",
            "ë¯¸ìˆ˜??,
            orders,
            ["VIP ?…ìž¥ê¶?],
            "B-200",
        )

        self.assertEqual(view_state.filter_count_text, "1 / 2ëª?)
        self.assertEqual([order.order_number for order in view_state.filtered_orders], ["B-200"])
        self.assertEqual(view_state.dropdown_order_numbers, ("B-200",))
        self.assertEqual(view_state.preserved_order_value, "B-200")
        self.assertEqual(view_state.row_states[0].received_text, "-")
        self.assertFalse(view_state.dropdown_disabled)
        self.assertFalse(view_state.search_blocked)

    def test_order_search_view_state_blocks_dropdown_when_search_error_exists(self) -> None:
        try:
            from views.dashboard_flet_view import build_order_search_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        orders = [
            Order(
                order_number="A-100",
                name="?ê¸¸??,
                phone="010-0000-0000",
                seat="A1",
                goods=["VIP ?…ìž¥ê¶?x1"],
                received_at="",
            )
        ]

        view_state = build_order_search_view_state(
            "",
            "?„ì²´",
            orders,
            ["VIP ?…ìž¥ê¶?],
            "A-100",
            error_message="?Œì¼ ? ê¸ˆ",
        )

        self.assertTrue(view_state.dropdown_disabled)
        self.assertTrue(view_state.search_blocked)
        self.assertEqual(view_state.feedback_color, "#D14343")
        self.assertIn("?Œì¼ ? ê¸ˆ", view_state.feedback_message)

    def test_order_search_view_state_returns_empty_feedback_for_no_results_without_error(self) -> None:
        try:
            from views.dashboard_flet_view import build_order_search_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        view_state = build_order_search_view_state(
            "",
            "?„ì²´",
            [],
            ["VIP ?…ìž¥ê¶?],
            None,
        )

        self.assertEqual(view_state.filter_count_text, "0ëª?)
        self.assertEqual(view_state.dropdown_order_numbers, ())
        self.assertEqual(view_state.row_states, ())
        self.assertIsNone(view_state.preserved_order_value)
        self.assertEqual(view_state.feedback_message, "ê²€??ê²°ê³¼ê°€ ?†ìŠµ?ˆë‹¤.")
        self.assertEqual(view_state.feedback_color, "#777777")

    def test_apply_order_search_dashboard_state_updates_dropdown_rows_and_feedback(self) -> None:
        try:
            from views.dashboard_flet_view import apply_order_search_dashboard_state, build_order_search_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.disabled = False
                self.visible = False
                self.color = ""
                self.options = []
                self.controls = []

        orders = [
            Order(
                order_number="A-100",
                name="Kim",
                phone="010-0000-0000",
                seat="A1",
                goods=["VIP x1"],
                received_at="",
            ),
            Order(
                order_number="B-200",
                name="Lee",
                phone="010-1111-2222",
                seat="B2",
                goods=["Poster x1"],
                received_at="2026-03-29 10:30:00",
            ),
        ]
        search_view_state = build_order_search_view_state(
            "",
            "?„ì²´",
            orders,
            ["VIP"],
            "A-100",
        )
        orders_map: dict[str, Order] = {}
        filter_count_text = _FakeControl()
        search_blocked_state = {"value": False}
        refresh_calls: list[bool] = []
        search_result_list = _FakeControl()
        search_feedback_text = _FakeControl()
        last_search_signature = {"value": None}

        apply_order_search_dashboard_state(
            search_view_state,
            orders_map=orders_map,
            filter_count_text=filter_count_text,
            search_blocked_state=search_blocked_state,
            refresh_print_controls=lambda *, search_blocked=False: refresh_calls.append(search_blocked),
            search_result_list=search_result_list,
            search_feedback_text=search_feedback_text,
            last_search_signature=last_search_signature,
        )

        self.assertEqual(filter_count_text.value, "2ëª?)
        self.assertEqual(list(orders_map), ["A-100", "B-200"])
        self.assertFalse(search_blocked_state["value"])
        self.assertEqual(refresh_calls, [False])
        self.assertEqual(len(search_result_list.controls), 2)
        self.assertEqual(search_feedback_text.value, "")
        self.assertFalse(search_feedback_text.visible)
        self.assertEqual(last_search_signature["value"], search_view_state.search_signature)

    def test_apply_order_search_dashboard_state_blocks_print_when_search_is_blocked(self) -> None:
        try:
            from views.dashboard_flet_view import apply_order_search_dashboard_state, build_order_search_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.disabled = False
                self.visible = False
                self.color = ""
                self.options = []
                self.controls = []

        orders = [
            Order(
                order_number="A-100",
                name="Kim",
                phone="010-0000-0000",
                seat="A1",
                goods=["VIP x1"],
                received_at="",
            )
        ]
        search_view_state = build_order_search_view_state(
            "",
            "?„ì²´",
            orders,
            ["VIP"],
            "A-100",
            error_message="file locked",
        )
        orders_map: dict[str, Order] = {}
        filter_count_text = _FakeControl()
        search_blocked_state = {"value": False}
        refresh_calls: list[bool] = []
        search_result_list = _FakeControl()
        search_feedback_text = _FakeControl()
        last_search_signature = {"value": None}

        apply_order_search_dashboard_state(
            search_view_state,
            orders_map=orders_map,
            filter_count_text=filter_count_text,
            search_blocked_state=search_blocked_state,
            refresh_print_controls=lambda *, search_blocked=False: refresh_calls.append(search_blocked),
            search_result_list=search_result_list,
            search_feedback_text=search_feedback_text,
            last_search_signature=last_search_signature,
        )

        self.assertTrue(search_blocked_state["value"])
        self.assertEqual(refresh_calls, [True])
        self.assertTrue(search_feedback_text.visible)
        self.assertEqual(search_feedback_text.color, "#D14343")
        self.assertIn("file locked", search_feedback_text.value)

    def test_build_order_search_panel_keeps_toolbar_controls_and_search_action(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_order_search_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _on_search(_event=None) -> None:
            return None

        search_field = ft.TextField()
        filter_dropdown = ft.Dropdown()
        filter_count_text = ft.Text("count")
        btn_import_data = ft.OutlinedButton("data ?Œì¼ ê°€?¸ì˜¤ê¸?)
        btn_refresh = ft.IconButton(icon=getattr(ft, "Icons", ft.icons).REFRESH_ROUNDED)
        search_feedback_text = ft.Text("feedback")
        search_result_header = ft.Container()
        search_result_list = ft.ListView()

        panel = build_order_search_panel(
            search_field=search_field,
            filter_dropdown=filter_dropdown,
            filter_count_text=filter_count_text,
            on_search=_on_search,
            btn_import_data=btn_import_data,
            btn_refresh=btn_refresh,
            search_feedback_text=search_feedback_text,
            search_result_header=search_result_header,
            search_result_list=search_result_list,
        )

        column = panel.content
        toolbar = column.controls[1]
        search_button = toolbar.controls[3]

        self.assertIs(toolbar.controls[0], search_field)
        self.assertIs(toolbar.controls[1], filter_dropdown)
        self.assertIs(toolbar.controls[2], filter_count_text)
        self.assertEqual(search_button.text, "ê²€??)
        self.assertIs(search_button.on_click, _on_search)
        self.assertIs(toolbar.controls[4], btn_import_data)
        self.assertIs(toolbar.controls[5], btn_refresh)

    def test_build_order_search_panel_keeps_feedback_and_results_controls(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_order_search_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        search_feedback_text = ft.Text("feedback")
        search_result_header = ft.Container()
        search_result_list = ft.ListView()
        panel = build_order_search_panel(
            search_field=ft.TextField(),
            filter_dropdown=ft.Dropdown(),
            filter_count_text=ft.Text("count"),
            on_search=lambda _event=None: None,
            btn_import_data=ft.OutlinedButton("data ?Œì¼ ê°€?¸ì˜¤ê¸?),
            btn_refresh=ft.IconButton(icon=getattr(ft, "Icons", ft.icons).REFRESH_ROUNDED),
            search_feedback_text=search_feedback_text,
            search_result_header=search_result_header,
            search_result_list=search_result_list,
        )

        column = panel.content
        results_container = column.controls[3]
        results_column = results_container.content

        self.assertIs(column.controls[2], search_feedback_text)
        self.assertIs(results_column.controls[0], search_result_header)
        self.assertIs(results_column.controls[1], search_result_list)

    def test_build_dashboard_snack_bar_uses_readable_success_colors(self) -> None:
        try:
            from views.dashboard_flet_view import build_dashboard_snack_bar
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        snack = build_dashboard_snack_bar("?„ë£Œ", success=True)

        self.assertEqual(snack.bgcolor, "#D8F4E3")
        self.assertEqual(snack.content.value, "?„ë£Œ")
        self.assertEqual(snack.content.color, "#163221")

    def test_build_dashboard_snack_bar_uses_readable_error_colors(self) -> None:
        try:
            from views.dashboard_flet_view import build_dashboard_snack_bar
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        snack = build_dashboard_snack_bar("?¤íŒ¨", success=False)

        self.assertEqual(snack.bgcolor, "#FFD6D6")
        self.assertEqual(snack.content.value, "?¤íŒ¨")
        self.assertEqual(snack.content.color, "#7A1F1F")

    def test_build_ticket_dashboard_panel_keeps_section_order_and_scroll(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_ticket_dashboard_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        top_controls_col = ft.Column()
        buyer_info_panel = ft.Container()
        camera_container = ft.Container()
        special_rule_progress_panel = ft.Container()
        order_search_panel = ft.Container()

        panel = build_ticket_dashboard_panel(
            top_controls_col=top_controls_col,
            buyer_info_panel=buyer_info_panel,
            camera_container=camera_container,
            special_rule_progress_panel=special_rule_progress_panel,
            order_search_panel=order_search_panel,
        )

        column = panel.content
        top_section = column.controls[0]
        preview_row = column.controls[1]

        self.assertIs(top_section.content, top_controls_col)
        self.assertIs(preview_row.controls[0], buyer_info_panel)
        self.assertIs(preview_row.controls[1], camera_container)
        self.assertIs(column.controls[2], special_rule_progress_panel)
        self.assertIs(column.controls[3], order_search_panel)
        self.assertEqual(column.scroll, ft.ScrollMode.AUTO)
        self.assertTrue(panel.expand)

    def test_build_ticket_dashboard_panel_keeps_spacing_and_padding(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_ticket_dashboard_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        panel = build_ticket_dashboard_panel(
            top_controls_col=ft.Column(),
            buyer_info_panel=ft.Container(),
            camera_container=ft.Container(),
            special_rule_progress_panel=ft.Container(),
            order_search_panel=ft.Container(),
        )

        self.assertEqual(panel.padding.left, 8)
        self.assertEqual(panel.padding.top, 8)
        self.assertEqual(panel.content.spacing, 8)

    def test_build_camera_focus_panel_keeps_controls_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_camera_focus_panel
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _collect_strings(control) -> set[str]:
            values: set[str] = set()

            def _walk(node) -> None:
                if node is None:
                    return
                for attr in ("value", "label", "text"):
                    text = getattr(node, attr, None)
                    if isinstance(text, str) and text:
                        values.add(text)
                content = getattr(node, "content", None)
                if content is not None:
                    _walk(content)
                controls = getattr(node, "controls", None)
                if isinstance(controls, list):
                    for child in controls:
                        _walk(child)

            _walk(control)
            return values

        focus_mode_dropdown = ft.Dropdown(label="focus")
        manual_focus_value_field = ft.TextField(label="manual")

        panel = build_camera_focus_panel(
            on_close=lambda _event=None: None,
            focus_mode_dropdown=focus_mode_dropdown,
            manual_focus_value_field=manual_focus_value_field,
        )

        strings = _collect_strings(panel)
        self.assertIn("ê³ ê¸‰ ì¹´ë©”???¤ì •", strings)
        self.assertNotIn("ì´ˆì  ?¤ì •???€?¥í•˜ë©??„ìž¬ ?°í??„ì—??ë°”ë¡œ ?ìš©?©ë‹ˆ??", strings)
        self.assertIs(panel.content.controls[2], focus_mode_dropdown)
        self.assertIs(panel.content.controls[3], manual_focus_value_field)

    def test_build_camera_focus_drawer_reflects_overlay_open_state(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_camera_focus_drawer
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        closed_drawer = build_camera_focus_drawer(
            is_open=False,
            panel_content=ft.Container(),
        )
        open_drawer = build_camera_focus_drawer(
            is_open=True,
            panel_content=ft.Container(),
        )

        self.assertEqual(closed_drawer.width, 336)
        self.assertEqual(closed_drawer.opacity, 0.0)
        self.assertEqual((closed_drawer.offset.x, closed_drawer.offset.y), (1.08, 0))
        self.assertEqual(closed_drawer.right, 24)
        self.assertEqual(closed_drawer.top, 148)
        self.assertEqual(open_drawer.opacity, 1.0)
        self.assertEqual((open_drawer.offset.x, open_drawer.offset.y), (0, 0))
        self.assertIsNotNone(getattr(open_drawer, "animate_offset", None))
        self.assertIsNotNone(getattr(open_drawer, "animate_opacity", None))

    def test_build_camera_focus_side_handle_keeps_open_action_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_camera_focus_side_handle
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _on_open(_event=None) -> None:
            return None

        def _on_hover(_event=None) -> None:
            return None

        handle = build_camera_focus_side_handle(on_open=_on_open, on_hover=_on_hover)
        content = handle.content

        self.assertIs(handle.on_click, _on_open)
        self.assertEqual(handle.right, 0)
        self.assertEqual(handle.top, 332)
        self.assertEqual(handle.width, 28)
        self.assertEqual(handle.tooltip, "?¤ì • ?´ê¸°")
        self.assertEqual(handle.bgcolor, "#DCE4EC")
        self.assertIs(handle.on_hover, _on_hover)
        self.assertIsInstance(content.controls[0].content, ft.Icon)
        self.assertEqual(content.controls[2].value, "??)
        self.assertEqual(content.controls[3].value, "??)

    def test_build_dashboard_overlay_host_keeps_content_drawer_and_handle_order(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_dashboard_overlay_host
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        content_host = ft.Container()
        overlay_drawer = ft.Container()
        side_handle = ft.Container()

        overlay_host = build_dashboard_overlay_host(
            content_host=content_host,
            overlay_drawer=overlay_drawer,
            side_handle=side_handle,
        )

        self.assertTrue(overlay_host.expand)
        self.assertEqual(overlay_host.controls[0], content_host)
        overlay_group = overlay_host.controls[1]
        self.assertEqual(overlay_group.controls[0], overlay_drawer)
        self.assertEqual(overlay_group.controls[1], side_handle)

    def test_build_dashboard_sidebar_keeps_tabs_and_settings_action(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_dashboard_sidebar
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _on_open_settings(_event=None) -> None:
            return None

        btn_ticket_tab = ft.TextButton("?°ì¼“ ?•ì¸")
        btn_receipt_tab = ft.TextButton("?ìˆ˜ì¦??‘ì‹")

        sidebar = build_dashboard_sidebar(
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
            on_open_settings=_on_open_settings,
        )

        column = sidebar.content
        settings_button = column.controls[5]

        self.assertIs(column.controls[2], btn_ticket_tab)
        self.assertIs(column.controls[3], btn_receipt_tab)
        self.assertEqual(settings_button.text, "Settings")
        self.assertIs(settings_button.on_click, _on_open_settings)

    def test_build_dashboard_sidebar_keeps_width_and_footer(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_dashboard_sidebar
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        sidebar = build_dashboard_sidebar(
            btn_ticket_tab=ft.TextButton("?°ì¼“ ?•ì¸"),
            btn_receipt_tab=ft.TextButton("?ìˆ˜ì¦??‘ì‹"),
            on_open_settings=lambda _event=None: None,
        )

        column = sidebar.content
        footer_text = column.controls[-1].content

        self.assertEqual(sidebar.width, 244)
        self.assertEqual(sidebar.bgcolor, "#F5F6F8")
        self.assertEqual(footer_text.value, "v1 Control Center")

    def test_build_settings_dialog_keeps_title_and_settings_panel(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_settings_dialog
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        app_settings_panel = ft.Container()

        dialog = build_settings_dialog(
            app_settings_panel=app_settings_panel,
            on_close=lambda _event=None: None,
        )

        self.assertEqual(dialog.title.value, "?¤ì •")
        self.assertEqual(dialog.content.width, 1120)
        self.assertEqual(dialog.content.height, 760)
        self.assertIs(dialog.content.content, app_settings_panel)

    def test_build_settings_dialog_wires_close_action_and_alignment(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_settings_dialog
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _on_close(_event=None) -> None:
            return None

        dialog = build_settings_dialog(
            app_settings_panel=ft.Container(),
            on_close=_on_close,
        )

        close_button = dialog.actions[0]

        self.assertEqual(close_button.text, "?«ê¸°")
        self.assertIs(close_button.on_click, _on_close)
        self.assertEqual(dialog.actions_alignment, ft.MainAxisAlignment.END)

    def test_build_dashboard_shell_places_sidebar_and_content_host(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_dashboard_shell
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        sidebar = ft.Container()
        content_host = ft.Container()

        shell = build_dashboard_shell(
            sidebar=sidebar,
            content_host=content_host,
        )

        content_container = shell.controls[1]

        self.assertIs(shell.controls[0], sidebar)
        self.assertIs(content_container.content, content_host)
        self.assertTrue(content_container.expand)

    def test_build_dashboard_shell_keeps_spacing_expand_and_background(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_dashboard_shell
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        shell = build_dashboard_shell(
            sidebar=ft.Container(),
            content_host=ft.Container(),
        )

        self.assertEqual(shell.spacing, 0)
        self.assertTrue(shell.expand)
        self.assertEqual(shell.controls[1].bgcolor, "#ECECEC")

    def test_bootstrap_dashboard_page_wires_initial_tab_runtime_and_close_handler(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import bootstrap_dashboard_page
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        added_controls: list[ft.Control] = []
        destroyed: list[bool] = []
        call_log: list[tuple[object, ...]] = []
        started_threads: list[tuple[object, ...]] = []
        page = SimpleNamespace(
            add=lambda control: added_controls.append(control),
            window=SimpleNamespace(
                on_event=None,
                destroy=lambda: destroyed.append(True),
            ),
        )
        runtime_manager = SimpleNamespace(
            subscribe=lambda callback: call_log.append(("subscribe", callback)),
            unsubscribe=lambda callback: call_log.append(("unsubscribe", callback)),
            stop=lambda timeout_sec=4.0: call_log.append(("stop", timeout_sec)),
        )
        current_tab = {"value": "ticket"}
        closing_event = threading.Event()

        def _set_tab(tab_key: str, push_update: bool = True) -> None:
            call_log.append(("set_tab", tab_key, push_update))

        def _on_runtime_event(state: str, message: str, timestamp: str) -> None:
            return None

        class _FakeThread:
            def __init__(self, *, target, daemon) -> None:
                self.target = target
                self.daemon = daemon

            def start(self) -> None:
                started_threads.append((self.target, self.daemon))

        btn_relogin = ft.OutlinedButton("?¬ë¡œê·¸ì¸")
        btn_start_stop = ft.ElevatedButton("?€ê¸?)
        btn_ticket_tab = ft.TextButton("?°ì¼“")
        btn_receipt_tab = ft.TextButton("?ìˆ˜ì¦?)

        with patch("views.dashboard_flet_view.threading.Thread", _FakeThread):
            bootstrap_dashboard_page(
                runtime_manager=runtime_manager,
                page=page,
                current_tab=current_tab,
                ticket_panel=ft.Container(),
                receipt_settings_panel=ft.Container(),
                content_host=ft.Container(),
                btn_ticket_tab=btn_ticket_tab,
                btn_receipt_tab=btn_receipt_tab,
                sidebar=ft.Container(),
                btn_relogin=btn_relogin,
                btn_start_stop=btn_start_stop,
                on_start=lambda _event=None: None,
                on_stop=lambda _event=None: None,
                set_tab=_set_tab,
                on_runtime_event=_on_runtime_event,
                watch_excel_changes=lambda: None,
                cancel_scheduled_search_refresh=lambda: call_log.append(("cancel_refresh",)),
                closing_event=closing_event,
            )

        self.assertEqual(call_log[0], ("set_tab", "ticket", True))
        self.assertEqual(call_log[1], ("subscribe", _on_runtime_event))
        self.assertEqual(len(added_controls), 1)
        self.assertTrue(started_threads)
        self.assertEqual(started_threads[0][1], True)
        self.assertEqual(btn_start_stop.text, "?°ì¼“ ?•ì¸ ?œìž‘")
        self.assertTrue(btn_relogin.disabled)
        self.assertIsNotNone(page.window.on_event)

        page.window.on_event(SimpleNamespace(data="close"))

        self.assertTrue(closing_event.is_set())
        self.assertIn(("cancel_refresh",), call_log)
        self.assertIn(("unsubscribe", _on_runtime_event), call_log)
        self.assertIn(("stop", 4.0), call_log)
        self.assertEqual(destroyed, [True])

    def test_watch_refresh_requires_real_file_change(self) -> None:
        try:
            from views.dashboard_flet_view import should_refresh_search_results_from_watch
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertFalse(
            should_refresh_search_results_from_watch(
                workbook_changed=False,
                tab_key="ticket",
                runtime_state="RUNNING",
            )
        )
        self.assertTrue(
            should_refresh_search_results_from_watch(
                workbook_changed=True,
                tab_key="ticket",
                runtime_state="RUNNING",
            )
        )
        self.assertFalse(
            should_refresh_search_results_from_watch(
                workbook_changed=True,
                tab_key="receipt",
                runtime_state="RUNNING",
            )
        )

    def test_process_search_refresh_watch_tick_schedules_refresh_only_when_change_and_policy_allow(self) -> None:
        try:
            from views.dashboard_flet_view import process_search_refresh_watch_tick
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        next_mtime_ns, should_schedule_refresh = process_search_refresh_watch_tick(
            100,
            101,
            tab_key="ticket",
            runtime_state="RUNNING",
        )
        self.assertEqual(next_mtime_ns, 101)
        self.assertTrue(should_schedule_refresh)

        next_mtime_ns, should_schedule_refresh = process_search_refresh_watch_tick(
            100,
            101,
            tab_key="receipt",
            runtime_state="RUNNING",
        )
        self.assertEqual(next_mtime_ns, 101)
        self.assertFalse(should_schedule_refresh)

    def test_process_search_refresh_watch_tick_preserves_previous_mtime_when_file_missing(self) -> None:
        try:
            from views.dashboard_flet_view import process_search_refresh_watch_tick
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        next_mtime_ns, should_schedule_refresh = process_search_refresh_watch_tick(
            100,
            None,
            tab_key="ticket",
            runtime_state="RUNNING",
        )

        self.assertEqual(next_mtime_ns, 100)
        self.assertFalse(should_schedule_refresh)

    def test_filter_count_text_uses_total_for_unreceived_view(self) -> None:
        try:
            from views.dashboard_flet_view import format_filter_count_text
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(format_filter_count_text("?„ì²´", 10, 4), "10ëª?)
        self.assertEqual(format_filter_count_text("?˜ë ¹?„ë£Œ", 10, 4), "4 / 10ëª?)
        self.assertEqual(format_filter_count_text("ë¯¸ìˆ˜??, 10, 4), "6 / 10ëª?)

    def test_search_feedback_distinguishes_error_and_empty_states(self) -> None:
        try:
            from views.dashboard_flet_view import resolve_order_search_feedback
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertEqual(
            resolve_order_search_feedback(0, ""),
            ("ê²€??ê²°ê³¼ê°€ ?†ìŠµ?ˆë‹¤.", "#777777"),
        )
        self.assertEqual(
            resolve_order_search_feedback(3, "?Œì¼ ? ê¸ˆ"),
            (
                "ì£¼ë¬¸ ê²€???¤íŒ¨: ?Œì¼ ? ê¸ˆ. ?‘ì? ?Œì¼ ?íƒœë¥??•ì¸?????¤ì‹œ ê²€?‰í•´ì£¼ì„¸??",
                "#D14343",
            ),
        )
        self.assertEqual(resolve_order_search_feedback(3, ""), ("", "#777777"))

    def test_print_button_disabled_state_respects_target_progress_and_block(self) -> None:
        try:
            from views.dashboard_flet_view import compute_print_button_disabled
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        self.assertTrue(
            compute_print_button_disabled(
                has_target=False,
                print_in_progress=False,
                blocked=False,
            )
        )
        self.assertTrue(
            compute_print_button_disabled(
                has_target=True,
                print_in_progress=True,
                blocked=False,
            )
        )
        self.assertTrue(
            compute_print_button_disabled(
                has_target=True,
                print_in_progress=False,
                blocked=True,
            )
        )
        self.assertFalse(
            compute_print_button_disabled(
                has_target=True,
                print_in_progress=False,
                blocked=False,
            )
        )

    def test_apply_camera_frame_state_sets_frame_and_visibility_together(self) -> None:
        try:
            from views.dashboard_flet_view import apply_camera_frame_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeCameraView:
            def __init__(self) -> None:
                self.src_base64 = ""
                self.visible = False

        camera_view = _FakeCameraView()
        apply_camera_frame_state(camera_view, "abc123")

        self.assertEqual(camera_view.src_base64, "abc123")
        self.assertTrue(camera_view.visible)

    def test_dispatch_camera_frame_update_applies_frame_and_refreshes_camera_view(self) -> None:
        try:
            from views.dashboard_flet_view import dispatch_camera_frame_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeCameraView:
            def __init__(self) -> None:
                self.src_base64 = ""
                self.visible = False
                self.update_calls = 0

            def update(self) -> None:
                self.update_calls += 1

        class _FakePage:
            def __init__(self) -> None:
                self.call_count = 0

            def call_from_thread(self, callback) -> None:
                self.call_count += 1
                callback()

        page = _FakePage()
        camera_view = _FakeCameraView()

        dispatch_camera_frame_update(page, camera_view, "frame123")

        self.assertEqual(page.call_count, 1)
        self.assertEqual(camera_view.src_base64, "frame123")
        self.assertTrue(camera_view.visible)
        self.assertEqual(camera_view.update_calls, 1)

    def test_dispatch_camera_frame_update_skips_when_closing_signal_is_set(self) -> None:
        try:
            from views.dashboard_flet_view import dispatch_camera_frame_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeCameraView:
            def __init__(self) -> None:
                self.src_base64 = ""
                self.visible = False
                self.update_calls = 0

            def update(self) -> None:
                self.update_calls += 1

        class _FakePage:
            def __init__(self) -> None:
                self.call_count = 0

            def call_from_thread(self, callback) -> None:
                self.call_count += 1
                callback()

        closing_event = threading.Event()
        closing_event.set()
        page = _FakePage()
        camera_view = _FakeCameraView()

        dispatch_camera_frame_update(page, camera_view, "frame123", closing_event)

        self.assertEqual(page.call_count, 0)
        self.assertEqual(camera_view.src_base64, "")
        self.assertFalse(camera_view.visible)
        self.assertEqual(camera_view.update_calls, 0)

    def test_dispatch_runtime_event_update_applies_status_refresh_and_search_refresh(self) -> None:
        try:
            from views.dashboard_flet_view import build_runtime_event_view_state, dispatch_runtime_event_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakePage:
            def __init__(self) -> None:
                self.call_count = 0
                self.update_calls = 0

            def call_from_thread(self, callback) -> None:
                self.call_count += 1
                callback()

            def update(self) -> None:
                self.update_calls += 1

        page = _FakePage()
        status_updates = []
        search_refreshes: list[str] = []
        event_view_state = build_runtime_event_view_state(
            "ticket",
            "RUNNING",
            "?¤ìº” ì¤?,
            "2026-03-29 12:00:00",
        )

        dispatch_runtime_event_update(
            page,
            event_view_state,
            status_updates.append,
            lambda: search_refreshes.append("search"),
        )

        self.assertEqual(page.call_count, 1)
        self.assertEqual(page.update_calls, 1)
        self.assertEqual(len(status_updates), 1)
        self.assertEqual(status_updates[0].state_text, "RUNNING")
        self.assertEqual(search_refreshes, ["search"])

    def test_dispatch_runtime_event_update_skips_search_when_refresh_not_required(self) -> None:
        try:
            from views.dashboard_flet_view import build_runtime_event_view_state, dispatch_runtime_event_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakePage:
            def __init__(self) -> None:
                self.call_count = 0
                self.update_calls = 0

            def call_from_thread(self, callback) -> None:
                self.call_count += 1
                callback()

            def update(self) -> None:
                self.update_calls += 1

        page = _FakePage()
        status_updates = []
        search_refreshes: list[str] = []
        event_view_state = build_runtime_event_view_state(
            "receipt",
            "RUNNING",
            "?¤ìº” ì¤?,
            "2026-03-29 12:00:00",
        )

        dispatch_runtime_event_update(
            page,
            event_view_state,
            status_updates.append,
            lambda: search_refreshes.append("search"),
        )

        self.assertEqual(page.call_count, 1)
        self.assertEqual(page.update_calls, 1)
        self.assertEqual(len(status_updates), 1)
        self.assertEqual(status_updates[0].state_text, "RUNNING")
        self.assertEqual(search_refreshes, [])

    def test_dispatch_runtime_event_dashboard_state_applies_runtime_controls_and_search_refresh(self) -> None:
        try:
            from views.dashboard_flet_view import build_runtime_event_view_state, dispatch_runtime_event_dashboard_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakePage:
            def __init__(self) -> None:
                self.call_count = 0
                self.update_calls = 0

            def call_from_thread(self, callback) -> None:
                self.call_count += 1
                callback()

            def update(self) -> None:
                self.update_calls += 1

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.bgcolor = ""
                self.disabled = True
                self.text = ""
                self.icon = None
                self.style = None
                self.on_click = None

        def _on_start(_event=None) -> None:
            return None

        def _on_stop(_event=None) -> None:
            return None

        page = _FakePage()
        current_state = {"value": "IDLE"}
        state_text = _FakeControl()
        state_badge = _FakeControl()
        runtime_hint_text = _FakeControl()
        last_event_text = _FakeControl()
        btn_relogin = _FakeControl()
        btn_start_stop = _FakeControl()
        search_refreshes: list[str] = []
        event_view_state = build_runtime_event_view_state(
            "ticket",
            "RUNNING",
            "?¤ìº” ì¤?,
            "2026-03-29 12:00:00",
        )

        dispatch_runtime_event_dashboard_state(
            page,
            event_view_state,
            current_state=current_state,
            state_text=state_text,
            state_badge=state_badge,
            runtime_hint_text=runtime_hint_text,
            last_event_text=last_event_text,
            btn_relogin=btn_relogin,
            btn_start_stop=btn_start_stop,
            on_start=_on_start,
            on_stop=_on_stop,
            refresh_search_results=lambda: search_refreshes.append("search"),
        )

        self.assertEqual(page.call_count, 1)
        self.assertEqual(page.update_calls, 1)
        self.assertEqual(current_state["value"], "RUNNING")
        self.assertEqual(state_text.value, "RUNNING")
        self.assertEqual(runtime_hint_text.value, "?¤ìº” ì¤?)
        self.assertEqual(btn_start_stop.text, "ì¤‘ì?")
        self.assertIs(btn_start_stop.on_click, _on_stop)
        self.assertEqual(search_refreshes, ["search"])

    def test_dispatch_runtime_event_dashboard_state_skips_search_when_not_required(self) -> None:
        try:
            from views.dashboard_flet_view import build_runtime_event_view_state, dispatch_runtime_event_dashboard_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakePage:
            def __init__(self) -> None:
                self.call_count = 0
                self.update_calls = 0

            def call_from_thread(self, callback) -> None:
                self.call_count += 1
                callback()

            def update(self) -> None:
                self.update_calls += 1

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.bgcolor = ""
                self.disabled = True
                self.text = ""
                self.icon = None
                self.style = None
                self.on_click = None

        def _on_start(_event=None) -> None:
            return None

        def _on_stop(_event=None) -> None:
            return None

        page = _FakePage()
        current_state = {"value": "IDLE"}
        state_text = _FakeControl()
        state_badge = _FakeControl()
        runtime_hint_text = _FakeControl()
        last_event_text = _FakeControl()
        btn_relogin = _FakeControl()
        btn_start_stop = _FakeControl()
        search_refreshes: list[str] = []
        event_view_state = build_runtime_event_view_state(
            "receipt",
            "RUNNING",
            "?¤ìº” ì¤?,
            "2026-03-29 12:00:00",
        )

        dispatch_runtime_event_dashboard_state(
            page,
            event_view_state,
            current_state=current_state,
            state_text=state_text,
            state_badge=state_badge,
            runtime_hint_text=runtime_hint_text,
            last_event_text=last_event_text,
            btn_relogin=btn_relogin,
            btn_start_stop=btn_start_stop,
            on_start=_on_start,
            on_stop=_on_stop,
            refresh_search_results=lambda: search_refreshes.append("search"),
        )

        self.assertEqual(page.call_count, 1)
        self.assertEqual(page.update_calls, 1)
        self.assertEqual(current_state["value"], "RUNNING")
        self.assertEqual(btn_start_stop.text, "ì¤‘ì?")
        self.assertEqual(search_refreshes, [])

    def test_load_ticket_product_names_returns_loaded_names_or_empty_list(self) -> None:
        try:
            from views.dashboard_flet_view import load_ticket_product_names
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _LoadedStore:
            def load(self):
                return type("Settings", (), {"ticket_product_names": ["VIP", "?¼ë°˜"]})()

        class _FailingStore:
            def load(self):
                raise RuntimeError("settings unavailable")

        self.assertEqual(load_ticket_product_names(_LoadedStore()), ["VIP", "?¼ë°˜"])
        self.assertEqual(load_ticket_product_names(_FailingStore()), [])

    def test_safe_page_update_skips_closed_or_failing_updates(self) -> None:
        try:
            from views.dashboard_flet_view import safe_page_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakePage:
            def __init__(self, *, fail: bool = False) -> None:
                self.fail = fail
                self.update_calls = 0

            def update(self) -> None:
                self.update_calls += 1
                if self.fail:
                    raise RuntimeError("closed")

        closing_event = threading.Event()
        closing_event.set()
        skipped_page = _FakePage()
        self.assertFalse(safe_page_update(skipped_page, closing_event))
        self.assertEqual(skipped_page.update_calls, 0)

        failing_page = _FakePage(fail=True)
        self.assertFalse(safe_page_update(failing_page))
        self.assertEqual(failing_page.update_calls, 1)

    def test_safe_page_update_logs_unexpected_failures_when_not_closing(self) -> None:
        try:
            from views.dashboard_flet_view import safe_page_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakePage:
            def update(self) -> None:
                raise RuntimeError("closed")

        with self.assertLogs("views.dashboard_flet_view", level="WARNING") as captured:
            self.assertFalse(safe_page_update(_FakePage()))

        self.assertTrue(any("?€?œë³´??UI ê°±ì‹  ?¤íŒ¨" in line for line in captured.output))

    def test_safe_page_update_skips_unmounted_controls_without_logging(self) -> None:
        try:
            from views.dashboard_flet_view import safe_page_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _UnmountedControl:
            def __init__(self) -> None:
                self._Control__page = None
                self.update_calls = 0

            def update(self) -> None:
                self.update_calls += 1

        control = _UnmountedControl()
        self.assertFalse(safe_page_update(control))
        self.assertEqual(control.update_calls, 0)

    def test_safe_page_update_allows_root_page_without_parent(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import safe_page_update
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        page = object.__new__(ft.Page)
        page._Control__page = None
        page.update_calls = 0
        page.update = lambda *args, **kwargs: setattr(page, "update_calls", page.update_calls + 1)

        self.assertTrue(safe_page_update(page))
        self.assertEqual(page.update_calls, 1)

    def test_call_page_from_thread_respects_close_signal_and_falls_back_directly(self) -> None:
        try:
            from views.dashboard_flet_view import call_page_from_thread
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        executed: list[str] = []

        class _FakePage:
            def __init__(self, *, fail: bool = False) -> None:
                self.fail = fail

            def call_from_thread(self, callback) -> None:
                if self.fail:
                    raise RuntimeError("thread bridge unavailable")
                callback()

        closing_event = threading.Event()
        closing_event.set()
        call_page_from_thread(_FakePage(), lambda: executed.append("skipped"), closing_event)
        self.assertEqual(executed, [])

        call_page_from_thread(_FakePage(fail=True), lambda: executed.append("fallback"))
        call_page_from_thread(_FakePage(), lambda: executed.append("thread"))
        self.assertEqual(executed, ["fallback", "thread"])

    def test_call_page_from_thread_falls_back_when_bridge_is_missing(self) -> None:
        try:
            from views.dashboard_flet_view import call_page_from_thread
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        executed: list[str] = []

        class _FakePage:
            pass

        call_page_from_thread(_FakePage(), lambda: executed.append("fallback"))

        self.assertEqual(executed, ["fallback"])

    def test_call_page_from_thread_uses_page_loop_when_bridge_is_missing(self) -> None:
        try:
            from views.dashboard_flet_view import call_page_from_thread
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        executed: list[str] = []
        scheduled: list[str] = []

        class _FakeLoop:
            def call_soon_threadsafe(self, callback) -> None:
                scheduled.append("scheduled")
                callback()

        class _FakePage:
            def __init__(self) -> None:
                self._Page__loop = _FakeLoop()

        call_page_from_thread(_FakePage(), lambda: executed.append("loop"))

        self.assertEqual(scheduled, ["scheduled"])
        self.assertEqual(executed, ["loop"])

    def test_call_page_from_thread_uses_public_page_loop_when_bridge_is_missing(self) -> None:
        try:
            from views.dashboard_flet_view import call_page_from_thread
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        executed: list[str] = []
        scheduled: list[str] = []

        class _FakeLoop:
            def call_soon_threadsafe(self, callback) -> None:
                scheduled.append("scheduled")
                callback()

        class _FakePage:
            def __init__(self) -> None:
                self.loop = _FakeLoop()

        call_page_from_thread(_FakePage(), lambda: executed.append("loop"))

        self.assertEqual(scheduled, ["scheduled"])
        self.assertEqual(executed, ["loop"])

    def test_call_page_from_thread_uses_run_task_when_available(self) -> None:
        try:
            from views.dashboard_flet_view import call_page_from_thread
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        executed: list[str] = []
        scheduled: list[str] = []

        class _FakePage:
            def run_task(self, handler) -> None:
                scheduled.append("task")
                asyncio.run(handler())

        call_page_from_thread(_FakePage(), lambda: executed.append("task"))

        self.assertEqual(scheduled, ["task"])
        self.assertEqual(executed, ["task"])

    def test_call_page_from_thread_does_not_retry_callback_after_post_callback_bridge_error(self) -> None:
        try:
            from views.dashboard_flet_view import call_page_from_thread
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        callback_runs: list[str] = []

        class _FakePage:
            def call_from_thread(self, callback) -> None:
                callback()
                raise RuntimeError("bridge cleanup failed")

        def _broken_callback() -> None:
            callback_runs.append("ran")
            raise RuntimeError("callback boom")

        with self.assertLogs("views.dashboard_flet_view", level="WARNING") as captured:
            call_page_from_thread(_FakePage(), _broken_callback)

        self.assertEqual(callback_runs, ["ran"])
        self.assertTrue(any("?€?œë³´??UI ì½œë°± ì²˜ë¦¬ ?¤íŒ¨" in line for line in captured.output))


    def test_build_camera_focus_panel_keeps_controls_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import (
                build_camera_focus_capability_badge,
                build_camera_focus_panel,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _collect_strings(control) -> set[str]:
            values: set[str] = set()

            def _walk(node) -> None:
                if node is None:
                    return
                for attr in ("value", "label", "text"):
                    text = getattr(node, attr, None)
                    if isinstance(text, str) and text:
                        values.add(text)
                content = getattr(node, "content", None)
                if content is not None:
                    _walk(content)
                controls = getattr(node, "controls", None)
                if isinstance(controls, list):
                    for child in controls:
                        _walk(child)

            _walk(control)
            return values

        focus_mode_dropdown = ft.Dropdown(label="focus")
        manual_focus_value_field = ft.TextField(label="manual")
        capability_badge = build_camera_focus_capability_badge(
            text="?„ìž¬ ì¹´ë©”?¼ëŠ” ?˜ë™ ì´ˆì ??ì§€?í•˜ì§€ ?ŠìŠµ?ˆë‹¤.",
            visible=False,
        )

        panel = build_camera_focus_panel(
            on_close=lambda _event=None: None,
            focus_mode_dropdown=focus_mode_dropdown,
            manual_focus_value_field=manual_focus_value_field,
            capability_badge=capability_badge,
        )

        strings = _collect_strings(panel)
        self.assertIn("ê³ ê¸‰ ì¹´ë©”???¤ì •", strings)
        self.assertNotIn("ì´ˆì  ?¤ì •???€?¥í•˜ë©??„ìž¬ ?°í??„ì—??ë°”ë¡œ ?ìš©?©ë‹ˆ??", strings)
        self.assertIs(panel.content.controls[2], capability_badge)
        self.assertIs(panel.content.controls[3], focus_mode_dropdown)
        self.assertIs(panel.content.controls[4], manual_focus_value_field)

    def test_apply_camera_focus_input_tone_sets_soft_field_chrome(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import apply_camera_focus_input_tone
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        focus_mode_dropdown = ft.Dropdown(label="focus")
        manual_focus_value_field = ft.TextField(label="manual")

        apply_camera_focus_input_tone(focus_mode_dropdown, manual_focus_value_field)

        self.assertTrue(focus_mode_dropdown.filled)
        self.assertEqual(focus_mode_dropdown.fill_color, "#F3FCFB")
        self.assertEqual(focus_mode_dropdown.border_color, "#CBEAE6")
        self.assertEqual(manual_focus_value_field.focused_border_color, "#39C5BB")
        self.assertEqual(manual_focus_value_field.content_padding.left, 14)

    def test_build_camera_focus_capability_badge_keeps_warning_copy_and_visibility(self) -> None:
        try:
            from views.dashboard_flet_view import build_camera_focus_capability_badge
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        badge = build_camera_focus_capability_badge(
            text="?„ìž¬ ì¹´ë©”?¼ëŠ” ?˜ë™ ì´ˆì ??ì§€?í•˜ì§€ ?ŠìŠµ?ˆë‹¤.",
            visible=False,
        )

        self.assertFalse(badge.visible)
        self.assertEqual(badge.bgcolor, "#FFF7E8")
        self.assertEqual(badge.content.controls[1].value, "?„ìž¬ ì¹´ë©”?¼ëŠ” ?˜ë™ ì´ˆì ??ì§€?í•˜ì§€ ?ŠìŠµ?ˆë‹¤.")

    def test_build_camera_focus_side_handle_keeps_open_action_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_camera_focus_side_handle
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _on_open(_event=None) -> None:
            return None

        def _on_hover(_event=None) -> None:
            return None

        handle = build_camera_focus_side_handle(on_open=_on_open, on_hover=_on_hover)
        content = handle.content

        self.assertIs(handle.on_click, _on_open)
        self.assertEqual(handle.right, 0)
        self.assertEqual(handle.top, 332)
        self.assertEqual(handle.width, 28)
        self.assertEqual(handle.tooltip, "?¤ì • ?´ê¸°")
        self.assertEqual(handle.bgcolor, "#DCE4EC")
        self.assertIs(handle.on_hover, _on_hover)
        self.assertIsInstance(content.controls[0].content, ft.Icon)
        self.assertEqual(content.controls[2].value, "??)
        self.assertEqual(content.controls[3].value, "??)

    def test_build_dashboard_sidebar_keeps_tabs_without_bottom_settings_action(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_dashboard_sidebar
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        btn_ticket_tab = ft.TextButton("?°ì¼“ ?•ì¸")
        btn_receipt_tab = ft.TextButton("?¤ì •")

        sidebar = build_dashboard_sidebar(
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
        )

        column = sidebar.content
        self.assertIs(column.controls[2], btn_ticket_tab)
        self.assertIs(column.controls[3], btn_receipt_tab)
        self.assertFalse(any(getattr(control, "text", None) == "Settings" for control in column.controls))

    def test_buyer_panel_state_formats_ticket_and_received_sections(self) -> None:
        try:
            from views.dashboard_flet_view import build_buyer_panel_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="A-100",
            name="ê¹€ë¯¼ìˆ˜",
            phone="010-0000-0000",
            seat="A1",
            goods=["VIP ?…ìž¥ê¶?x1", "?¬í† ì¹´ë“œ x1"],
            received_at="2026-03-29 10:30:00",
        )

        panel_state = build_buyer_panel_state(order, ["VIP ?…ìž¥ê¶?])

        self.assertEqual(panel_state.name_text, "ì£¼ë¬¸?ëª…: ê¹€ë¯¼ìˆ˜")
        self.assertEqual(panel_state.phone_text, "?°ë½ì²? 010-0000-0000")
        self.assertEqual(panel_state.seat_text, "ì¢Œì„ë²ˆí˜¸: A1")
        self.assertEqual(panel_state.goods_text, "?¬í† ì¹´ë“œ x1")
        self.assertTrue(panel_state.goods_visible)
        self.assertEqual(panel_state.goods_hint_text, "")
        self.assertEqual(panel_state.ticket_text, "?°ì¼“: VIP ?…ìž¥ê¶?x1")
        self.assertTrue(panel_state.ticket_visible)
        self.assertEqual(panel_state.received_text, "?˜ë ¹?„ë£Œ: 2026-03-29 10:30:00")
        self.assertTrue(panel_state.received_visible)

    def test_buyer_panel_state_hides_optional_sections_when_empty(self) -> None:
        try:
            from views.dashboard_flet_view import build_buyer_panel_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="A-200",
            name="ê¹€ë¯¸ìˆ˜",
            phone="010-1111-2222",
            seat="B2",
            goods=["VIP ?…ìž¥ê¶?x1"],
            received_at="",
        )

        panel_state = build_buyer_panel_state(order, ["VIP ?…ìž¥ê¶?])

        self.assertEqual(panel_state.goods_text, "")
        self.assertFalse(panel_state.goods_visible)
        self.assertEqual(panel_state.goods_hint_text, "ë³„ë„ êµ¬ë§¤ ?í’ˆ???†ìŠµ?ˆë‹¤.")
        self.assertEqual(panel_state.ticket_text, "?°ì¼“: VIP ?…ìž¥ê¶?x1")
        self.assertTrue(panel_state.ticket_visible)
        self.assertEqual(panel_state.received_text, "")
        self.assertFalse(panel_state.received_visible)

    def test_buyer_event_view_state_shows_detail_and_propagates_search_block(self) -> None:
        try:
            from views.dashboard_flet_view import build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="A-100",
            name="Kim",
            phone="010-0000-0000",
            seat="A1",
            goods=["VIP x1", "Poster x1"],
            received_at="2026-03-29 10:30:00",
        )

        state = build_buyer_event_view_state(order, ["VIP"], search_blocked=True)

        self.assertIs(state.order, order)
        self.assertTrue(state.search_blocked)
        self.assertTrue(state.buyer_detail_visible)
        self.assertFalse(state.buyer_empty_hint_visible)
        self.assertTrue(state.panel_state.goods_visible)
        self.assertTrue(state.panel_state.ticket_visible)
        self.assertTrue(state.panel_state.received_visible)

    def test_buyer_event_view_state_keeps_optional_sections_hidden_for_plain_goods(self) -> None:
        try:
            from views.dashboard_flet_view import build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        order = Order(
            order_number="B-200",
            name="Lee",
            phone="010-1111-2222",
            seat="B2",
            goods=["General x1"],
            received_at="",
        )

        state = build_buyer_event_view_state(order, ["VIP"], search_blocked=False)

        self.assertFalse(state.search_blocked)
        self.assertTrue(state.buyer_detail_visible)
        self.assertFalse(state.buyer_empty_hint_visible)
        self.assertTrue(state.panel_state.goods_visible)
        self.assertFalse(state.panel_state.ticket_visible)
        self.assertFalse(state.panel_state.received_visible)

    def test_apply_buyer_event_view_state_updates_text_and_visibility_controls(self) -> None:
        try:
            from views.dashboard_flet_view import apply_buyer_event_view_state, build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.visible = False

        order = Order(
            order_number="A-100",
            name="Kim",
            phone="010-0000-0000",
            seat="A1",
            goods=["VIP x1", "Poster x1"],
            received_at="2026-03-29 10:30:00",
        )
        view_state = build_buyer_event_view_state(order, ["VIP"], search_blocked=False)
        current_buyer_order: dict[str, Order | None] = {"value": None}

        buyer_name_text = _FakeControl()
        buyer_phone_text = _FakeControl()
        buyer_seat_text = _FakeControl()
        buyer_goods_text = _FakeControl()
        buyer_goods_hint = _FakeControl()
        buyer_ticket_text = _FakeControl()
        buyer_received_text = _FakeControl()
        buyer_detail_col = _FakeControl()
        buyer_empty_hint = _FakeControl()
        buyer_empty_hint.visible = True

        apply_buyer_event_view_state(
            view_state,
            current_buyer_order=current_buyer_order,
            buyer_name_text=buyer_name_text,
            buyer_phone_text=buyer_phone_text,
            buyer_seat_text=buyer_seat_text,
            buyer_goods_text=buyer_goods_text,
            buyer_goods_hint=buyer_goods_hint,
            buyer_ticket_text=buyer_ticket_text,
            buyer_received_text=buyer_received_text,
            buyer_detail_col=buyer_detail_col,
            buyer_empty_hint=buyer_empty_hint,
        )

        self.assertIs(current_buyer_order["value"], order)
        self.assertEqual(buyer_name_text.value, "ì£¼ë¬¸?ëª…: Kim")
        self.assertEqual(buyer_phone_text.value, "?°ë½ì²? 010-0000-0000")
        self.assertEqual(buyer_goods_text.value, "Poster x1")
        self.assertTrue(buyer_goods_text.visible)
        self.assertFalse(buyer_goods_hint.visible)
        self.assertEqual(buyer_ticket_text.value, "?°ì¼“: VIP x1")
        self.assertTrue(buyer_ticket_text.visible)
        self.assertEqual(buyer_received_text.value, "?˜ë ¹?„ë£Œ: 2026-03-29 10:30:00")
        self.assertTrue(buyer_received_text.visible)
        self.assertTrue(buyer_detail_col.visible)
        self.assertFalse(buyer_empty_hint.visible)

    def test_apply_buyer_event_view_state_hides_optional_sections_for_plain_goods(self) -> None:
        try:
            from views.dashboard_flet_view import apply_buyer_event_view_state, build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.visible = True

        order = Order(
            order_number="B-200",
            name="Lee",
            phone="010-1111-2222",
            seat="B2",
            goods=["General x1"],
            received_at="",
        )
        view_state = build_buyer_event_view_state(order, ["VIP"], search_blocked=True)
        current_buyer_order: dict[str, Order | None] = {"value": None}

        buyer_name_text = _FakeControl()
        buyer_phone_text = _FakeControl()
        buyer_seat_text = _FakeControl()
        buyer_goods_text = _FakeControl()
        buyer_goods_hint = _FakeControl()
        buyer_ticket_text = _FakeControl()
        buyer_received_text = _FakeControl()
        buyer_detail_col = _FakeControl()
        buyer_empty_hint = _FakeControl()

        apply_buyer_event_view_state(
            view_state,
            current_buyer_order=current_buyer_order,
            buyer_name_text=buyer_name_text,
            buyer_phone_text=buyer_phone_text,
            buyer_seat_text=buyer_seat_text,
            buyer_goods_text=buyer_goods_text,
            buyer_goods_hint=buyer_goods_hint,
            buyer_ticket_text=buyer_ticket_text,
            buyer_received_text=buyer_received_text,
            buyer_detail_col=buyer_detail_col,
            buyer_empty_hint=buyer_empty_hint,
        )

        self.assertIs(current_buyer_order["value"], order)
        self.assertEqual(buyer_goods_text.value, "General x1")
        self.assertTrue(buyer_goods_text.visible)
        self.assertFalse(buyer_goods_hint.visible)
        self.assertEqual(buyer_ticket_text.value, "")
        self.assertFalse(buyer_ticket_text.visible)
        self.assertEqual(buyer_received_text.value, "")
        self.assertFalse(buyer_received_text.visible)
        self.assertTrue(buyer_detail_col.visible)
        self.assertFalse(buyer_empty_hint.visible)

    def test_apply_buyer_event_dashboard_state_updates_controls_and_refresh_state(self) -> None:
        try:
            from views.dashboard_flet_view import apply_buyer_event_dashboard_state, build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.visible = False

        order = Order(
            order_number="A-100",
            name="Kim",
            phone="010-0000-0000",
            seat="A1",
            goods=["VIP x1", "Poster x1"],
            received_at="2026-03-29 10:30:00",
        )
        view_state = build_buyer_event_view_state(order, ["VIP"], search_blocked=False)
        current_buyer_order: dict[str, Order | None] = {"value": None}
        refresh_calls: list[bool] = []

        buyer_name_text = _FakeControl()
        buyer_phone_text = _FakeControl()
        buyer_seat_text = _FakeControl()
        buyer_goods_text = _FakeControl()
        buyer_goods_hint = _FakeControl()
        buyer_ticket_text = _FakeControl()
        buyer_received_text = _FakeControl()
        buyer_detail_col = _FakeControl()
        buyer_empty_hint = _FakeControl()
        buyer_empty_hint.visible = True

        apply_buyer_event_dashboard_state(
            view_state,
            current_buyer_order=current_buyer_order,
            buyer_name_text=buyer_name_text,
            buyer_phone_text=buyer_phone_text,
            buyer_seat_text=buyer_seat_text,
            buyer_goods_text=buyer_goods_text,
            buyer_goods_hint=buyer_goods_hint,
            buyer_ticket_text=buyer_ticket_text,
            buyer_received_text=buyer_received_text,
            buyer_detail_col=buyer_detail_col,
            buyer_empty_hint=buyer_empty_hint,
            refresh_print_controls=lambda *, search_blocked: refresh_calls.append(search_blocked),
        )

        self.assertIs(current_buyer_order["value"], order)
        self.assertEqual(buyer_name_text.value, "ì£¼ë¬¸?ëª…: Kim")
        self.assertEqual(buyer_goods_text.value, "Poster x1")
        self.assertTrue(buyer_goods_text.visible)
        self.assertFalse(buyer_goods_hint.visible)
        self.assertTrue(buyer_ticket_text.visible)
        self.assertTrue(buyer_received_text.visible)
        self.assertTrue(buyer_detail_col.visible)
        self.assertFalse(buyer_empty_hint.visible)
        self.assertEqual(refresh_calls, [False])

    def test_apply_buyer_event_dashboard_state_forwards_blocked_state_to_print_controls(self) -> None:
        try:
            from views.dashboard_flet_view import apply_buyer_event_dashboard_state, build_buyer_event_view_state
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        class _FakeControl:
            def __init__(self) -> None:
                self.value = ""
                self.visible = True

        order = Order(
            order_number="B-200",
            name="Lee",
            phone="010-1111-2222",
            seat="B2",
            goods=["VIP x1"],
            received_at="",
        )
        view_state = build_buyer_event_view_state(order, ["VIP"], search_blocked=True)
        current_buyer_order: dict[str, Order | None] = {"value": None}
        refresh_calls: list[bool] = []

        buyer_name_text = _FakeControl()
        buyer_phone_text = _FakeControl()
        buyer_seat_text = _FakeControl()
        buyer_goods_text = _FakeControl()
        buyer_goods_hint = _FakeControl()
        buyer_ticket_text = _FakeControl()
        buyer_received_text = _FakeControl()
        buyer_detail_col = _FakeControl()
        buyer_empty_hint = _FakeControl()

        apply_buyer_event_dashboard_state(
            view_state,
            current_buyer_order=current_buyer_order,
            buyer_name_text=buyer_name_text,
            buyer_phone_text=buyer_phone_text,
            buyer_seat_text=buyer_seat_text,
            buyer_goods_text=buyer_goods_text,
            buyer_goods_hint=buyer_goods_hint,
            buyer_ticket_text=buyer_ticket_text,
            buyer_received_text=buyer_received_text,
            buyer_detail_col=buyer_detail_col,
            buyer_empty_hint=buyer_empty_hint,
            refresh_print_controls=lambda *, search_blocked: refresh_calls.append(search_blocked),
        )

        self.assertIs(current_buyer_order["value"], order)
        self.assertEqual(buyer_goods_text.value, "")
        self.assertFalse(buyer_goods_text.visible)
        self.assertTrue(buyer_goods_hint.visible)
        self.assertEqual(buyer_goods_hint.value, "ë³„ë„ êµ¬ë§¤ ?í’ˆ???†ìŠµ?ˆë‹¤.")
        self.assertEqual(buyer_ticket_text.value, "?°ì¼“: VIP x1")
        self.assertTrue(buyer_ticket_text.visible)
        self.assertFalse(buyer_received_text.visible)
        self.assertEqual(refresh_calls, [True])

    def test_build_dashboard_sidebar_keeps_width_and_footer(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_dashboard_sidebar
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        sidebar = build_dashboard_sidebar(
            btn_ticket_tab=ft.TextButton("?°ì¼“ ?•ì¸"),
            btn_receipt_tab=ft.TextButton("?¤ì •"),
        )

        column = sidebar.content
        footer_text = column.controls[-1].content

        self.assertEqual(sidebar.width, 244)
        self.assertEqual(sidebar.bgcolor, "#F5F6F8")
        self.assertEqual(footer_text.value, "v1 Control Center")


    def test_build_camera_focus_panel_keeps_controls_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import (
                build_camera_focus_capability_badge,
                build_camera_focus_panel,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _collect_strings(control) -> set[str]:
            values: set[str] = set()

            def _walk(node) -> None:
                if node is None:
                    return
                for attr in ("value", "label", "text"):
                    text = getattr(node, attr, None)
                    if isinstance(text, str) and text:
                        values.add(text)
                content = getattr(node, "content", None)
                if content is not None:
                    _walk(content)
                controls = getattr(node, "controls", None)
                if isinstance(controls, list):
                    for child in controls:
                        _walk(child)

            _walk(control)
            return values

        focus_mode_dropdown = ft.Dropdown(label="focus")
        manual_focus_value_field = ft.TextField(label="manual")
        capability_badge = build_camera_focus_capability_badge(
            text="\ud604\uc7ac \uce74\uba54\ub77c\ub294 \uc218\ub3d9 \ucd08\uc810\uc744 \uc9c0\uc6d0\ud558\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.",
            visible=False,
        )

        panel = build_camera_focus_panel(
            on_close=lambda _event=None: None,
            focus_mode_dropdown=focus_mode_dropdown,
            manual_focus_value_field=manual_focus_value_field,
            capability_badge=capability_badge,
        )

        strings = _collect_strings(panel)
        self.assertIn("\uace0\uae09 \uce74\uba54\ub77c \uc124\uc815", strings)
        self.assertNotIn("\ucd08\uc810 \uc124\uc815\uc744 \uc800\uc7a5\ud558\uba74 \ud604\uc7ac \ub7f0\ud0c0\uc784\uc5d0\ub3c4 \ubc14\ub85c \uc801\uc6a9\ub429\ub2c8\ub2e4.", strings)
        self.assertIs(panel.content.controls[2], capability_badge)
        self.assertIs(panel.content.controls[3], focus_mode_dropdown)
        self.assertIs(panel.content.controls[4], manual_focus_value_field)

    def test_build_camera_focus_side_handle_keeps_open_action_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_camera_focus_side_handle
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _on_open(_event=None) -> None:
            return None

        def _on_hover(_event=None) -> None:
            return None

        handle = build_camera_focus_side_handle(on_open=_on_open, on_hover=_on_hover)
        content = handle.content

        self.assertIs(handle.on_click, _on_open)
        self.assertEqual(handle.right, 0)
        self.assertEqual(handle.top, 292)
        self.assertEqual(handle.width, 64)
        self.assertEqual(handle.tooltip, "\uc124\uc815 \uc5f4\uae30")
        self.assertEqual(handle.bgcolor, "#FBFDFF")
        self.assertIs(handle.on_hover, _on_hover)
        self.assertIsInstance(content.controls[0].content, ft.Icon)
        self.assertEqual(content.controls[2].value, "\uc124")
        self.assertEqual(content.controls[3].value, "\uc815")

    def test_build_dashboard_sidebar_keeps_tabs_and_settings_action(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_dashboard_sidebar
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        btn_ticket_tab = ft.TextButton("\ud2f0\ucf13 \ud655\uc778")
        btn_receipt_tab = ft.TextButton("\uc124\uc815")

        sidebar = build_dashboard_sidebar(
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
        )

        column = sidebar.content
        self.assertIs(column.controls[2], btn_ticket_tab)
        self.assertIs(column.controls[3], btn_receipt_tab)
        self.assertFalse(any(getattr(control, "text", None) == "Settings" for control in column.controls))


    def test_build_camera_focus_panel_keeps_controls_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import (
                build_camera_focus_capability_badge,
                build_camera_focus_panel,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _collect_strings(control) -> set[str]:
            values: set[str] = set()

            def _walk(node) -> None:
                if node is None:
                    return
                for attr in ("value", "label", "text"):
                    text = getattr(node, attr, None)
                    if isinstance(text, str) and text:
                        values.add(text)
                content = getattr(node, "content", None)
                if content is not None:
                    _walk(content)
                controls = getattr(node, "controls", None)
                if isinstance(controls, list):
                    for child in controls:
                        _walk(child)

            _walk(control)
            return values

        focus_mode_dropdown = ft.Dropdown(label="focus")
        manual_focus_value_field = ft.TextField(label="manual")
        capability_badge = build_camera_focus_capability_badge(
            text="\ud604\uc7ac \uce74\uba54\ub77c\ub294 \uc218\ub3d9 \ucd08\uc810\uc744 \uc9c0\uc6d0\ud558\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.",
            visible=False,
        )

        panel = build_camera_focus_panel(
            on_close=lambda _event=None: None,
            focus_mode_dropdown=focus_mode_dropdown,
            manual_focus_value_field=manual_focus_value_field,
            capability_badge=capability_badge,
        )

        strings = _collect_strings(panel)
        self.assertIn("\uce74\uba54\ub77c \ucd08\uc810 \uae30\ub2a5", strings)
        self.assertNotIn("\uace0\uae09 \uce74\uba54\ub77c \uc124\uc815", strings)
        self.assertIs(panel.content.controls[3], capability_badge)
        self.assertIs(panel.content.controls[4], focus_mode_dropdown)
        self.assertIs(panel.content.controls[5], manual_focus_value_field)

    def test_build_camera_focus_drawer_reflects_full_height_sidebar_state(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import (
                CAMERA_SETTINGS_DRAWER_WIDTH,
                build_camera_focus_drawer,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        closed_drawer = build_camera_focus_drawer(
            is_open=False,
            panel_content=ft.Container(),
            on_close=lambda _event=None: None,
        )
        open_drawer = build_camera_focus_drawer(
            is_open=True,
            panel_content=ft.Container(),
            on_close=lambda _event=None: None,
        )

        self.assertEqual(closed_drawer.width, CAMERA_SETTINGS_DRAWER_WIDTH)
        self.assertEqual(closed_drawer.right, 0)
        self.assertEqual(closed_drawer.top, 0)
        self.assertEqual(closed_drawer.bottom, 0)
        self.assertEqual(closed_drawer.opacity, 0.0)
        self.assertEqual((closed_drawer.offset.x, closed_drawer.offset.y), (1.08, 0))
        self.assertEqual(open_drawer.opacity, 1.0)
        self.assertEqual((open_drawer.offset.x, open_drawer.offset.y), (0, 0))

    def test_build_camera_focus_drawer_reflects_overlay_open_state(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import (
                CAMERA_SETTINGS_DRAWER_WIDTH,
                build_camera_focus_drawer,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        closed_drawer = build_camera_focus_drawer(
            is_open=False,
            panel_content=ft.Container(),
            on_close=lambda _event=None: None,
        )
        open_drawer = build_camera_focus_drawer(
            is_open=True,
            panel_content=ft.Container(),
            on_close=lambda _event=None: None,
        )

        self.assertEqual(closed_drawer.width, CAMERA_SETTINGS_DRAWER_WIDTH)
        self.assertEqual(closed_drawer.right, 0)
        self.assertEqual(closed_drawer.top, 0)
        self.assertEqual(closed_drawer.bottom, 0)
        self.assertEqual(closed_drawer.opacity, 0.0)
        self.assertEqual((closed_drawer.offset.x, closed_drawer.offset.y), (1.08, 0))
        self.assertEqual(open_drawer.opacity, 1.0)
        self.assertEqual((open_drawer.offset.x, open_drawer.offset.y), (0, 0))

    def test_build_camera_focus_side_handle_keeps_open_action_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_camera_focus_side_handle
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _on_open(_event=None) -> None:
            return None

        def _on_hover(_event=None) -> None:
            return None

        handle = build_camera_focus_side_handle(on_open=_on_open, on_hover=_on_hover)
        content = handle.content

        self.assertIs(handle.on_click, _on_open)
        self.assertEqual(handle.right, 0)
        self.assertEqual(handle.top, 332)
        self.assertEqual(handle.width, 28)
        self.assertEqual(handle.tooltip, "\uc124\uc815 \uc5f4\uae30")
        self.assertEqual(handle.bgcolor, "#DCE4EC")
        self.assertIs(handle.on_hover, _on_hover)
        self.assertIsInstance(content.controls[0].content, ft.Icon)
        self.assertEqual(content.controls[2].value, "\uc124")
        self.assertEqual(content.controls[3].value, "\uc815")

    def test_build_dashboard_sidebar_keeps_tabs_and_settings_action(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_dashboard_sidebar
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        btn_ticket_tab = ft.TextButton("?°ì¼“ ?•ì¸")
        btn_receipt_tab = ft.TextButton("?ìˆ˜ì¦??‘ì‹")

        sidebar = build_dashboard_sidebar(
            btn_ticket_tab=btn_ticket_tab,
            btn_receipt_tab=btn_receipt_tab,
        )

        column = sidebar.content
        self.assertIs(column.controls[2], btn_ticket_tab)
        self.assertIs(column.controls[3], btn_receipt_tab)
        self.assertFalse(any(getattr(control, "text", None) == "Settings" for control in column.controls))


    def test_build_camera_focus_panel_keeps_controls_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import (
                build_camera_focus_capability_badge,
                build_camera_focus_panel,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _collect_strings(control) -> set[str]:
            values: set[str] = set()

            def _walk(node) -> None:
                if node is None:
                    return
                for attr in ("value", "label", "text"):
                    text = getattr(node, attr, None)
                    if isinstance(text, str) and text:
                        values.add(text)
                content = getattr(node, "content", None)
                if content is not None:
                    _walk(content)
                controls = getattr(node, "controls", None)
                if isinstance(controls, list):
                    for child in controls:
                        _walk(child)

            _walk(control)
            return values

        camera_selector_row = ft.Row(controls=[ft.Dropdown(label="camera")])
        focus_mode_dropdown = ft.Dropdown(label="focus")
        manual_focus_value_field = ft.TextField(label="manual")
        capability_badge = build_camera_focus_capability_badge(
            text="\ud604\uc7ac \uce74\uba54\ub77c\ub294 \uc218\ub3d9 \ucd08\uc810\uc744 \uc9c0\uc6d0\ud558\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.",
            visible=False,
        )

        panel = build_camera_focus_panel(
            on_close=lambda _event=None: None,
            camera_selector_row=camera_selector_row,
            focus_mode_dropdown=focus_mode_dropdown,
            manual_focus_value_field=manual_focus_value_field,
            capability_badge=capability_badge,
        )

        strings = _collect_strings(panel)
        self.assertIn("\uce74\uba54\ub77c \ucd08\uc810 \uae30\ub2a5", strings)
        self.assertIn("\ud604\uc7ac \uc2a4\uce94\uc6a9 \uc6f9\ucea0\uc758 \ucd08\uc810 \ubaa8\ub4dc\uc640 \uc218\ub3d9 \uac12\uc744 \uc870\uc815\ud569\ub2c8\ub2e4.", strings)
        self.assertIs(panel.content.controls[3], camera_selector_row)
        self.assertIs(panel.content.controls[4], capability_badge)
        self.assertIs(panel.content.controls[5], focus_mode_dropdown)
        self.assertIs(panel.content.controls[6], manual_focus_value_field)

    def test_build_camera_focus_side_handle_keeps_open_action_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_camera_focus_side_handle
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _on_open(_event=None) -> None:
            return None

        def _on_hover(_event=None) -> None:
            return None

        handle = build_camera_focus_side_handle(on_open=_on_open, on_hover=_on_hover)
        content = handle.content

        self.assertIs(handle.on_click, _on_open)
        self.assertEqual(handle.right, 0)
        self.assertEqual(handle.top, 332)
        self.assertEqual(handle.width, 28)
        self.assertEqual(handle.height, 116)
        self.assertEqual(handle.tooltip, "\uc124\uc815 \uc5f4\uae30")
        self.assertEqual(handle.bgcolor, "#DCE4EC")
        self.assertIs(handle.on_hover, _on_hover)
        self.assertIsInstance(content.controls[0].content, ft.Icon)
        self.assertEqual(content.controls[2].value, "\uc124")
        self.assertEqual(content.controls[3].value, "\uc815")

    def test_build_camera_focus_side_handle_keeps_open_action_and_strings(self) -> None:
        try:
            import flet as ft
            from views.dashboard_flet_view import build_camera_focus_side_handle
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        def _on_open(_event=None) -> None:
            return None

        def _on_hover(_event=None) -> None:
            return None

        handle = build_camera_focus_side_handle(on_open=_on_open, on_hover=_on_hover)
        content = handle.content

        self.assertIs(handle.on_click, _on_open)
        self.assertEqual(handle.right, 0)
        self.assertEqual(handle.top, 332)
        self.assertEqual(handle.width, 28)
        self.assertEqual(handle.height, 116)
        self.assertEqual(handle.tooltip, "\uc124\uc815 \uc5f4\uae30")
        self.assertEqual(handle.bgcolor, "#DCE4EC")
        self.assertIs(handle.on_hover, _on_hover)
        self.assertIsInstance(content.controls[0].content, ft.Icon)
        self.assertEqual(content.controls[2].value, "\uc124")
        self.assertEqual(content.controls[3].value, "\uc815")

    def test_dashboard_source_resets_ticket_settings_scroll_after_sidebar_update(self) -> None:
        source = Path("views/dashboard_flet_view.py").read_text(encoding="utf-8")

        update_marker = "if push_update:\n                safe_page_update(page, search_refresh_stop)"
        reset_marker = (
            "if is_open and active_tab == \"ticket\":\n"
            "                _reset_settings_panel_scroll(active_panel)"
        )

        update_index = source.find(update_marker)
        self.assertNotEqual(update_index, -1)
        reset_index = source.find(reset_marker, update_index)
        self.assertNotEqual(reset_index, -1)
        self.assertGreater(reset_index, update_index)


if __name__ == "__main__":
    unittest.main()
