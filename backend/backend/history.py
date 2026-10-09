from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING

from backend.database import (
    create_session,
    end_session,
    list_sessions,
    log_extrusion_event,
    reopen_session,
    set_serial_log,
)
from backend.logs import PrintTrafficLog

if TYPE_CHECKING:
    from backend.serial_manager import SerialLogEntry

logger = logging.getLogger(__name__)


class PrintHistory:
    """Records print sessions and their extrusion changes in the database,
    and each print's serial traffic in a file named in its session row."""

    def __init__(self, conn: sqlite3.Connection, traffic: PrintTrafficLog | None = None):
        self._conn = conn
        self._traffic = traffic or PrintTrafficLog()
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
        settings: Mapping[str, object],
        edited: bool = False,
    ) -> int:
        if self._session_id is not None:
            logger.warning("Session %d still open at new print start", self._session_id)
            self.end("stopped")
        self._last_session_id = None
        self._session_id = create_session(
            self._conn,
            filename,
            syringe_config,
            total_lines,
            source=source,
            settings=settings,
            edited=edited,
        )
        path = self._traffic.start(self._session_id)
        set_serial_log(self._conn, self._session_id, str(path))
        return self._session_id

    def log_traffic(self, entry: SerialLogEntry) -> None:
        """Serial traffic, written to the running print's file (if any)."""
        self._traffic.write(entry.timestamp, entry.direction, entry.content, entry.line_number)

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
        self._traffic.stop()
        self._last_session_id = self._session_id
        self._session_id = None

    def on_event(self, event: dict) -> None:
        """Follows the print through the app's event bus."""
        if event["type"] == "print_end":
            self.end(event["reason"], event["resume_line"])
        elif event["type"] == "print_resumed":
            self.reopen()

    def reopen(self) -> None:
        """Continue the last session: its stopped print was resumed."""
        if self._session_id is not None or self._last_session_id is None:
            return
        reopen_session(self._conn, self._last_session_id)
        self._session_id = self._last_session_id
        self.resuming()

    def resuming(self) -> None:
        """A stopped print is about to be resumed: its return moves already
        belong to its serial traffic."""
        session_id = self._session_id or self._last_session_id
        if session_id is None:
            return
        row = self._conn.execute("SELECT serial_log FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row and row[0]:
            self._traffic.resume(row[0])

    def resume_failed(self) -> None:
        if self._session_id is None:
            self._traffic.stop()
