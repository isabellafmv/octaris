import sqlite3
import tempfile
from pathlib import Path

import pytest

from backend.database import (
    create_session,
    end_session,
    init_db,
    list_sessions,
    log_extrusion_event,
    reopen_session,
)


def test_init_db_creates_tables():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    conn = init_db(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "sessions" in tables
    assert "extrusion_events" in tables
    conn.close()
    db_path.unlink()


def test_session_lifecycle():
    conn = init_db(Path(":memory:"))

    sid = create_session(conn, "test.stl", "left", 100)
    assert sid == 1

    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert row[3] == "test.stl"  # filename
    assert row[4] == "left"  # syringe_config
    assert row[5] == 100  # total_lines
    assert row[6] == 0  # completed

    end_session(conn, sid, "completed")
    row = conn.execute(
        "SELECT ended_at, completed, end_reason FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    assert row[0] is not None  # ended_at
    assert row[1] == 1  # completed
    assert row[2] == "completed"

    conn.close()


def test_extrusion_events():
    conn = init_db(Path(":memory:"))
    sid = create_session(conn, "test.stl", "both", 200)

    log_extrusion_event(conn, sid, 95, 50)
    log_extrusion_event(conn, sid, 80, 100)

    rows = conn.execute(
        "SELECT * FROM extrusion_events WHERE session_id = ?", (sid,)
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][3] == 95  # extrusion_rate
    assert rows[1][4] == 100  # lines_sent

    conn.close()


def test_session_stopped_incomplete():
    conn = init_db(Path(":memory:"))
    sid = create_session(conn, "test.stl", "right", 300)
    end_session(conn, sid, "stopped")

    row = conn.execute(
        "SELECT completed, end_reason FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    assert row == (0, "stopped")

    conn.close()


def test_end_session_rejects_unknown_reason():
    conn = init_db(Path(":memory:"))
    sid = create_session(conn, "test.stl", "left", 10)
    with pytest.raises(ValueError):
        end_session(conn, sid, "crashed")
    conn.close()


def test_create_session_stores_source_and_settings():
    conn = init_db(Path(":memory:"))
    sid = create_session(
        conn, "cube.stl", "both", 500,
        source="stl",
        settings={"nozzle_diameter": 0.41, "layer_height": 0.3, "flow_multiplier": 1.2},
    )

    row = conn.execute(
        "SELECT source, nozzle_diameter, syringe_diameter, layer_height, "
        "pressurize_mm, flow_multiplier, travel_retract_multiplier, "
        "end_reason, resume_line FROM sessions WHERE id = ?",
        (sid,),
    ).fetchone()
    assert row == ("stl", 0.41, None, 0.3, None, 1.2, None, None, None)
    conn.close()


def test_init_db_migrates_old_sessions_table(tmp_path):
    db_path = tmp_path / "old.db"
    old = sqlite3.connect(str(db_path))
    old.executescript(
        """
        CREATE TABLE sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            filename TEXT NOT NULL,
            syringe_config TEXT NOT NULL,
            total_lines INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO sessions (started_at, filename, syringe_config, total_lines)
        VALUES ('2026-01-01T00:00:00+00:00', 'old.stl', 'left', 42);
        """
    )
    old.commit()
    old.close()

    conn = init_db(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    assert {
        "nozzle_diameter", "syringe_diameter", "layer_height", "pressurize_mm",
        "flow_multiplier", "travel_retract_multiplier", "source", "end_reason",
        "resume_line",
    } <= columns

    # The old row survives, and new sessions can use the new columns.
    [old_row] = list_sessions(conn)
    assert old_row["filename"] == "old.stl"
    assert old_row["source"] is None
    create_session(conn, "new.gcode", "right", 7, source="gcode")
    conn.close()

    # Running init_db again on a migrated database is a no-op.
    init_db(db_path).close()


def test_list_sessions_newest_first_with_events():
    conn = init_db(Path(":memory:"))
    first = create_session(conn, "a.stl", "left", 10, source="stl")
    second = create_session(conn, "b.gcode", "right", 20, source="gcode")
    log_extrusion_event(conn, first, 90, 3)
    log_extrusion_event(conn, second, 110, 5)
    log_extrusion_event(conn, first, 120, 7)
    end_session(conn, first, "completed")

    sessions = list_sessions(conn, limit=50)

    assert [s["id"] for s in sessions] == [second, first]
    assert sessions[1]["completed"] is True
    assert sessions[1]["end_reason"] == "completed"
    assert [(e["extrusion_rate"], e["lines_sent"]) for e in sessions[1]["extrusion_events"]] == [
        (90, 3), (120, 7),
    ]
    assert [e["extrusion_rate"] for e in sessions[0]["extrusion_events"]] == [110]

    assert [s["id"] for s in list_sessions(conn, limit=1)] == [second]
    conn.close()


def test_resume_line_and_reopen():
    conn = init_db(Path(":memory:"))
    sid = create_session(conn, "a.stl", "left", 100, source="stl")

    end_session(conn, sid, "estop", resume_line=42)
    row = conn.execute(
        "SELECT ended_at, end_reason, resume_line FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    assert row[0] is not None and row[1:] == ("estop", 42)

    reopen_session(conn, sid)
    row = conn.execute(
        "SELECT ended_at, completed, end_reason, resume_line FROM sessions WHERE id = ?", (sid,)
    ).fetchone()
    assert row == (None, 0, None, None)
    conn.close()
