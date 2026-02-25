"""Receipt printing settings model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


PaperWidth = Literal["58", "80"]
PrinterDpi = Literal[180, 203, 300]
VALID_DPI: set[int] = {180, 203, 300}


@dataclass
class ReceiptSettings:
    """User-configurable receipt output settings."""

    printer_name: str = ""
    paper_width: PaperWidth = "80"
    show_qr: bool = True
    show_logo: bool = False
    # Supports both legacy .tpl and new canvas .json templates.
    template_path: str = "templates/receipt_layout.json"
    logo_path: str = ""
    event_title: str = ""
    qr_payload_template: str = "{{order_number}}|{{url}}"
    margin_top: int = 0
    margin_bottom: int = 0
    printer_dpi: int = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "printer_name": self.printer_name,
            "paper_width": self.paper_width,
            "show_qr": self.show_qr,
            "show_logo": self.show_logo,
            "template_path": self.template_path,
            "logo_path": self.logo_path,
            "event_title": self.event_title,
            "qr_payload_template": self.qr_payload_template,
            "margin_top": self.margin_top,
            "margin_bottom": self.margin_bottom,
            "printer_dpi": self.printer_dpi,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ReceiptSettings":
        data = payload or {}
        paper_width_raw = str(data.get("paper_width", "80")).strip()
        paper_width: PaperWidth = "58" if paper_width_raw == "58" else "80"

        dpi_raw = int(data.get("printer_dpi", 300))
        printer_dpi = dpi_raw if dpi_raw in VALID_DPI else 203

        return cls(
            printer_name=str(data.get("printer_name", "")).strip(),
            paper_width=paper_width,
            show_qr=bool(data.get("show_qr", True)),
            show_logo=bool(data.get("show_logo", False)),
            template_path=str(data.get("template_path", "templates/receipt_layout.json")).strip()
            or "templates/receipt_layout.json",
            logo_path=str(data.get("logo_path", "")).strip(),
            event_title=str(data.get("event_title", "")).strip(),
            qr_payload_template=str(data.get("qr_payload_template", "{{order_number}}|{{url}}")).strip()
            or "{{order_number}}|{{url}}",
            margin_top=max(0, int(data.get("margin_top", 0))),
            margin_bottom=max(0, int(data.get("margin_bottom", 0))),
            printer_dpi=printer_dpi,
        )
