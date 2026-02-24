"""Bottom anchor helper contract tests."""
from __future__ import annotations

import unittest

from views.settings_flet_view import _compose_bottom_anchor


class SettingsCanvasBottomAnchorContractTest(unittest.TestCase):
    def test_margin_bottom_zero_disables_anchor(self) -> None:
        anchor = _compose_bottom_anchor(
            margin_bottom_px=0,
            other_start_y=900,
            moving_bottom_y=950,
        )
        self.assertIsNone(anchor)

    def test_anchor_uses_other_start_when_higher(self) -> None:
        anchor = _compose_bottom_anchor(
            margin_bottom_px=40,
            other_start_y=1000,
            moving_bottom_y=950,
        )
        self.assertEqual(anchor, 1000)

    def test_anchor_preserves_moving_bottom_when_higher(self) -> None:
        anchor = _compose_bottom_anchor(
            margin_bottom_px=40,
            other_start_y=900,
            moving_bottom_y=950,
        )
        self.assertEqual(anchor, 950)

    def test_anchor_handles_missing_other_start(self) -> None:
        anchor = _compose_bottom_anchor(
            margin_bottom_px=40,
            other_start_y=None,
            moving_bottom_y=920,
        )
        self.assertEqual(anchor, 920)


if __name__ == "__main__":
    unittest.main()
