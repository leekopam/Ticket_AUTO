"""Renders receipt images from template elements."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import qrcode

from services.receipt_template import TemplateElement, substitute


@dataclass
class RenderConfig:
    """Renderer output options."""

    paper_width: str = "80"  # "58" or "80"
    margin: int = 16
    line_gap: int = 6
    dpi: int = 203


def _paper_width_px(paper_width: str, dpi: int = 203) -> int:
    from models.receipt_canvas_model import paper_width_to_px
    return paper_width_to_px(paper_width, dpi=dpi)


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []
    if bold:
        candidates.append(r"C:\Windows\Fonts\malgunbd.ttf")
    candidates.extend(
        [
            r"C:\Windows\Fonts\malgun.ttf",
            r"C:\Windows\Fonts\gulim.ttc",
        ]
    )

    for candidate in candidates:
        if not Path(candidate).exists():
            continue
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue

    return ImageFont.load_default()


def _draw_dashed_line(draw: ImageDraw.ImageDraw, x0: int, x1: int, y: int) -> None:
    dash = 14
    gap = 8
    cursor = x0
    while cursor < x1:
        draw.line((cursor, y, min(cursor + dash, x1), y), fill=(0, 0, 0), width=2)
        cursor += dash + gap


def _wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    if text is None:
        return [""]

    wrapped: list[str] = []
    for paragraph in str(text).split("\n"):
        line = ""
        for char in paragraph:
            candidate = line + char
            if draw.textlength(candidate, font=font) <= max_width:
                line = candidate
                continue
            if line:
                wrapped.append(line)
                line = char
            else:
                wrapped.append(char)
                line = ""
        wrapped.append(line)
    return wrapped


class ReceiptRenderer:
    """Converts parsed template + context to bitmap image."""

    def __init__(self, config: RenderConfig):
        self._config = config
        self._width = _paper_width_px(config.paper_width, dpi=config.dpi)

        if config.paper_width == "58":
            self._font_normal = _load_font(20)
            self._font_bold = _load_font(22, bold=True)
            self._font_title = _load_font(28, bold=True)
        else:
            self._font_normal = _load_font(22)
            self._font_bold = _load_font(24, bold=True)
            self._font_title = _load_font(32, bold=True)

    def render(self, template: list[TemplateElement], context: dict[str, object]) -> Image.Image:
        resolved = self._resolve_elements(template, context)
        content_width = self._width - (self._config.margin * 2)
        measured_height, images, qrs = self._measure(resolved, content_width)

        canvas = Image.new("RGB", (self._width, measured_height), "white")
        draw = ImageDraw.Draw(canvas)

        image_cache = {index: image for index, image in images}
        qr_cache = {index: image for index, image in qrs}

        y = self._config.margin
        for index, element in enumerate(resolved):
            if element.type == "hr":
                _draw_dashed_line(
                    draw,
                    self._config.margin,
                    self._width - self._config.margin,
                    y,
                )
                y += 12
                continue

            if element.type == "text":
                font = self._font_for_style(element.style)
                for line in _wrap_lines(draw, element.text, font, content_width):
                    display_text = line if line else " "
                    box = draw.textbbox((0, 0), display_text, font=font)
                    text_width = box[2] - box[0]
                    text_height = box[3] - box[1]
                    if element.align == "center":
                        x = self._config.margin + (content_width - text_width) // 2
                    elif element.align == "right":
                        x = self._config.margin + (content_width - text_width)
                    else:
                        x = self._config.margin
                    draw.text((x, y), display_text, fill=(0, 0, 0), font=font)
                    y += text_height + self._config.line_gap
                continue

            if element.type == "img":
                image = image_cache.get(index)
                if image is None:
                    continue
                x = self._config.margin + (content_width - image.width) // 2
                canvas.paste(image, (x, y), image)
                y += image.height + 10
                continue

            if element.type == "qr":
                image = qr_cache.get(index)
                if image is None:
                    continue
                x = self._config.margin + (content_width - image.width) // 2
                canvas.paste(image, (x, y))
                y += image.height + 10

        return canvas

    def _resolve_elements(
        self,
        template: list[TemplateElement],
        context: dict[str, object],
    ) -> list[TemplateElement]:
        resolved: list[TemplateElement] = []
        for element in template:
            if element.cond_key and not bool(context.get(element.cond_key, False)):
                continue
            resolved.append(
                TemplateElement(
                    type=element.type,
                    text=substitute(element.text, context),
                    align=element.align,
                    style=element.style,
                    cond_key=None,
                )
            )
        return resolved

    def _measure(
        self,
        elements: list[TemplateElement],
        content_width: int,
    ) -> tuple[int, list[tuple[int, Image.Image]], list[tuple[int, Image.Image]]]:
        probe = Image.new("RGB", (self._width, 8), "white")
        draw = ImageDraw.Draw(probe)

        total_height = self._config.margin
        image_items: list[tuple[int, Image.Image]] = []
        qr_items: list[tuple[int, Image.Image]] = []

        for index, element in enumerate(elements):
            if element.type == "hr":
                total_height += 12
                continue

            if element.type == "text":
                font = self._font_for_style(element.style)
                for line in _wrap_lines(draw, element.text, font, content_width):
                    display_text = line if line else " "
                    box = draw.textbbox((0, 0), display_text, font=font)
                    total_height += (box[3] - box[1]) + self._config.line_gap
                continue

            if element.type == "img":
                path = Path(element.text.strip())
                if not path.exists():
                    continue
                with Image.open(path) as source:
                    image = source.convert("RGBA")
                max_width = int(content_width * 0.8)
                if image.width > max_width:
                    ratio = max_width / image.width
                    image = image.resize((max_width, int(image.height * ratio)))
                image_items.append((index, image))
                total_height += image.height + 10
                continue

            if element.type == "qr":
                payload = element.text.strip()
                if not payload:
                    continue
                qr = qrcode.QRCode(
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                    box_size=4,
                    border=1,
                )
                qr.add_data(payload)
                qr.make(fit=True)
                image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                max_width = int(content_width * 0.6)
                if image.width > max_width:
                    image = image.resize((max_width, max_width))
                qr_items.append((index, image))
                total_height += image.height + 10

        total_height += self._config.margin
        return total_height, image_items, qr_items

    def _font_for_style(self, style: str):
        if style == "title":
            return self._font_title
        if style == "bold":
            return self._font_bold
        return self._font_normal
