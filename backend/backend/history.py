from __future__ import annotations

import logging
import sqlite3

from backend.database import (
    create_session,
    end_session,
    list_sessions,
    log_extrusion_event,
    reopen_session,
)

logger = logging.getLogger(__name__)


class PrintHistory:
    """Records print sessions and their extrusion changes in the database."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._session_id: int | None = None
        # The session that ended last, so a resumed e-stop can reopen it
        self._last_session_id: int | None = None

    @property
    def session_id(self) -> int | None:
        return self._session_id

    def start(
        self,
        filename: str,
        syringe_config: str,
        total_lines: int,
        source: str | None,
        settings: dict[str, float | None],
    ) -> int:
        if self._session_id is not None:
            logger.warning("Session %d still open at new print start", self._session_id)
            self.end("stopped")
        self._last_session_id = None
        self._session_id = create_session(
            self._conn, filename, syringe_config, total_lines,
            source=source, settings=settings,
        )
        return self._session_id

    def list(self, limit: int) -> list[dict]:
        """Recent sessions, newest first, with their extrusion events."""
        return list_sessions(self._conn, limit)

    def log_extrusion(self, rate: int, lines_sent: int) -> None:
        if self._session_id is not None:
            log_extrusion_event(self._conn, self._session_id, rate, lines_sent)

    def end(self, end_reason: str, resume_line: int | None = None) -> None:
        if self._session_id is None:
            return
        end_session(self._conn, self._session_id, end_reason, resume_line)
        self._last_session_id = self._session_id
        self._session_id = None

    def reopen(self) -> None:
        """Continue the last session: its stopped print was resumed."""
        if self._session_id is not None or self._last_session_id is None:
            return
        reopen_session(self._conn, self._last_session_id)
        self._session_id = self._last_session_id
