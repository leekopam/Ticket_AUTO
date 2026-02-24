"""Renderer for JSON canvas receipt layout."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import qrcode

from models.receipt_canvas_model import ReceiptCanvasDocument, ReceiptCanvasElement, paper_width_to_px
from services.receipt_template import substitute


def _load_font(size: int, *, bold: bool = False):
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
            return ImageFont.truetype(candidate, max(8, int(size)))
        except Exception:
            continue
    return ImageFont.load_default()


def _fit_inside(src_w: int, src_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    if src_w <= 0 or src_h <= 0:
        return max_w, max_h
    ratio = min(max_w / src_w, max_h / src_h)
    ratio = max(0.01, ratio)
    return max(1, int(src_w * ratio)), max(1, int(src_h * ratio))


def _text_align_x(align: str, box_x: int, box_w: int, text_w: int) -> int:
    if align == "center":
        return box_x + max(0, (box_w - text_w) // 2)
    if align == "right":
        return box_x + max(0, box_w - text_w)
    return box_x


def render_canvas_layout(
    doc: ReceiptCanvasDocument,
    context: dict[str, object],
    paper_width: str,
    margin_top: int = 0,
    margin_bottom: int = 0,
) -> Image.Image:
    """Render canvas document to receipt bitmap."""
    width = paper_width_to_px(paper_width)
    base_width = max(1, int(doc.meta.canvas_width_px))
    scale = width / base_width

    elements = [element for element in doc.elements if element.visible]
    if elements:
        max_bottom = max(element.y + element.h for element in elements)
        canvas_height = max(max_bottom, int(doc.meta.canvas_height_px))
    else:
        canvas_height = max(180, int(doc.meta.canvas_height_px))

    # 요소 Y좌표는 에디터에서 이미 margin_top 이후에 배치됨 (y >= margin_top)
    # canvas_height는 y=0부터의 높이이므로 margin_top 영역 포함 → margin_bottom만 추가
    mb = max(0, int(margin_bottom * scale))

    out_height = max(1, int(canvas_height * scale) + mb)
    image = Image.new("RGB", (width, out_height), "white")
    draw = ImageDraw.Draw(image)

    for element in elements:
        x = int(element.x * scale)
        y = int(element.y * scale)
        w = max(1, int(element.w * scale))
        h = max(1, int(element.h * scale))

        if element.type == "text":
            _render_text_element(draw, element, context, x, y, w, h, scale)
        elif element.type == "image":
            _render_image_element(image, element, x, y, w, h)
        elif element.type == "qr":
            _render_qr_element(image, element, context, x, y, w, h)
        elif element.type == "divider":
            _render_divider_element(draw, element, context, x, y, w, h, scale)

    return image


def _render_text_element(
    draw: ImageDraw.ImageDraw,
    element: ReceiptCanvasElement,
    context: dict[str, object],
    x: int,
    y: int,
    w: int,
    h: int,
    scale: float,
) -> None:
    text = substitute(element.text_template, context)
    font = _load_font(max(8, int(element.font_size * scale)), bold=element.bold)

    lines = str(text).split("\n")
    cursor_y = y
    for line in lines:
        content = line if line else " "
        box = draw.textbbox((0, 0), content, font=font)
        text_w = box[2] - box[0]
        text_h = box[3] - box[1]
        if cursor_y + text_h > y + h:
            break

        text_x = _text_align_x(element.align, x, w, text_w)
        draw.text((text_x, cursor_y), content, fill=(0, 0, 0), font=font)
        cursor_y += text_h + max(2, int(4 * scale))


def _render_image_element(
    canvas: Image.Image,
    element: ReceiptCanvasElement,
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    if not element.asset_path:
        return

    path = Path(element.asset_path)
    if not path.exists():
        return

    with Image.open(path) as source:
        img = source.convert("RGBA")

    if element.preserve_ratio:
        target_w, target_h = _fit_inside(img.width, img.height, w, h)
    else:
        target_w, target_h = w, h
    img = img.resize((target_w, target_h))

    offset_x = _text_align_x(element.align, x, w, target_w)
    offset_y = y + max(0, (h - target_h) // 2)
    canvas.paste(img, (offset_x, offset_y), img)


def _render_qr_element(
    canvas: Image.Image,
    element: ReceiptCanvasElement,
    context: dict[str, object],
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    payload = substitute(element.data_template, context).strip()
    if not payload:
        return

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=max(1, int(element.box_size)),
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    target_size = min(w, h)
    if target_size <= 0:
        return
    qr_image = qr_image.resize((target_size, target_size))

    offset_x = _text_align_x(element.align, x, w, target_size)
    offset_y = y + max(0, (h - target_size) // 2)
    canvas.paste(qr_image, (offset_x, offset_y))


def _render_divider_element(
    draw: ImageDraw.ImageDraw,
    element: ReceiptCanvasElement,
    context: dict[str, object],
    x: int,
    y: int,
    w: int,
    h: int,
    scale: float,
) -> None:
    """구분선 렌더링 (solid / dashed / dotted, 중앙 텍스트 지원)"""
    center_y = y + h // 2
    style = element.line_style
    thickness = max(1, int(element.line_thickness * scale))
    text = substitute(element.text_template, context).strip() if element.text_template else ""

    # 텍스트가 있으면 중앙 영역을 비우고 좌우에 선을 그림
    gap_left = x
    gap_right = x + w
    if text:
        font = _load_font(max(8, int(element.font_size * scale)), bold=element.bold)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        margin = 6
        text_x = x + (w - text_w) // 2
        text_y = center_y - text_h // 2
        draw.text((text_x, text_y), text, fill="black", font=font)
        gap_left = text_x - margin
        gap_right = text_x + text_w + margin

    def _draw_line_segment(x1: int, x2: int) -> None:
        if x2 <= x1:
            return
        if style == "dashed":
            cx = x1
            while cx < x2:
                draw.line([(cx, center_y), (min(cx + 8, x2), center_y)], fill="black", width=thickness)
                cx += 12
        elif style == "dotted":
            cx = x1
            while cx < x2:
                draw.line([(cx, center_y), (min(cx + 2, x2), center_y)], fill="black", width=thickness)
                cx += 6
        else:
            draw.line([(x1, center_y), (x2, center_y)], fill="black", width=thickness)

    if text:
        _draw_line_segment(x, gap_left)
        _draw_line_segment(gap_right, x + w)
    else:
        _draw_line_segment(x, x + w)
