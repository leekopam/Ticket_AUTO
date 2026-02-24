"""Canvas-based receipt layout model."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4


PaperWidth = Literal["58", "80"]
ElementType = Literal["text", "image", "qr", "divider"]
AlignType = Literal["left", "center", "right"]

VALID_ELEMENT_TYPES: set[str] = {"text", "image", "qr", "divider"}
VALID_ALIGN: set[str] = {"left", "center", "right"}


def paper_width_to_px(paper_width: str) -> int:
    return 384 if paper_width == "58" else 576


@dataclass
class ReceiptCanvasMeta:
    """Canvas document metadata."""

    name: str = "Receipt Layout"
    paper_width: PaperWidth = "80"
    canvas_width_px: int = 576
    canvas_height_px: int = 900

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "paper_width": self.paper_width,
            "canvas_width_px": int(self.canvas_width_px),
            "canvas_height_px": int(self.canvas_height_px),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReceiptCanvasMeta":
        required = ("name", "paper_width", "canvas_width_px", "canvas_height_px")
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"meta missing keys: {', '.join(missing)}")

        paper_width_raw = str(payload.get("paper_width", "80")).strip()
        paper_width: PaperWidth = "58" if paper_width_raw == "58" else "80"
        canvas_width_px = int(payload.get("canvas_width_px", paper_width_to_px(paper_width)))
        canvas_height_px = max(120, int(payload.get("canvas_height_px", 900)))

        return cls(
            name=str(payload.get("name", "Receipt Layout")).strip() or "Receipt Layout",
            paper_width=paper_width,
            canvas_width_px=canvas_width_px,
            canvas_height_px=canvas_height_px,
        )


@dataclass
class ReceiptCanvasElement:
    """Single canvas element."""

    id: str
    type: ElementType
    x: int
    y: int
    w: int
    h: int
    align: AlignType = "left"
    visible: bool = True

    text_template: str = ""
    font_size: int = 22
    bold: bool = False

    asset_path: str = ""
    embedded_data: str = ""  # base64 인코딩된 이미지 데이터 (포터블 저장용)
    preserve_ratio: bool = True

    data_template: str = "{{order_number}}|{{url}}"
    box_size: int = 4

    line_style: str = "solid"  # solid / dashed / dotted
    line_thickness: int = 1    # 구분선 굵기 (px)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "x": int(self.x),
            "y": int(self.y),
            "w": int(self.w),
            "h": int(self.h),
            "align": self.align,
            "visible": bool(self.visible),
            "text_template": self.text_template,
            "font_size": int(self.font_size),
            "bold": bool(self.bold),
            "asset_path": self.asset_path,
            "embedded_data": self.embedded_data,
            "preserve_ratio": bool(self.preserve_ratio),
            "data_template": self.data_template,
            "box_size": int(self.box_size),
            "line_style": self.line_style,
            "line_thickness": int(self.line_thickness),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReceiptCanvasElement":
        required = ("id", "type", "x", "y", "w", "h")
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"element missing keys: {', '.join(missing)}")

        raw_type = str(payload["type"]).strip().lower()
        if raw_type not in VALID_ELEMENT_TYPES:
            raise ValueError(f"invalid element type: {raw_type}")

        raw_align = str(payload.get("align", "left")).strip().lower()
        align: AlignType = "left"
        if raw_align in VALID_ALIGN:
            align = raw_align  # type: ignore[assignment]

        return cls(
            id=str(payload["id"]).strip(),
            type=raw_type,  # type: ignore[arg-type]
            x=int(payload["x"]),
            y=int(payload["y"]),
            w=max(1, int(payload["w"])),
            h=max(1, int(payload["h"])),
            align=align,
            visible=bool(payload.get("visible", True)),
            text_template=str(payload.get("text_template", "")),
            font_size=max(8, int(payload.get("font_size", 22))),
            bold=bool(payload.get("bold", False)),
            asset_path=str(payload.get("asset_path", "")).strip(),
            embedded_data=str(payload.get("embedded_data", "")),
            preserve_ratio=bool(payload.get("preserve_ratio", True)),
            data_template=str(payload.get("data_template", "{{order_number}}|{{url}}")),
            box_size=max(1, int(payload.get("box_size", 4))),
            line_style=str(payload.get("line_style", "solid")).strip() or "solid",
            line_thickness=max(1, int(payload.get("line_thickness", 1))),
        )


@dataclass
class ReceiptCanvasDocument:
    """Canvas document root."""

    version: int = 1
    meta: ReceiptCanvasMeta = field(default_factory=ReceiptCanvasMeta)
    elements: list[ReceiptCanvasElement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "meta": self.meta.to_dict(),
            "elements": [element.to_dict() for element in self.elements],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReceiptCanvasDocument":
        version = int(payload.get("version", 0))
        if version != 1:
            raise ValueError(f"unsupported canvas version: {version}")

        meta_raw = payload.get("meta")
        if not isinstance(meta_raw, dict):
            raise ValueError("meta must be an object")
        meta = ReceiptCanvasMeta.from_dict(meta_raw)

        elements_raw = payload.get("elements", [])
        if not isinstance(elements_raw, list):
            raise ValueError("elements must be a list")
        elements = [ReceiptCanvasElement.from_dict(item) for item in elements_raw]

        return cls(version=version, meta=meta, elements=elements)


def create_default_document(paper_width: PaperWidth = "80") -> ReceiptCanvasDocument:
    """Create empty default layout document."""
    width = paper_width_to_px(paper_width)
    return ReceiptCanvasDocument(
        version=1,
        meta=ReceiptCanvasMeta(
            name="Receipt Layout",
            paper_width=paper_width,
            canvas_width_px=width,
            canvas_height_px=900,
        ),
        elements=[],
    )


def make_element_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"
