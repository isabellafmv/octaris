from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "octaris_log.db"

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


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.executescript(_CREATE_TABLES)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(
    conn: sqlite3.Connection,
    filename: str,
    syringe_config: str,
    total_lines: int,
) -> int:
    cursor = conn.execute(
        "INSERT INTO sessions (started_at, filename, syringe_config, total_lines) VALUES (?, ?, ?, ?)",
        (_now(), filename, syringe_config, total_lines),
    )
    conn.commit()
    return cursor.lastrowid  # type: ignore


def end_session(conn: sqlite3.Connection, session_id: int, completed: bool) -> None:
    conn.execute(
        "UPDATE sessions SET ended_at = ?, completed = ? WHERE id = ?",
        (_now(), int(completed), session_id),
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
