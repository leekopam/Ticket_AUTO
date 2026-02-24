"""Canvas layout storage tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from models.receipt_canvas_model import (
    ReceiptCanvasDocument,
    ReceiptCanvasElement,
    ReceiptCanvasMeta,
)
from services.receipt_canvas_store import ReceiptCanvasStore


class ReceiptCanvasStoreTest(unittest.TestCase):
    def test_round_trip_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "layout.json"
            store = ReceiptCanvasStore(
                default_layout_path=str(path),
                asset_dir=str(Path(temp_dir) / "assets"),
            )
            doc = ReceiptCanvasDocument(
                version=1,
                meta=ReceiptCanvasMeta(
                    name="Test Layout",
                    paper_width="80",
                    canvas_width_px=576,
                    canvas_height_px=900,
                ),
                elements=[
                    ReceiptCanvasElement(
                        id="txt_1",
                        type="text",
                        x=10,
                        y=12,
                        w=180,
                        h=48,
                        text_template="주문번호: {{order_number}}",
                    )
                ],
            )

            store.save_layout(str(path), doc)
            loaded = store.load_layout(str(path))

            self.assertEqual(loaded.version, 1)
            self.assertEqual(loaded.meta.name, "Test Layout")
            self.assertEqual(len(loaded.elements), 1)
            self.assertEqual(loaded.elements[0].text_template, "주문번호: {{order_number}}")

    def test_missing_required_keys_raise_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "layout_invalid.json"
            path.write_text(
                '{"version":1,"meta":{"name":"X","paper_width":"80","canvas_width_px":576,"canvas_height_px":900},'
                '"elements":[{"type":"text","x":0,"y":0,"w":100,"h":40}]}',
                encoding="utf-8",
            )
            store = ReceiptCanvasStore(
                default_layout_path=str(path),
                asset_dir=str(Path(temp_dir) / "assets"),
            )

            with self.assertRaises(ValueError):
                store.load_layout(str(path))

    def test_import_image_asset_copies_to_runtime_asset_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            src = Path(temp_dir) / "src.png"
            Image.new("RGB", (10, 10), "black").save(src)

            asset_dir = Path(temp_dir) / "assets"
            store = ReceiptCanvasStore(
                default_layout_path=str(Path(temp_dir) / "layout.json"),
                asset_dir=str(asset_dir),
            )
            imported_path = store.import_image_asset(str(src))
            imported = Path(imported_path)

            self.assertTrue(imported.exists())
            self.assertTrue(imported.is_file())
            self.assertEqual(imported.parent.resolve(), asset_dir.resolve())


    def test_portable_export_import_round_trip(self) -> None:
        """포터블 내보내기/불러오기 라운드트립 검증."""
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "assets"
            asset_dir.mkdir()

            # 테스트 이미지 생성
            src_img = asset_dir / "img_test123456.png"
            Image.new("RGB", (20, 20), "red").save(src_img)

            store = ReceiptCanvasStore(
                default_layout_path=str(Path(temp_dir) / "layout.json"),
                asset_dir=str(asset_dir),
            )

            doc = ReceiptCanvasDocument(
                version=1,
                meta=ReceiptCanvasMeta(
                    name="Portable Test",
                    paper_width="80",
                    canvas_width_px=576,
                    canvas_height_px=900,
                ),
                elements=[
                    ReceiptCanvasElement(
                        id="img_1",
                        type="image",
                        x=0, y=0, w=100, h=100,
                        asset_path=src_img.as_posix(),
                    ),
                    ReceiptCanvasElement(
                        id="txt_1",
                        type="text",
                        x=0, y=110, w=200, h=40,
                        text_template="Hello",
                    ),
                ],
            )

            # 포터블 내보내기
            export_path = Path(temp_dir) / "exported.json"
            store.export_portable(str(export_path), doc)
            self.assertTrue(export_path.exists())

            # JSON에 embedded_data가 포함되어 있는지 확인
            import json
            exported = json.loads(export_path.read_text(encoding="utf-8"))
            img_elem = exported["elements"][0]
            self.assertTrue(img_elem["embedded_data"].startswith("data:image/png;base64,"))
            # text 엘리먼트는 embedded_data 비어있음
            self.assertEqual(exported["elements"][1]["embedded_data"], "")

            # 원본 이미지 삭제 후 다른 asset 디렉토리로 불러오기
            src_img.unlink()
            restore_asset_dir = Path(temp_dir) / "restored_assets"
            store2 = ReceiptCanvasStore(
                default_layout_path=str(Path(temp_dir) / "layout.json"),
                asset_dir=str(restore_asset_dir),
            )
            loaded = store2.load_layout(str(export_path))

            # 이미지 엘리먼트의 asset_path가 복원되었는지 확인
            restored_elem = loaded.elements[0]
            self.assertTrue(Path(restored_elem.asset_path).exists())
            self.assertTrue(restored_elem.asset_path.startswith(restore_asset_dir.as_posix()))
            # embedded_data는 유지됨
            self.assertTrue(restored_elem.embedded_data.startswith("data:image/png;base64,"))

            # text 엘리먼트는 영향 없음
            self.assertEqual(loaded.elements[1].text_template, "Hello")
            self.assertEqual(loaded.elements[1].embedded_data, "")

    def test_load_layout_skips_restore_if_asset_exists(self) -> None:
        """asset_path 파일이 이미 존재하면 복원을 건너뛰는지 검증."""
        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "assets"
            asset_dir.mkdir()

            # 기존 이미지 파일
            existing_img = asset_dir / "img_existing.png"
            Image.new("RGB", (10, 10), "blue").save(existing_img)

            store = ReceiptCanvasStore(
                default_layout_path=str(Path(temp_dir) / "layout.json"),
                asset_dir=str(asset_dir),
            )

            doc = ReceiptCanvasDocument(
                version=1,
                meta=ReceiptCanvasMeta(
                    name="Test",
                    paper_width="80",
                    canvas_width_px=576,
                    canvas_height_px=900,
                ),
                elements=[
                    ReceiptCanvasElement(
                        id="img_1",
                        type="image",
                        x=0, y=0, w=100, h=100,
                        asset_path=existing_img.as_posix(),
                    ),
                ],
            )

            # 포터블 내보내기
            export_path = Path(temp_dir) / "test.json"
            store.export_portable(str(export_path), doc)

            # 불러오기 - 기존 파일이 존재하므로 asset_path 변경 없음
            loaded = store.load_layout(str(export_path))
            self.assertEqual(loaded.elements[0].asset_path, existing_img.as_posix())


if __name__ == "__main__":
    unittest.main()
