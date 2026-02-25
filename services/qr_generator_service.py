"""QR 코드 페이로드 빌드 및 이미지 생성 서비스."""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from enum import Enum

from PIL import Image
import qrcode


class QrType(Enum):
    """QR 코드 유형."""
    URL = "url"
    WIFI = "wifi"
    TEXT = "text"


@dataclass(frozen=True)
class QrConfig:
    """QR 생성에 필요한 통합 입력 필드."""
    qr_type: QrType
    # URL
    url: str = ""
    # Wi-Fi
    ssid: str = ""
    password: str = ""
    encryption: str = "WPA"  # WPA | WPA2 | WEP | nopass
    # 안내문
    text: str = ""


def build_payload(config: QrConfig) -> str:
    """QR 타입에 따라 페이로드 문자열을 생성한다.

    Raises:
        ValueError: 필수 입력값이 비어있는 경우
    """
    if config.qr_type == QrType.URL:
        raw = config.url.strip()
        if not raw:
            raise ValueError("URL을 입력해주세요.")
        if not raw.startswith(("http://", "https://")):
            raw = "https://" + raw
        return raw

    if config.qr_type == QrType.WIFI:
        ssid = config.ssid.strip()
        if not ssid:
            raise ValueError("SSID를 입력해주세요.")
        enc = config.encryption if config.encryption != "없음" else "nopass"
        # ZXing Wi-Fi 표준 포맷 (특수문자 이스케이프)
        escaped_ssid = _escape_wifi_field(ssid)
        escaped_pw = _escape_wifi_field(config.password)
        return f"WIFI:T:{enc};S:{escaped_ssid};P:{escaped_pw};;"

    if config.qr_type == QrType.TEXT:
        text = config.text.strip()
        if not text:
            raise ValueError("안내문 내용을 입력해주세요.")
        return text

    raise ValueError(f"지원하지 않는 QR 타입: {config.qr_type}")


def _escape_wifi_field(value: str) -> str:
    """Wi-Fi QR 특수문자 이스케이프 (ZXing 표준)."""
    result = value
    for ch in ('\\', '"', ';', ',', ':'):
        result = result.replace(ch, f'\\{ch}')
    return result


def calculate_qr_native_size(payload: str, box_size: int = 4, border: int = 1) -> int:
    """페이로드와 box_size로 QR 네이티브 픽셀 크기를 계산한다."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=max(1, box_size),
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    modules = qr.modules_count
    return (modules + 2 * border) * max(1, box_size)


def generate_qr_image(payload: str, output_px: int = 300) -> Image.Image:
    """페이로드로 QR 코드 PIL 이미지를 생성한다."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img.resize((output_px, output_px), Image.LANCZOS)


def image_to_base64(image: Image.Image) -> str:
    """PIL 이미지를 base64 문자열로 변환 (Flet ft.Image(src_base64=) 용)."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def save_qr_image(image: Image.Image, path: str) -> None:
    """QR 이미지를 PNG 파일로 저장한다."""
    image.save(path, format="PNG")
