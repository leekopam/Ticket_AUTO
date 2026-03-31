"""Isolated debug-tool settings for ticket-check testing helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TicketDebugSettings:
    """Debug-only flags kept separate from normal receipt settings."""

    count_scan_success_as_processed: bool = False
    play_sound_for_duplicate_received_qr: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "count_scan_success_as_processed": bool(self.count_scan_success_as_processed),
            "play_sound_for_duplicate_received_qr": bool(self.play_sound_for_duplicate_received_qr),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "TicketDebugSettings":
        data = payload or {}
        return cls(
            count_scan_success_as_processed=bool(data.get("count_scan_success_as_processed", False)),
            play_sound_for_duplicate_received_qr=bool(data.get("play_sound_for_duplicate_received_qr", False)),
        )
