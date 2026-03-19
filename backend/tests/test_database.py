import sqlite3
import tempfile
from pathlib import Path

from backend.database import (
    create_session,
    end_session,
    init_db,
    log_extrusion_event,
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

    end_session(conn, sid, completed=True)
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert row[2] is not None  # ended_at
    assert row[6] == 1  # completed

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
    end_session(conn, sid, completed=False)

    row = conn.execute("SELECT completed FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert row[0] == 0

    conn.close()
