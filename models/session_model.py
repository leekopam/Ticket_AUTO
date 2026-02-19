"""
Session backup model.

This file stores the latest PHPSESSID for backup/diagnostics only.
Runtime authentication source is BrowserService (Playwright persistent context).
"""
import os


class SessionModel:
    """Session backup model."""

    def __init__(self, session_file: str = "session.txt"):
        self._session_file = session_file
        self._php_session_id: str = ""
        self._load_session()

    def _load_session(self) -> None:
        """Load last known PHPSESSID from file if it exists."""
        if not os.path.exists(self._session_file):
            self._php_session_id = ""
            return

        with open(self._session_file, "r", encoding="utf-8") as f:
            self._php_session_id = f.read().strip()

    def save_session(self, session_id: str) -> None:
        """Save latest PHPSESSID for backup/diagnostics."""
        self._php_session_id = session_id.strip()
        with open(self._session_file, "w", encoding="utf-8") as f:
            f.write(self._php_session_id)

    @property
    def php_session_id(self) -> str:
        """Return cached backup session id."""
        return self._php_session_id

    @property
    def is_valid(self) -> bool:
        """Whether a backup value exists."""
        return bool(self._php_session_id)
