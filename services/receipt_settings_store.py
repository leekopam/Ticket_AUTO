"""Persistence for receipt settings."""
from __future__ import annotations

import json
import logging

from models.receipt_settings_model import ReceiptSettings
from project_paths import resolve_project_path

_logger = logging.getLogger(__name__)


class ReceiptSettingsStore:
    """Loads and saves settings without manual file editing."""

    def __init__(self, path: str = ".runtime/receipt_settings.json"):
        self._path = resolve_project_path(path)

    def load(self) -> ReceiptSettings:
        if not self._path.exists():
            return ReceiptSettings()

        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return ReceiptSettings()

        if not isinstance(payload, dict):
            return ReceiptSettings()
        return ReceiptSettings.from_dict(payload)

    def save(self, settings: ReceiptSettings) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(settings.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            _logger.warning("설정 파일 저장 실패 path=%s error=%s", self._path, exc)
