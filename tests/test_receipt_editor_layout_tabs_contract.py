"""Receipt editor tab surface contract tests."""
from __future__ import annotations

import unittest
from pathlib import Path


class ReceiptEditorLayoutTabsContractTest(unittest.TestCase):
    def test_editor_exposes_receipt_and_product_receipt_controls(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn('ft.TextButton("영수증"', source)
        self.assertIn('ft.TextButton("상품 영수증"', source)
        self.assertIn('ft.Switch(', source)
        self.assertIn("상품 영수증 추가 출력", source)
        self.assertIn("QR 스캔 시 영수증 자동 출력", source)
        self.assertIn('getattr(settings_store.load(), "qr_scan_auto_print_enabled", True)', source)

    def test_canvas_preview_is_1to1_and_viewport_scales_with_window(self) -> None:
        """미리보기는 실제 px와 1:1을 유지하고, 뷰포트 상한만 창 높이에 맞춰 조정된다."""
        try:
            from views.settings_flet_view import (
                CANVAS_PREVIEW_MIN_WIDTH,
                CANVAS_VIEWPORT_DEFAULT_MAX_HEIGHT,
                CANVAS_VIEWPORT_MIN_HEIGHT,
                resolve_canvas_preview_width,
                resolve_canvas_viewport_max_height,
            )
        except ModuleNotFoundError as exc:
            self.skipTest(f"flet not installed: {exc}")

        # 창 크기와 무관하게 미리보기는 실제 px를 넘지 않는다 (1:1 상한)
        self.assertEqual(resolve_canvas_preview_width(None, 576), 576)
        self.assertEqual(resolve_canvas_preview_width(1800, 576), 576)
        self.assertEqual(resolve_canvas_preview_width(1800, 384), 384)
        self.assertEqual(
            resolve_canvas_viewport_max_height(None),
            CANVAS_VIEWPORT_DEFAULT_MAX_HEIGHT,
        )
        # 창이 좁으면 미리보기는 가용 폭까지만 축소되고 뷰포트는 창 높이를 따라간다
        self.assertLess(resolve_canvas_preview_width(1200, 576), 576)
        self.assertGreater(
            resolve_canvas_viewport_max_height(920),
            CANVAS_VIEWPORT_DEFAULT_MAX_HEIGHT,
        )
        # 극단적으로 작은 창에서는 최소값을 보장한다
        self.assertEqual(resolve_canvas_preview_width(600, 576), CANVAS_PREVIEW_MIN_WIDTH)
        self.assertEqual(resolve_canvas_viewport_max_height(480), CANVAS_VIEWPORT_MIN_HEIGHT)

    def test_dashboard_forwards_window_size_to_receipt_panel(self) -> None:
        """대시보드 리사이즈 핸들러가 패널의 _apply_window_size 훅을 호출한다."""
        source = Path("views/dashboard_flet_view.py").read_text(encoding="utf-8-sig")
        self.assertIn('receipt_settings_panel_ref["value"], "_apply_window_size", None', source)
        self.assertIn("apply_window_size(window_w, window_h)", source)


if __name__ == "__main__":
    unittest.main()
