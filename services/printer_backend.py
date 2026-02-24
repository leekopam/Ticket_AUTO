"""Printer backend contracts for future transport extension."""
from __future__ import annotations

from typing import Protocol

from PIL import Image


class PrinterBackend(Protocol):
    """Transport abstraction for receipt output."""

    def print_image(self, image: Image.Image, printer_name: str | None, job_name: str) -> None:
        ...


class EscPosSerialBackend:
    """Reserved for Bluetooth/COM ESC/POS transport."""

    def print_image(self, image: Image.Image, printer_name: str | None, job_name: str) -> None:
        raise NotImplementedError("EscPosSerialBackend is reserved for a future iteration.")
