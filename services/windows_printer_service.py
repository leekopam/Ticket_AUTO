"""Windows printer listing and image print service."""
from __future__ import annotations

from PIL import Image, ImageWin

# PIL 1-bit packed → ESC/POS 래스터 비트 반전 테이블
# PIL: set bit(1)=white, ESC/POS: set bit(1)=black
_INVERT_TABLE = bytes(b ^ 0xFF for b in range(256))


class WindowsPrinterService:
    """Print bitmap receipts to any Windows-installed printer."""

    def _import_win32(self):
        try:
            import win32con  # type: ignore
            import win32print  # type: ignore
            import win32ui  # type: ignore
        except ModuleNotFoundError as exc:
            raise RuntimeError("pywin32 is required for Windows printing.") from exc
        return win32con, win32print, win32ui

    def list_printers(self) -> list[str]:
        _, win32print, _ = self._import_win32()
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = win32print.EnumPrinters(flags)
        names = [item[2] for item in printers if len(item) >= 3 and item[2]]
        return sorted(set(names))

    def get_default_printer(self) -> str:
        _, win32print, _ = self._import_win32()
        return win32print.GetDefaultPrinter()

    @staticmethod
    def _to_escpos_raster(image: Image.Image) -> bytes:
        """PIL 이미지를 ESC/POS GS v 0 래스터 명령으로 변환"""
        mono = image.convert("L").point(lambda x: 0 if x < 128 else 255, "1")
        width, height = mono.size
        width_bytes = (width + 7) // 8

        raw = mono.tobytes("raw", "1")
        inverted = raw.translate(_INVERT_TABLE)

        result = bytearray()
        chunk_size = 256

        for y_start in range(0, height, chunk_size):
            chunk_h = min(chunk_size, height - y_start)
            # GS v 0 m xL xH yL yH d1...dk
            result.extend(b'\x1d\x76\x30\x00')
            result.extend(bytes([width_bytes & 0xFF, (width_bytes >> 8) & 0xFF]))
            result.extend(bytes([chunk_h & 0xFF, (chunk_h >> 8) & 0xFF]))

            offset = y_start * width_bytes
            result.extend(inverted[offset:offset + chunk_h * width_bytes])

        return bytes(result)

    def _query_printable_width(self, target: str) -> int:
        """GDI로 프린터의 실제 인쇄 가능 폭(도트) 조회"""
        win32con, _, win32ui = self._import_win32()
        dc = win32ui.CreateDC()
        dc.CreatePrinterDC(target)
        width = dc.GetDeviceCaps(win32con.HORZRES)
        dc.DeleteDC()
        return width

    @staticmethod
    def _fit_to_printer_width(image: Image.Image, printer_width: int) -> Image.Image:
        """이미지를 프린터 인쇄 폭에 비례 맞춤"""
        if image.width == printer_width or printer_width <= 0:
            return image
        ratio = printer_width / image.width
        new_height = max(1, int(image.height * ratio))
        return image.resize((printer_width, new_height), Image.LANCZOS)

    def print_image(self, image: Image.Image, printer_name: str | None, job_name: str) -> None:
        """ESC/POS RAW 모드로 영수증 이미지 인쇄 (여백 공백 보존)

        프린터 드라이버에서 실제 인쇄 폭을 조회한 뒤 이미지를 맞추고,
        ESC/POS 래스터 명령으로 직접 전송하여 빈 행도 용지 이송으로 처리한다.
        ESC/POS 실패 시 GDI 폴백.
        """
        _, win32print, _ = self._import_win32()
        target = printer_name or self.get_default_printer()

        # 프린터 실제 인쇄 폭 조회 후 이미지 리사이즈
        printer_width = self._query_printable_width(target)
        fitted = self._fit_to_printer_width(image.convert("RGB"), printer_width)

        try:
            self._print_escpos(fitted, target, job_name, win32print)
        except Exception:
            self._print_gdi(image, target, job_name)

    def _print_escpos(
        self,
        image: Image.Image,
        target: str,
        job_name: str,
        win32print,
    ) -> None:
        """ESC/POS RAW 래스터 인쇄"""
        raster_data = self._to_escpos_raster(image)

        handle = win32print.OpenPrinter(target)
        try:
            win32print.StartDocPrinter(handle, 1, (job_name, None, "RAW"))
            win32print.StartPagePrinter(handle)

            # 프린터 초기화
            win32print.WritePrinter(handle, b'\x1b\x40')
            # 래스터 이미지 전송
            win32print.WritePrinter(handle, raster_data)

            win32print.EndPagePrinter(handle)
            win32print.EndDocPrinter(handle)
        finally:
            win32print.ClosePrinter(handle)

    def _print_gdi(self, image: Image.Image, target: str, job_name: str) -> None:
        """GDI 폴백 인쇄 (ESC/POS 미지원 프린터용)"""
        win32con, _, win32ui = self._import_win32()

        dc = win32ui.CreateDC()
        dc.CreatePrinterDC(target)
        printable_width = dc.GetDeviceCaps(win32con.HORZRES)
        printable_height = dc.GetDeviceCaps(win32con.VERTRES)

        rendered = image.convert("RGB")
        scale = printable_width / max(1, rendered.width)
        page_height_unscaled = max(1, int(printable_height / max(scale, 1e-6)))

        dc.StartDoc(job_name)
        try:
            start_y = 0
            while start_y < rendered.height:
                end_y = min(rendered.height, start_y + page_height_unscaled)
                chunk = rendered.crop((0, start_y, rendered.width, end_y))
                scaled_chunk_height = max(1, int(chunk.height * scale))

                dib = ImageWin.Dib(chunk)
                dc.StartPage()
                dib.draw(dc.GetHandleOutput(), (0, 0, printable_width, scaled_chunk_height))
                dc.EndPage()
                start_y = end_y
        finally:
            dc.EndDoc()
            dc.DeleteDC()
