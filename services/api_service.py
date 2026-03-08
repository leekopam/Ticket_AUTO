"""QR 리다이렉트 파싱 서비스.

이 서비스는 인증/네트워크를 수행하지 않고,
브라우저 계층이 전달한 응답 정보만 해석한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import parse_qs, unquote, urlparse


REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
ORDER_NUMBER_PATTERN = re.compile(r"[A-Z0-9]{4,}_[A-Z0-9]{4,}")


@dataclass
class QRParseResult:
    success: bool
    order_number: str = ""
    full_url: str = ""
    error_code: str = ""
    error_message: str = ""


class ApiService:
    """브라우저 응답 파싱 전용 서비스.

    인증 상태/세션 쿠키는 보관하지 않는다.
    """
    WITCHFORM_BASE = "https://witchform.com"
    QR_PREFIX = "https://witchform.com/qrcode_link.php"

    @staticmethod
    def _extract_order_number(path: str, query: str, fragment: str = "") -> str:
        decoded_path = unquote(path or "")
        decoded_query = unquote(query or "")
        decoded_fragment = unquote(fragment or "")
        path_parts = [part.strip() for part in decoded_path.split("/") if part.strip()]
        if not path_parts:
            path_parts = []

        last_part = path_parts[-1] if path_parts else ""
        query_params = parse_qs(decoded_query)
        idx_value = query_params.get("idx", [""])[0].strip()
        uuid_value = query_params.get("uuid", [""])[0].strip()

        # Newer Witchform links sometimes already include the full order number
        # in the trailing path segment, e.g. WFLM7QSDTC_69D53CU23685.
        if ORDER_NUMBER_PATTERN.fullmatch(last_part):
            return last_part

        # Some Witchform QR redirects split the order number across the path
        # and query string, e.g. /.../69D1IASG67A8?uuid=WFLM7QSDTC.
        if uuid_value and last_part:
            composite_order_number = f"{uuid_value}_{last_part}"
            if ORDER_NUMBER_PATTERN.fullmatch(composite_order_number):
                return composite_order_number

        if idx_value and last_part:
            return f"{idx_value}_{last_part}"

        prioritized_keys = (
            "order_number",
            "ordernumber",
            "order_no",
            "orderno",
            "order",
            "no",
        )
        for key in prioritized_keys:
            for value in query_params.get(key, []):
                candidate = str(value).strip()
                if ORDER_NUMBER_PATTERN.fullmatch(candidate):
                    return candidate

        direct_candidates = path_parts + [
            str(value).strip()
            for values in query_params.values()
            for value in values
        ]
        for candidate in direct_candidates:
            if ORDER_NUMBER_PATTERN.fullmatch(candidate):
                return candidate

        for text in (decoded_path, decoded_query, decoded_fragment):
            match = ORDER_NUMBER_PATTERN.search(text)
            if match:
                return match.group(0)

        return ""

    def parse_qr_redirect(self, qr_url: str, status_code: int, location: str) -> QRParseResult:
        """리다이렉트 응답에서 주문번호를 추출한다."""
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

            order_number = self._extract_order_number(path, parsed.query, parsed.fragment)
            if not order_number:
                return QRParseResult(
                    success=False,
                    error_code="ORDER_NUMBER_MISSING",
                    error_message="주문번호를 QR 리다이렉트에서 찾을 수 없습니다.",
                )
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
