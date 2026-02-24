"""Windows printer listing and image print service."""
from __future__ import annotations

from PIL import Image, ImageWin


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

    def print_image(self, image: Image.Image, printer_name: str | None, job_name: str) -> None:
        win32con, _, win32ui = self._import_win32()
        target = printer_name or self.get_default_printer()

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
