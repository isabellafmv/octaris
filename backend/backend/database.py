from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _default_db_path() -> Path:
    """Put the database in a writable location (not inside the frozen bundle)."""
    if getattr(sys, "frozen", False):
        # Bundled app: use ~/Library/Application Support/Octaris/
        app_data = Path.home() / "Library" / "Application Support" / "Octaris"
        app_data.mkdir(parents=True, exist_ok=True)
        return app_data / "octaris_log.db"
    # Dev: store in backend/
    return Path(__file__).resolve().parent.parent / "octaris_log.db"


DB_PATH = _default_db_path()

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    filename TEXT NOT NULL,
    syringe_config TEXT NOT NULL,
    total_lines INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS extrusion_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    timestamp TEXT NOT NULL,
    extrusion_rate INTEGER NOT NULL,
    lines_sent INTEGER NOT NULL
);
"""


# Columns added to sessions after the first release. init_db() adds any that
# are missing, so existing databases are migrated in place.
_SESSION_COLUMNS = {
    "nozzle_diameter": "REAL",
    "syringe_diameter": "REAL",
    "layer_height": "REAL",
    "pressurize_mm": "REAL",
    "flow_multiplier": "REAL",
    "travel_retract_multiplier": "REAL",
    "source": "TEXT CHECK (source IN ('stl', 'gcode'))",
    "end_reason": "TEXT CHECK (end_reason IN ('completed', 'stopped', 'estop', 'error'))",
    "resume_line": "INTEGER",
}

PRINT_SETTING_KEYS = (
    "nozzle_diameter",
    "syringe_diameter",
    "layer_height",
    "pressurize_mm",
    "flow_multiplier",
    "travel_retract_multiplier",
)

END_REASONS = ("completed", "stopped", "estop", "error")


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    for name, definition in _SESSION_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.executescript(_CREATE_TABLES)
    _migrate(conn)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(
    conn: sqlite3.Connection,
    filename: str,
    syringe_config: str,
    total_lines: int,
    source: str | None = None,
    settings: dict[str, float | None] | None = None,
) -> int:
    settings = settings or {}
    columns = ["started_at", "filename", "syringe_config", "total_lines", "source"]
    values: list[Any] = [_now(), filename, syringe_config, total_lines, source]
    for key in PRINT_SETTING_KEYS:
        columns.append(key)
        values.append(settings.get(key))
    placeholders = ", ".join("?" for _ in columns)
    cursor = conn.execute(
        f"INSERT INTO sessions ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


def end_session(
    conn: sqlite3.Connection,
    session_id: int,
    end_reason: str,
    resume_line: int | None = None,
) -> None:
    """End a session. `resume_line` is the line a resumable stop halted on."""
    if end_reason not in END_REASONS:
        raise ValueError(f"Invalid end_reason: {end_reason}")
    conn.execute(
        "UPDATE sessions SET ended_at = ?, completed = ?, end_reason = ?, resume_line = ? "
        "WHERE id = ?",
        (_now(), int(end_reason == "completed"), end_reason, resume_line, session_id),
    )
    conn.commit()


def reopen_session(conn: sqlite3.Connection, session_id: int) -> None:
    """Mark an ended session as running again (a stopped print was resumed)."""
    conn.execute(
        "UPDATE sessions SET ended_at = NULL, completed = 0, end_reason = NULL, "
        "resume_line = NULL WHERE id = ?",
        (session_id,),
    )
    conn.commit()


def log_extrusion_event(
    conn: sqlite3.Connection,
    session_id: int,
    extrusion_rate: int,
    lines_sent: int,
) -> None:
    conn.execute(
        "INSERT INTO extrusion_events (session_id, timestamp, extrusion_rate, lines_sent) VALUES (?, ?, ?, ?)",
        (session_id, _now(), extrusion_rate, lines_sent),
    )
    conn.commit()


def list_sessions(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    """Most recent sessions first, each with its extrusion events (oldest first)."""
    conn_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        sessions = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)
            )
        ]
        by_id = {s["id"]: s for s in sessions}
        for s in sessions:
            s["completed"] = bool(s["completed"])
            s["extrusion_events"] = []
        if by_id:
            placeholders = ", ".join("?" for _ in by_id)
            for row in conn.execute(
                "SELECT id, session_id, timestamp, extrusion_rate, lines_sent "
                f"FROM extrusion_events WHERE session_id IN ({placeholders}) ORDER BY id",
                list(by_id),
            ):
                by_id[row["session_id"]]["extrusion_events"].append(dict(row))
    finally:
        conn.row_factory = conn_factory
    return sessions
