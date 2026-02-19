"""
QR redirect parsing service.

This service no longer performs authenticated HTTP requests directly.
The browser service is responsible for network/auth state.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


@dataclass
class QRParseResult:
    success: bool
    order_number: str = ""
    full_url: str = ""
    error_code: str = ""
    error_message: str = ""


class ApiService:
    WITCHFORM_BASE = "https://witchform.com"
    QR_PREFIX = "https://witchform.com/qrcode_link.php"

    def __init__(self, php_session_id: str = ""):
        # kept for backward compatibility; runtime auth source is browser context.
        self._php_session_id = php_session_id

    def set_session(self, session_id: str) -> None:
        # kept for backward compatibility.
        self._php_session_id = session_id

    def parse_qr_redirect(self, qr_url: str, status_code: int, location: str) -> QRParseResult:
        """Parse redirect result and extract order info."""
        if not qr_url.startswith(self.QR_PREFIX):
            return QRParseResult(
                success=False,
                error_code="QR_INVALID_PREFIX",
                error_message="유효한 Witchform QR 코드가 아닙니다.",
            )

        if status_code in REDIRECT_STATUS_CODES:
            if not location:
                return QRParseResult(
                    success=False,
                    error_code="REDIRECT_MISSING",
                    error_message="리다이렉트 응답에 Location 헤더가 없습니다.",
                )

            parsed = urlparse(location)
            path = parsed.path or ""

            if path.startswith("/w/login"):
                return QRParseResult(
                    success=False,
                    error_code="AUTH_REQUIRED",
                    error_message="로그인이 필요합니다.",
                )

            if not path.startswith("/w/myform/sellForm-history-detail"):
                return QRParseResult(
                    success=False,
                    error_code="ORDER_PATH_INVALID",
                    error_message="주문 상세 경로가 아닙니다.",
                )

            path_parts = [part for part in path.split("/") if part]
            if not path_parts:
                return QRParseResult(
                    success=False,
                    error_code="ORDER_PATH_INVALID",
                    error_message="주문 경로가 올바르지 않습니다.",
                )

            last_part = path_parts[-1]
            query_params = parse_qs(parsed.query)
            uuid = query_params.get("idx", [""])[0].strip()

            if not uuid:
                return QRParseResult(
                    success=False,
                    error_code="ORDER_IDX_MISSING",
                    error_message="주문 식별자(idx)가 누락되었습니다.",
                )

            order_number = f"{uuid}_{last_part}"
            full_url = (
                f"{self.WITCHFORM_BASE}{location}"
                if location.startswith("/")
                else location
            )

            print(f"Order Number: {order_number}")
            return QRParseResult(
                success=True,
                order_number=order_number,
                full_url=full_url,
            )

        if status_code == 200:
            return QRParseResult(
                success=False,
                error_code="REDIRECT_MISSING",
                error_message="리다이렉트 응답이 아닙니다. 로그인 상태를 확인하세요.",
            )

        return QRParseResult(
            success=False,
            error_code="HTTP_STATUS_INVALID",
            error_message=f"예상하지 못한 응답 코드입니다: {status_code}",
        )

    def parse_qr_url(self, url: str) -> QRParseResult:
        """Legacy API. Kept to avoid hard break of external callers."""
        return QRParseResult(
            success=False,
            error_code="LEGACY_METHOD_DISABLED",
            error_message="parse_qr_redirect를 사용하세요.",
        )
