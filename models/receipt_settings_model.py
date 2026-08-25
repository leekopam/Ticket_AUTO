"""Receipt printing settings model."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any, Literal

from project_paths import (
    RESOURCE_PRODUCT_TEMPLATE_FILE,
    RESOURCE_RECEIPT_TEMPLATE_FILE,
)


PaperWidth = Literal["58", "80"]
PrinterDpi = Literal[180, 203, 300]
ScannerFocusMode = Literal["auto", "manual"]
ScanSuccessSoundTriggerType = Literal["always", "every_n", "specific_counts"]
VALID_DPI: set[int] = {180, 203, 300}
DEFAULT_RECEIPT_TEMPLATE_PATH = RESOURCE_RECEIPT_TEMPLATE_FILE.as_posix()
DEFAULT_PRODUCT_TEMPLATE_PATH = RESOURCE_PRODUCT_TEMPLATE_FILE.as_posix()


def _normalize_template_path(value: object, default_path: str) -> str:
    raw = str(value or "").strip() or default_path
    path = Path(raw)
    if path.parts and path.parts[0] == "templates":
        return (RESOURCE_RECEIPT_TEMPLATE_FILE.parent / Path(*path.parts[1:])).as_posix()
    return raw


def _is_removed_sound_trigger_type(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"rare_percent", "order_number_equals"}


def _coerce_sound_trigger_type(value: object) -> ScanSuccessSoundTriggerType:
    normalized = str(value or "").strip().lower()
    if normalized == "every_n":
        return "every_n"
    if normalized == "specific_counts":
        return "specific_counts"
    return "always"


@dataclass
class ScanSuccessSoundRule:
    """Conditional sound rule for QR scan success feedback."""

    name: str = ""
    sound_path: str = ""
    enabled: bool = True
    weight: float = 1.0
    trigger_type: ScanSuccessSoundTriggerType = "always"
    trigger_value: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sound_path": self.sound_path,
            "enabled": self.enabled,
            "weight": self.weight,
            "trigger_type": self.trigger_type,
            "trigger_value": self.trigger_value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ScanSuccessSoundRule":
        data = payload or {}
        weight_raw = data.get("weight", 1)
        try:
            weight = max(0.0, float(weight_raw))
        except (TypeError, ValueError):
            weight = 1.0

        return cls(
            name=str(data.get("name", "")).strip(),
            sound_path=str(data.get("sound_path", "")).strip(),
            enabled=bool(data.get("enabled", True)),
            weight=weight,
            trigger_type=_coerce_sound_trigger_type(data.get("trigger_type", "always")),
            trigger_value=str(data.get("trigger_value", "")).strip(),
        )


@dataclass
class ReceiptSettings:
    """User-configurable receipt output settings."""

    printer_name: str = ""
    paper_width: PaperWidth = "80"
    show_qr: bool = True
    show_logo: bool = False
    # Supports both legacy .tpl and new canvas .json templates.
    template_path: str = DEFAULT_RECEIPT_TEMPLATE_PATH
    product_template_path: str = DEFAULT_PRODUCT_TEMPLATE_PATH
    logo_path: str = ""
    event_title: str = ""
    qr_payload_template: str = "{{order_number}}|{{url}}"
    margin_top: int = 0
    margin_bottom: int = 0
    printer_dpi: int = 300
    print_product_receipt: bool = False
    qr_scan_auto_print_enabled: bool = True
    ticket_product_names: list[str] = field(default_factory=list)
    qr_scan_success_sound_path: str = ""
    qr_scan_success_sound_rules: list[ScanSuccessSoundRule] = field(default_factory=list)
    camera_index: int = 0
    scanner_focus_mode: ScannerFocusMode = "auto"
    scanner_manual_focus_value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "printer_name": self.printer_name,
            "paper_width": self.paper_width,
            "show_qr": self.show_qr,
            "show_logo": self.show_logo,
            "template_path": self.template_path,
            "product_template_path": self.product_template_path,
            "logo_path": self.logo_path,
            "event_title": self.event_title,
            "qr_payload_template": self.qr_payload_template,
            "margin_top": self.margin_top,
            "margin_bottom": self.margin_bottom,
            "printer_dpi": self.printer_dpi,
            "print_product_receipt": self.print_product_receipt,
            "qr_scan_auto_print_enabled": self.qr_scan_auto_print_enabled,
            "ticket_product_names": self.ticket_product_names,
            "qr_scan_success_sound_path": self.qr_scan_success_sound_path,
            "qr_scan_success_sound_rules": [rule.to_dict() for rule in self.qr_scan_success_sound_rules],
            "camera_index": self.camera_index,
            "scanner_focus_mode": self.scanner_focus_mode,
            "scanner_manual_focus_value": self.scanner_manual_focus_value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ReceiptSettings":
        data = payload or {}
        paper_width_raw = str(data.get("paper_width", "80")).strip()
        paper_width: PaperWidth = "58" if paper_width_raw == "58" else "80"

        dpi_raw = int(data.get("printer_dpi", 300))
        printer_dpi = dpi_raw if dpi_raw in VALID_DPI else 203
        focus_mode_raw = str(data.get("scanner_focus_mode", "auto")).strip().lower()
        scanner_focus_mode: ScannerFocusMode = "manual" if focus_mode_raw == "manual" else "auto"
        manual_focus_raw = data.get("scanner_manual_focus_value", None)
        try:
            scanner_manual_focus_value = None if manual_focus_raw in ("", None) else float(manual_focus_raw)
        except (TypeError, ValueError):
            scanner_manual_focus_value = None
        if scanner_manual_focus_value is not None and not math.isfinite(scanner_manual_focus_value):
            scanner_manual_focus_value = None
        if scanner_focus_mode == "manual" and scanner_manual_focus_value is None:
            scanner_focus_mode = "auto"
        sound_rules_raw = data.get("qr_scan_success_sound_rules", [])
        if isinstance(sound_rules_raw, list):
            sound_rules = []
            for item in sound_rules_raw:
                if not isinstance(item, dict):
                    continue
                if _is_removed_sound_trigger_type(item.get("trigger_type", "")):
                    continue
                sound_rules.append(ScanSuccessSoundRule.from_dict(item))
        else:
            sound_rules = []

        return cls(
            printer_name=str(data.get("printer_name", "")).strip(),
            paper_width=paper_width,
            show_qr=bool(data.get("show_qr", True)),
            show_logo=bool(data.get("show_logo", False)),
            template_path=_normalize_template_path(
                data.get("template_path", DEFAULT_RECEIPT_TEMPLATE_PATH),
                DEFAULT_RECEIPT_TEMPLATE_PATH,
            ),
            product_template_path=_normalize_template_path(
                data.get("product_template_path", DEFAULT_PRODUCT_TEMPLATE_PATH),
                DEFAULT_PRODUCT_TEMPLATE_PATH,
            ),
            logo_path=str(data.get("logo_path", "")).strip(),
            event_title=str(data.get("event_title", "")).strip(),
            qr_payload_template=str(data.get("qr_payload_template", "{{order_number}}|{{url}}")).strip()
            or "{{order_number}}|{{url}}",
            margin_top=max(0, int(data.get("margin_top", 0))),
            margin_bottom=max(0, int(data.get("margin_bottom", 0))),
            printer_dpi=printer_dpi,
            print_product_receipt=bool(data.get("print_product_receipt", False)),
            qr_scan_auto_print_enabled=bool(data.get("qr_scan_auto_print_enabled", True)),
            ticket_product_names=list(data.get("ticket_product_names", [])),
            qr_scan_success_sound_path=str(data.get("qr_scan_success_sound_path", "")).strip(),
            qr_scan_success_sound_rules=sound_rules,
            camera_index=max(0, int(data.get("camera_index", 0))),
            scanner_focus_mode=scanner_focus_mode,
            scanner_manual_focus_value=scanner_manual_focus_value,
        )
