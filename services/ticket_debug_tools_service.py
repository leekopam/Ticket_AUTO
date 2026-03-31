"""Helpers for ticket-check debug tools that must stay isolated from core settings."""
from __future__ import annotations

from models.ticket_debug_settings_model import TicketDebugSettings
from services.ticket_debug_settings_store import TicketDebugSettingsStore


class TicketDebugToolsService:
    """Query debug-only behavior flags from a dedicated store."""

    def __init__(self, settings_store: TicketDebugSettingsStore | None = None):
        self._settings_store = settings_store or TicketDebugSettingsStore()

    def load_settings(self) -> TicketDebugSettings:
        return self._settings_store.load()

    def save_settings(self, settings: TicketDebugSettings) -> None:
        self._settings_store.save(settings)

    def should_count_scan_success_as_processed(self, settings: TicketDebugSettings | None = None) -> bool:
        effective_settings = settings or self.load_settings()
        return bool(effective_settings.count_scan_success_as_processed)

    def should_play_sound_for_duplicate_received_qr(
        self,
        settings: TicketDebugSettings | None = None,
    ) -> bool:
        effective_settings = settings or self.load_settings()
        return bool(effective_settings.play_sound_for_duplicate_received_qr)
