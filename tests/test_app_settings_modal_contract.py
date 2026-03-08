"""App settings modal structure tests."""
from __future__ import annotations

import unittest


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


if __name__ == "__main__":
    unittest.main()
