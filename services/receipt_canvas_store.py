"""JSON canvas layout storage and asset import helpers."""
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

logger = logging.getLogger(__name__)


DEFAULT_LAYOUT_PATH = "templates/receipt_layout.json"
ASSET_DIR = ".runtime/receipt_assets"


class ReceiptCanvasStore:
    """Persist and load canvas layout documents."""

    def __init__(
        self,
        *,
        default_layout_path: str = DEFAULT_LAYOUT_PATH,
        asset_dir: str = ASSET_DIR,
    ):
        self._default_layout_path = Path(default_layout_path)
        self._asset_dir = Path(asset_dir)

    def load_layout(self, path: str) -> ReceiptCanvasDocument:
        target = Path(path)
        if not target.exists():
            return create_default_document()

        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("layout json root must be object")

        doc = ReceiptCanvasDocument.from_dict(payload)
        self._restore_embedded_images(doc)
        return self._normalize_canvas_width(doc)

    def save_layout(self, path: str, doc: ReceiptCanvasDocument) -> None:
        normalized = self._normalize_canvas_width(doc)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(normalized.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def import_image_asset(self, src: str) -> str:
        source = Path(src)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"image file not found: {src}")

        self._asset_dir.mkdir(parents=True, exist_ok=True)
        suffix = source.suffix if source.suffix else ".png"
        name = f"img_{uuid4().hex[:12]}{suffix}"
        target = self._asset_dir / name
        shutil.copy2(source, target)
        return target.as_posix()

    def export_portable(self, path: str, doc: ReceiptCanvasDocument) -> None:
        """Export a portable JSON that embeds image assets as base64."""
        portable_doc = self._normalize_canvas_width(
            ReceiptCanvasDocument.from_dict(doc.to_dict())
        )

        for elem in portable_doc.elements:
            if elem.type != "image" or not elem.asset_path:
                continue
            asset = Path(elem.asset_path)
            if not asset.exists() or not asset.is_file():
                logger.warning("portable export skipped missing image: %s", elem.asset_path)
                continue

            raw = asset.read_bytes()
            ext = asset.suffix.lower().lstrip(".")
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "bmp": "bmp"}.get(ext, ext)
            elem.embedded_data = (
                f"data:image/{mime};base64,{base64.b64encode(raw).decode('ascii')}"
            )

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(portable_doc.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _restore_embedded_images(self, doc: ReceiptCanvasDocument) -> None:
        """Restore embedded base64 images into the runtime asset directory."""
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
                logger.exception("image restore failed: element=%s", elem.id)

    def ensure_default_layout(self) -> str:
        """Ensure the default JSON template exists and return its path."""
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
