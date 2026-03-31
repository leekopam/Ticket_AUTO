"""JSON 캔버스 레이아웃 저장소 및 에셋(Asset) 가져오기 도우미입니다."""
from __future__ import annotations

import base64
import json
import logging
import shutil
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from models.receipt_canvas_model import (
    ReceiptCanvasDocument,
    create_default_document,
    paper_width_to_px,
)
from project_paths import (
    RESOURCE_RECEIPT_TEMPLATE_FILE,
    ensure_managed_templates_dir,
    resolve_project_path,
)

logger = logging.getLogger(__name__)


DEFAULT_LAYOUT_PATH = RESOURCE_RECEIPT_TEMPLATE_FILE.as_posix()
ASSET_DIR = ".runtime/receipt_assets"


class ReceiptCanvasStore:
    """캔버스 레이아웃 문서(Canvas layout documents)를 영구 저장하고 불러옵니다."""

    def __init__(
        self,
        *,
        default_layout_path: str = DEFAULT_LAYOUT_PATH,
        asset_dir: str = ASSET_DIR,
    ):
        ensure_managed_templates_dir()
        self._default_layout_path = resolve_project_path(default_layout_path)
        self._asset_dir = resolve_project_path(asset_dir)

    def load_layout(self, path: str) -> ReceiptCanvasDocument:
        target = resolve_project_path(path)
        if not target.exists():
            return create_default_document()

        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("레이아웃 JSON의 최상위 노드는 객체(Object)여야 합니다.")

        doc = ReceiptCanvasDocument.from_dict(payload)
        self._restore_embedded_images(doc)
        return self._normalize_canvas_width(doc)

    def save_layout(self, path: str, doc: ReceiptCanvasDocument) -> None:
        normalized = self._normalize_canvas_width(doc)
        target = resolve_project_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(normalized.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def import_image_asset(self, src: str) -> str:
        source = Path(src)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {src}")

        self._asset_dir.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix if source.suffix else ".png"
        name = f"img_{uuid4().hex[:12]}{suffix}"
        target = self._asset_dir / name
        shutil.copy2(source, target)
        return target.as_posix()

    def export_portable(self, path: str, doc: ReceiptCanvasDocument) -> None:
        """이미지 애셋을 Base64 형태로 내장(Embed)한 휴대용(Portable) JSON을 내보냅니다."""
        portable_doc = self._normalize_canvas_width(
            ReceiptCanvasDocument.from_dict(doc.to_dict())
        )

        for elem in portable_doc.elements:
            if elem.type != "image" or not elem.asset_path:
                continue
            asset = Path(elem.asset_path)
            if not asset.exists() or not asset.is_file():
                logger.warning("누락된 이미지를 건너뛰고 휴대용 내보내기를 진행합니다: %s", elem.asset_path)
                continue

            raw = asset.read_bytes()
            ext = asset.suffix.lower().lstrip(".")
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "bmp": "bmp"}.get(ext, ext)
            elem.embedded_data = (
                f"data:image/{mime};base64,{base64.b64encode(raw).decode('ascii')}"
            )

        target = resolve_project_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(portable_doc.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _restore_embedded_images(self, doc: ReceiptCanvasDocument) -> None:
        """내장된 Base64 이미지를 런타임 에셋(Asset) 디렉터리로 복원합니다."""
        for elem in doc.elements:
            if elem.type != "image" or not elem.embedded_data:
                continue
            if elem.asset_path and Path(elem.asset_path).exists():
                continue
            try:
                data_str = elem.embedded_data
                if data_str.startswith("data:"):
                    header, b64_data = data_str.split(",", 1)
                    mime_part = header.split(";")[0]
                    img_type = mime_part.split("/")[-1]
                    ext = {"jpeg": ".jpg"}.get(img_type, f".{img_type}")
                else:
                    b64_data = data_str
                    ext = ".png"

                raw = base64.b64decode(b64_data)
                self._asset_dir.mkdir(parents=True, exist_ok=True)
                name = f"img_{uuid4().hex[:12]}{ext}"
                restored = self._asset_dir / name
                restored.write_bytes(raw)
                elem.asset_path = restored.as_posix()
            except Exception:
                logger.exception("이미지 복원에 실패했습니다: element=%s", elem.id)

    def ensure_default_layout(self) -> str:
        """기본 JSON 템플릿 파일이 존재하는지 확인하고, 해당 경로를 반환합니다."""
        if not self._default_layout_path.exists():
            doc = create_default_document()
            self.save_layout(str(self._default_layout_path), doc)
        return self._default_layout_path.as_posix()

    def _normalize_canvas_width(self, doc: ReceiptCanvasDocument) -> ReceiptCanvasDocument:
        target_width = max(1, paper_width_to_px(doc.meta.paper_width))
        normalized = doc

        if int(normalized.meta.canvas_width_px) != target_width:
            normalized = replace(
                normalized,
                meta=replace(normalized.meta, canvas_width_px=target_width),
            )

        if not normalized.elements:
            return normalized

        effective_width = max(
            target_width,
            max(int(element.x) + max(1, int(element.w)) for element in normalized.elements),
        )
        if effective_width <= target_width:
            return normalized

        ratio = target_width / effective_width
        resized = []
        for element in normalized.elements:
            new_x = int(round(element.x * ratio))
            new_w = max(1, int(round(element.w * ratio)))
            if new_w > target_width:
                new_w = target_width
            max_x = max(0, target_width - new_w)
            if new_x > max_x:
                new_x = max_x
            resized.append(replace(element, x=new_x, w=new_w))

        return replace(normalized, elements=resized)
