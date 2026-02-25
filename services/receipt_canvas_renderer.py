"""Renderer for JSON canvas receipt layout."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import qrcode

from models.receipt_canvas_model import ReceiptCanvasDocument, ReceiptCanvasElement, paper_width_to_px
from services.receipt_template import substitute


# 폰트 매핑: key → (regular_path, bold_path)
FONT_MAP: dict[str, tuple[str, str]] = {
    "malgun":      (r"C:\Windows\Fonts\malgun.ttf",      r"C:\Windows\Fonts\malgunbd.ttf"),
    "gulim":       (r"C:\Windows\Fonts\gulim.ttc",       r"C:\Windows\Fonts\gulim.ttc"),
    "batang":      (r"C:\Windows\Fonts\batang.ttc",      r"C:\Windows\Fonts\batang.ttc"),
    "nanumgothic": (r"C:\Windows\Fonts\NanumGothic.ttf", r"C:\Windows\Fonts\NanumGothic.ttf"),
    "arial":       (r"C:\Windows\Fonts\arial.ttf",       r"C:\Windows\Fonts\arialbd.ttf"),
    "times":       (r"C:\Windows\Fonts\times.ttf",       r"C:\Windows\Fonts\timesbd.ttf"),
    "calibri":     (r"C:\Windows\Fonts\calibri.ttf",     r"C:\Windows\Fonts\calibrib.ttf"),
    "comic":       (r"C:\Windows\Fonts\comic.ttf",       r"C:\Windows\Fonts\comicbd.ttf"),
    "georgia":     (r"C:\Windows\Fonts\georgia.ttf",     r"C:\Windows\Fonts\georgiab.ttf"),
    "verdana":     (r"C:\Windows\Fonts\verdana.ttf",     r"C:\Windows\Fonts\verdanab.ttf"),
    "consolas":    (r"C:\Windows\Fonts\consola.ttf",     r"C:\Windows\Fonts\consolab.ttf"),
    "impact":      (r"C:\Windows\Fonts\impact.ttf",      r"C:\Windows\Fonts\impact.ttf"),
}


def _load_font(size: int, *, bold: bool = False, family: str = "malgun"):
    """지정된 폰트 패밀리에서 폰트를 로드 (폴백: malgun → gulim → default)"""
    safe_size = max(8, int(size))
    paths = FONT_MAP.get(family, FONT_MAP["malgun"])
    candidate = paths[1] if bold else paths[0]
    if Path(candidate).exists():
        try:
            return ImageFont.truetype(candidate, safe_size)
        except Exception:
            pass
    # 폴백: malgun → gulim
    for fallback_key in ("malgun", "gulim"):
        fb_paths = FONT_MAP[fallback_key]
        fb_candidate = fb_paths[1] if bold else fb_paths[0]
        if Path(fb_candidate).exists():
            try:
                return ImageFont.truetype(fb_candidate, safe_size)
            except Exception:
                continue
    return ImageFont.load_default()



def _text_align_x(align: str, box_x: int, box_w: int, text_w: int) -> int:
    if align == "center":
        return box_x + max(0, (box_w - text_w) // 2)
    if align == "right":
        return box_x + max(0, box_w - text_w)
    return box_x


def _is_element_visible(
    element: ReceiptCanvasElement,
    context: dict[str, object],
) -> bool:
    """요소 가시성 판정 (visible 플래그 + visibility_tag + 텍스트 치환 결과)"""
    if not element.visible:
        return False
    # visibility_tag 연결 필드가 비어있으면 숨김
    if element.visibility_tag:
        tag_value = context.get(element.visibility_tag, "")
        if not str(tag_value).strip():
            return False
    # 텍스트 요소: 치환 결과가 비면 숨김
    if element.type == "text" and element.text_template:
        resolved = substitute(element.text_template, context).strip()
        if not resolved:
            return False
    return True


def _calc_text_render_height(
    element: ReceiptCanvasElement,
    context: dict[str, object],
    w: int,
    scale: float,
) -> int:
    """텍스트 요소의 실제 렌더링 높이를 계산 (줄바꿈 반영)"""
    text = substitute(element.text_template, context)
    font = _load_font(
        max(8, int(element.font_size * scale)),
        bold=element.bold,
        family=element.font_family,
    )
    lines = str(text).split("\n")
    line_gap = max(2, int(4 * scale))
    # 임시 이미지로 텍스트 크기 측정
    tmp = Image.new("RGB", (1, 1), "white")
    draw = ImageDraw.Draw(tmp)
    total_h = 0
    for line in lines:
        content = line if line else " "
        box = draw.textbbox((0, 0), content, font=font)
        total_h += box[3] - box[1]
    if lines:
        total_h += line_gap * (len(lines) - 1)
    return total_h


Band = list[ReceiptCanvasElement]


def _group_into_bands(elements: list[ReceiptCanvasElement]) -> list[Band]:
    """Y범위가 겹치는 요소들을 하나의 밴드로 그룹핑"""
    if not elements:
        return []
    sorted_elems = sorted(elements, key=lambda e: e.y)
    bands: list[Band] = []
    current_band: Band = [sorted_elems[0]]
    band_bottom = sorted_elems[0].y + sorted_elems[0].h
    for elem in sorted_elems[1:]:
        if elem.y < band_bottom:
            # Y범위 겹침 → 같은 밴드
            current_band.append(elem)
            band_bottom = max(band_bottom, elem.y + elem.h)
        else:
            bands.append(current_band)
            current_band = [elem]
            band_bottom = elem.y + elem.h
    bands.append(current_band)
    return bands


def render_canvas_layout(
    doc: ReceiptCanvasDocument,
    context: dict[str, object],
    paper_width: str,
    margin_top: int = 0,
    margin_bottom: int = 0,
    dpi: int = 203,
) -> Image.Image:
    """밴드 기반 동적 레이아웃으로 영수증 렌더링"""
    width = paper_width_to_px(paper_width, dpi=dpi)
    base_width = max(1, int(doc.meta.canvas_width_px))
    scale = width / base_width
    all_elements = [e for e in doc.elements if e.visible]
    if not all_elements:
        out_h = max(1, int(max(180, doc.meta.canvas_height_px) * scale))
        return Image.new("RGB", (width, out_h), "white")

    # 밴드 그룹핑 (원본 좌표 기준)
    bands = _group_into_bands(all_elements)

    # 밴드별 갭 계산 (인접 밴드 간 원본 간격 보존)
    band_gaps: list[int] = [0]  # 첫 밴드 앞 갭 없음
    for i in range(1, len(bands)):
        prev_bottom = max(e.y + e.h for e in bands[i - 1])
        curr_top = min(e.y for e in bands[i])
        band_gaps.append(max(0, curr_top - prev_bottom))

    # 첫 밴드의 원본 시작 Y
    first_band_top = min(e.y for e in bands[0])
    mt = max(0, int(margin_top * scale))

    # 밴드별 가시성 필터 + 동적 높이 계산 (상단 여백 적용)
    cursor_y = mt + int(first_band_top * scale)
    render_list: list[tuple[ReceiptCanvasElement, int, int, int, int]] = []  # (elem, x, y, w, h)

    for band_idx, band in enumerate(bands):
        visible_elems = [
            e for e in band
            if _is_element_visible(e, context)
        ]
        if not visible_elems:
            # 빈 밴드 → 공간 0, 갭도 건너뜀
            continue

        # 이전 밴드와의 갭 적용
        cursor_y += int(band_gaps[band_idx] * scale)

        band_orig_top = min(e.y for e in band)
        band_render_height = 0

        for elem in visible_elems:
            # X좌표는 원본 유지
            ex = int(elem.x * scale)
            ew = max(1, int(elem.w * scale))
            # 밴드 내 상대 Y 오프셋
            rel_y = int((elem.y - band_orig_top) * scale)
            ey = cursor_y + rel_y

            eh = max(1, int(elem.h * scale))
            # 텍스트 요소: 실제 내용에 따라 높이 자동 확장
            if elem.type == "text":
                actual_h = _calc_text_render_height(elem, context, ew, scale)
                if actual_h > eh:
                    eh = actual_h

            render_list.append((elem, ex, ey, ew, eh))
            elem_bottom = rel_y + eh
            if elem_bottom > band_render_height:
                band_render_height = elem_bottom

        cursor_y += band_render_height

    # 총 높이 계산
    if render_list:
        total_bottom = max(ey + eh for _, _, ey, _, eh in render_list)
    else:
        total_bottom = int(doc.meta.canvas_height_px * scale)
    mb = max(0, int(margin_bottom * scale))
    out_height = max(1, total_bottom + mb)

    image = Image.new("RGB", (width, out_height), "white")
    draw = ImageDraw.Draw(image)

    for elem, x, y, w, h in render_list:
        if elem.type == "text":
            _render_text_element(draw, elem, context, x, y, w, h, scale)
        elif elem.type == "image":
            _render_image_element(image, elem, x, y, w, h)
        elif elem.type == "qr":
            _render_qr_element(image, elem, context, x, y, w, h)
        elif elem.type == "divider":
            _render_divider_element(draw, elem, context, x, y, w, h, scale)

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
    font = _load_font(max(8, int(element.font_size * scale)), bold=element.bold, family=element.font_family)

    lines = str(text).split("\n")
    line_gap = max(2, int(4 * scale))

    # 전체 텍스트 높이 계산 (수직 중앙 정렬용)
    line_metrics: list[tuple[str, int, int]] = []  # (content, text_w, text_h)
    total_text_h = 0
    for line in lines:
        content = line if line else " "
        box = draw.textbbox((0, 0), content, font=font)
        tw = box[2] - box[0]
        th = box[3] - box[1]
        line_metrics.append((content, tw, th))
        total_text_h += th
    if line_metrics:
        total_text_h += line_gap * (len(line_metrics) - 1)

    # 수직 중앙 정렬
    cursor_y = y + max(0, (h - total_text_h) // 2)

    for content, text_w, text_h in line_metrics:
        if cursor_y + text_h > y + h:
            break
        text_x = _text_align_x(element.align, x, w, text_w)
        draw.text((text_x, cursor_y), content, fill=(0, 0, 0), font=font)
        cursor_y += text_h + line_gap


def _load_image_source(element: ReceiptCanvasElement) -> Image.Image | None:
    """asset_path 또는 embedded_data에서 이미지 로드"""
    # 1) 파일 경로가 존재하면 파일에서 로드
    if element.asset_path:
        path = Path(element.asset_path)
        if path.exists():
            with Image.open(path) as source:
                return source.convert("RGBA")
    # 2) embedded_data(base64)가 있으면 메모리에서 로드
    if element.embedded_data:
        try:
            data_str = element.embedded_data
            if data_str.startswith("data:"):
                _, b64_data = data_str.split(",", 1)
            else:
                b64_data = data_str
            raw = base64.b64decode(b64_data)
            return Image.open(io.BytesIO(raw)).convert("RGBA")
        except Exception:
            return None
    return None


def _render_image_element(
    canvas: Image.Image,
    element: ReceiptCanvasElement,
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    img = _load_image_source(element)
    if img is None:
        return

    img = img.resize((w, h))

    offset_x = _text_align_x(element.align, x, w, w)
    canvas.paste(img, (offset_x, y), img)


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

    box_sz = max(1, int(element.box_size))
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_sz,
        border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    # 요소 바운딩 박스에 맞춰 QR 이미지 리사이즈
    native_size = qr_image.size[0]
    target_size = min(w, h) if min(w, h) > 0 else native_size
    if target_size <= 0:
        return
    if native_size != target_size:
        qr_image = qr_image.resize((target_size, target_size), Image.LANCZOS)

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
    """구분선 렌더링 (solid / dashed / dotted / none, 중앙 텍스트 지원)"""
    center_y = y + h // 2
    style = element.line_style
    thickness = max(1, int(element.line_thickness * scale))
    text = substitute(element.text_template, context).strip() if element.text_template else ""

    # 텍스트가 있으면 중앙 영역을 비우고 좌우에 선을 그림
    gap_left = x
    gap_right = x + w
    if text:
        font = _load_font(max(8, int(element.font_size * scale)), bold=element.bold, family=element.font_family)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        margin = 6
        text_x = x + (w - text_w) // 2
        text_y = center_y - text_h // 2
        draw.text((text_x, text_y), text, fill="black", font=font)
        gap_left = text_x - margin
        gap_right = text_x + text_w + margin

    # 선 없음: 영역 높이만큼 여백 역할 (선을 그리지 않음)
    if style == "none":
        return

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
