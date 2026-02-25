"""QR 생성 서비스 유닛 테스트."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PIL import Image

from services.qr_generator_service import (
    QrConfig,
    QrType,
    build_payload,
    generate_qr_image,
    image_to_base64,
    save_qr_image,
)


# --- build_payload ---

class TestBuildPayloadUrl:
    def test_auto_https_prefix(self):
        config = QrConfig(qr_type=QrType.URL, url="example.com")
        assert build_payload(config) == "https://example.com"

    def test_existing_https_kept(self):
        config = QrConfig(qr_type=QrType.URL, url="https://example.com")
        assert build_payload(config) == "https://example.com"

    def test_existing_http_kept(self):
        config = QrConfig(qr_type=QrType.URL, url="http://example.com")
        assert build_payload(config) == "http://example.com"

    def test_empty_url_raises(self):
        config = QrConfig(qr_type=QrType.URL, url="  ")
        with pytest.raises(ValueError, match="URL"):
            build_payload(config)


class TestBuildPayloadWifi:
    def test_standard_format(self):
        config = QrConfig(qr_type=QrType.WIFI, ssid="MyNet", password="pass123", encryption="WPA")
        result = build_payload(config)
        assert result == "WIFI:T:WPA;S:MyNet;P:pass123;;"

    def test_special_chars_escaped(self):
        config = QrConfig(qr_type=QrType.WIFI, ssid="My;Net", password="p:ass", encryption="WPA2")
        result = build_payload(config)
        assert "My\\;Net" in result
        assert "p\\:ass" in result

    def test_no_encryption(self):
        config = QrConfig(qr_type=QrType.WIFI, ssid="Open", password="", encryption="없음")
        result = build_payload(config)
        assert "T:nopass" in result

    def test_empty_ssid_raises(self):
        config = QrConfig(qr_type=QrType.WIFI, ssid="", password="pw")
        with pytest.raises(ValueError, match="SSID"):
            build_payload(config)


class TestBuildPayloadText:
    def test_text_passthrough(self):
        config = QrConfig(qr_type=QrType.TEXT, text="안내문 테스트")
        assert build_payload(config) == "안내문 테스트"

    def test_empty_text_raises(self):
        config = QrConfig(qr_type=QrType.TEXT, text="")
        with pytest.raises(ValueError, match="안내문"):
            build_payload(config)


# --- generate_qr_image ---

class TestGenerateQrImage:
    def test_returns_pil_image(self):
        img = generate_qr_image("https://example.com")
        assert isinstance(img, Image.Image)

    def test_output_size(self):
        img = generate_qr_image("test", output_px=200)
        assert img.size == (200, 200)


# --- image_to_base64 ---

class TestImageToBase64:
    def test_returns_nonempty_string(self):
        img = Image.new("RGB", (10, 10), "white")
        b64 = image_to_base64(img)
        assert isinstance(b64, str)
        assert len(b64) > 0


# --- save_qr_image ---

class TestSaveQrImage:
    def test_saves_png(self):
        img = Image.new("RGB", (50, 50), "black")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        save_qr_image(img, path)
        assert Path(path).stat().st_size > 0
        Path(path).unlink(missing_ok=True)
