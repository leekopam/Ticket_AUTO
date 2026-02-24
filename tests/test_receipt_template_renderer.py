"""영수증 템플릿 파서/렌더러 테스트."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.receipt_template import TemplateElement, load_template, substitute


class ReceiptTemplateRendererTest(unittest.TestCase):
    def test_template_parsing_and_condition_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.tpl"
            path.write_text(
                "\n".join(
                    [
                        "[[CENTER|TITLE]]{{event_title}}",
                        "[[HR]]",
                        "[[IF show_qr]]",
                        "[[QR]]{{qr_payload}}",
                        "[[ENDIF]]",
                        "[[IF show_logo]]",
                        "[[IMG]]{{logo_path}}",
                        "[[ENDIF]]",
                    ]
                ),
                encoding="utf-8",
            )
            elements = load_template(str(path))
            self.assertEqual(elements[0].type, "text")
            self.assertEqual(elements[0].align, "center")
            self.assertEqual(elements[0].style, "title")
            self.assertEqual(elements[1].type, "hr")
            self.assertEqual(elements[2].type, "qr")
            self.assertEqual(elements[2].cond_key, "show_qr")
            self.assertEqual(elements[3].type, "img")
            self.assertEqual(elements[3].cond_key, "show_logo")

    def test_substitute_missing_key_as_empty(self) -> None:
        self.assertEqual(
            substitute("{{order_number}}|{{unknown}}", {"order_number": "A-1"}),
            "A-1|",
        )

    def test_renderer_width_for_58_and_80(self) -> None:
        try:
            from services.receipt_renderer import ReceiptRenderer, RenderConfig
        except ModuleNotFoundError as exc:
            self.skipTest(f"renderer dependency missing: {exc}")

        template = [
            TemplateElement(type="text", text="테스트"),
            TemplateElement(type="hr"),
            TemplateElement(type="qr", text="{{qr_payload}}", cond_key="show_qr"),
        ]
        context = {"qr_payload": "TEST", "show_qr": True}

        image_58 = ReceiptRenderer(RenderConfig(paper_width="58")).render(template, context)
        image_80 = ReceiptRenderer(RenderConfig(paper_width="80")).render(template, context)

        self.assertEqual(image_58.width, 384)
        self.assertEqual(image_80.width, 576)
        self.assertGreater(image_58.height, 0)
        self.assertGreater(image_80.height, 0)


if __name__ == "__main__":
    unittest.main()
