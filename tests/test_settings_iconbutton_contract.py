"""Receipt settings IconButton compatibility contract tests (Flet 0.23.x)."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


class SettingsIconButtonContractTest(unittest.TestCase):
    def setUp(self) -> None:
        source = Path("views/settings_flet_view.py").read_text(encoding="utf-8-sig")
        self.tree = ast.parse(source)

    def test_iconbutton_icon_param_is_not_a_widget(self) -> None:
        """Flet 0.23.x IconButton의 icon 파라미터에는 위젯이 아닌 아이콘 이름만 허용된다.
        커스텀 위젯(ft.Text 등)을 사용하려면 content 파라미터를 사용해야 한다."""
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_iconbutton = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "ft"
                and func.attr == "IconButton"
            )
            if not is_iconbutton:
                continue
            for kw in node.keywords:
                if kw.arg != "icon":
                    continue
                # icon 파라미터에 ft.Text 등 위젯 호출이 들어가면 안 됨
                self.assertNotIsInstance(
                    kw.value, ast.Call,
                    "IconButton의 icon에 위젯 호출 대신 아이콘 이름을 사용하거나 content를 사용하세요",
                )


if __name__ == "__main__":
    unittest.main()
