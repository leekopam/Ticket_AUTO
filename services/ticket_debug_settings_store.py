"""Persistence for isolated ticket-check debug settings."""
from __future__ import annotations

import json

from models.ticket_debug_settings_model import TicketDebugSettings
from project_paths import resolve_project_path


class TicketDebugSettingsStore:
    """Load/save debug-only settings without mixing them into core settings."""

    def __init__(self, path: str = ".runtime/ticket_debug_settings.json"):
        self._path = resolve_project_path(path)

    def load(self) -> TicketDebugSettings:
        if not self._path.exists():
            return TicketDebugSettings()

        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return TicketDebugSettings()

        if not isinstance(payload, dict):
            return TicketDebugSettings()
        return TicketDebugSettings.from_dict(payload)

    def save(self, settings: TicketDebugSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
