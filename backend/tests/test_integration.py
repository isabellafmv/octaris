"""End-to-end print lifecycle through the FastAPI test client.

Exercises the real HTTP routes against the virtual printer, connected the
way dev mode does it (the "virtual" port from /ports), and checks the serial
log, the WebSocket event stream (via the real EventBus), the mid-print 409
gating, timeout -> pause recovery, resends, and the SQLite session rows the
run leaves behind. Stops are placed by holding the printer's motion part way
through a chosen move.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from backend import serial_manager as serial_module
from backend import virtual_printer
from backend.main import app
from backend.queue_worker import PrintStatus
from backend.schemas import WsEvent
from backend.virtual_printer import VirtualPrinter
from tests.serial_fakes import attach

FIXTURES = Path(__file__).parent / "fixtures"
RAW_SAMPLE = (FIXTURES / "raw_sample.gcode").read_text()

# A two-move planner keeps the host just ahead of the motion: when the
# printer is held mid-move, one more move is planned and the next line is in
# flight, blocked on the full planner.
PRINTER_OPTIONS = {"planner_depth": 2, "speed": 200, "busy_interval_s": 0.05, "ok_delay_s": 0}


async def wait_for(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting for condition"
        await asyncio.sleep(0.005)


def worker_index(text: str, occurrence: int = 0) -> int:
    lines = app.state.queue_worker._lines
    return [i for i, line in enumerate(lines) if line == text][occurrence]


def sent_commands(entries: list[dict[str, Any]]) -> list[str]:
    return [e["content"] for e in entries if e["direction"] == "sent"]


async def all_log_entries(client) -> list[dict[str, Any]]:
    resp = await client.get("/gcode/log", params={"limit": 10000})
    assert resp.status_code == 200
    return resp.json()["entries"]


async def upload_sample(client, mode: str = "left"):
    resp = await client.post(
        "/upload/gcode",
        params={"syringe_mode": mode},
        files={"file": ("sample.gcode", RAW_SAMPLE.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text


@pytest.fixture
def dev_mode(monkeypatch):
    monkeypatch.setenv("OCTARIS_VIRTUAL_PRINTER", "1")
    monkeypatch.setattr(virtual_printer, "DEV_OPTIONS", PRINTER_OPTIONS)


# --- the full lifecycle -----------------------------------------------------


async def test_full_print_lifecycle(client, dev_mode, data_dir):
    worker = app.state.queue_worker
    sub_id, event_queue = app.state.event_bus.subscribe()

    # 1. connect to the virtual printer, as dev mode offers it
    resp = await client.get("/ports")
    assert {"device": "virtual", "description": "Virtual printer"} in resp.json()["ports"]
    resp = await client.post("/connect", json={"port": "virtual"})
    assert resp.status_code == 200
    assert app.state.serial_manager.is_connected
    printer: VirtualPrinter = app.state.serial_manager._serial
    assert isinstance(printer, VirtualPrinter)

    resp = await client.post("/gcode/send", json={"line": "M115"})
    assert resp.status_code == 200
    assert "FIRMWARE_NAME:Marlin" in resp.json()["response"]
    assert "Cap:EMERGENCY_PARSER:1" in resp.json()["response"]

    # 2. calibrate
    resp = await client.post("/calibration/zero")
    assert resp.status_code == 200
    assert resp.json()["command"] == "G92 X0 Y0 Z0 B0"
    assert app.state.session.calibrated is True

    # 3. upload the fixture G-code
    await upload_sample(client)

    # 4. start, with motion held half way along the first travel move
    printer.hold_at("X10 Y10 Z0.3", 0.5)
    resp = await client.post("/print/start")
    assert resp.status_code == 200
    assert worker.status == PrintStatus.PRINTING
    await wait_for(printer.held.is_set)

    # -- 409s on manual commands while PRINTING --
    resp = await client.post("/jog", json={"axis": "X", "distance": 1})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Pause the print first"

    resp = await client.post("/gcode/send", json={"line": "G28"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Pause the print first"

    resp = await client.post("/calibration/zero")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Pause the print first"

    # 5. change flow — allowed regardless of print status. Two print lines
    # after the travel move are already sent at 100%; the rest go out at
    # 80%, through the stop and resume below.
    resp = await client.post("/extrusion", json={"rate": 80})
    assert resp.status_code == 200
    assert worker.flow_rate == 80

    # 6. pause, and let the moves already sent run out
    resp = await client.post("/print/pause")
    assert resp.status_code == 200
    assert worker.status == PrintStatus.PAUSED
    # Next, stop half way along the first line of layer 2 (planned B-2.5).
    printer.hold_at("X20 Y10 B-2", 0.5)
    printer.release()
    await wait_for(lambda: printer.moves_planned == 0)

    # -- while PAUSED: jog and raw G-code are allowed, G92/calibration are not --
    resp = await client.post("/jog", json={"axis": "X", "distance": 1})
    assert resp.status_code == 200

    resp = await client.post("/gcode/send", json={"line": "G4 P10"})
    assert resp.status_code == 200

    resp = await client.post("/gcode/send", json={"line": "G92 X0 Y0"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Can't re-zero during a print"

    resp = await client.post("/calibration/zero")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Can't re-zero during a print"

    # 7. resume (plain resume from PAUSED, not a checkpoint resume)
    resp = await client.post("/print/resume")
    assert resp.status_code == 200
    assert worker.status == PrintStatus.PRINTING

    # 8. stop at the hold
    await wait_for(printer.held.is_set)
    k = worker_index("G1 F200 X20 Y10 B-2.5")

    resp = await client.post("/print/stop")
    assert resp.status_code == 200
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}
    assert worker.status == PrintStatus.STOPPED
    assert worker.checkpoint.line == k
    # As actually sent at 80%: B goes from -1.6 to -2, not -2 to -2.5.
    assert worker.checkpoint.position == pytest.approx(
        {"X": 15, "Y": 10, "Z": 0.5, "A": 0, "B": -1.8, "C": 0}
    )
    assert worker.checkpoint.after.pos["B"] == pytest.approx(-2.0)

    [session] = (await client.get("/history")).json()["sessions"]
    session1_id = session["id"]
    assert session["filename"] == "sample.gcode"
    assert session["source"] == "gcode"
    assert session["end_reason"] == "stopped"
    assert session["completed"] is False
    assert session["resume_line"] == k

    # 9. resume the checkpoint
    resp = await client.post("/print/resume")
    assert resp.status_code == 200
    assert worker.status == PrintStatus.PRINTING

    # 10. complete
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert worker.lines_sent == worker.lines_total
    await wait_for(lambda: printer.moves_planned == 0)
    # Where the uninterrupted print would have ended at this flow: the
    # footer's depressurize is scaled too (+0.16 from B-3.2).
    assert printer.position == pytest.approx({"X": 0, "Y": 0, "Z": 5.5, "A": 0, "B": -3.04, "C": 0})

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["id"] == session1_id  # the same session was reopened
    assert session["end_reason"] == "completed"
    assert session["completed"] is True
    assert session["resume_line"] is None

    resp = await client.post("/extrusion", json={"rate": 100})
    assert resp.status_code == 200
    assert worker.flow_rate == 100

    # --- second print, stopped the same way --------------------------------

    await upload_sample(client)
    printer.hold_at("X20 Y20 B-1*", 0.5)
    resp = await client.post("/print/start")
    assert resp.status_code == 200

    await wait_for(printer.held.is_set)
    k2 = worker_index("G1 F200 X20 Y20 B-1")

    resp = await client.post("/print/stop")
    assert resp.status_code == 200
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}
    assert worker.status == PrintStatus.STOPPED
    assert worker.checkpoint.position == pytest.approx(
        {"X": 20, "Y": 15, "Z": 0.3, "A": 0, "B": -0.75, "C": 0}
    )

    sessions = (await client.get("/history")).json()["sessions"]
    assert len(sessions) == 2
    session2 = next(s for s in sessions if s["id"] != session1_id)
    assert session2["end_reason"] == "stopped"
    assert session2["completed"] is False
    assert session2["resume_line"] == k2

    # --- SQLite rows, read straight off the connection ----------------------

    rows = app.state.db.execute(
        "SELECT id, filename, source, completed, end_reason, resume_line FROM sessions ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0] == (session1_id, "sample.gcode", "gcode", 1, "completed", None)
    assert rows[1] == (session2["id"], "sample.gcode", "gcode", 0, "stopped", k2)

    # --- serial log command order --------------------------------------------

    entries = await all_log_entries(client)
    sent = sent_commands(entries)

    i_m92 = sent.index("M92 X800 Y800 Z800 A800 B800 C800")  # auto-sent by /connect
    i_m115 = sent.index("M115")
    i_g92_zero = sent.index("G92 X0 Y0 Z0 B0")  # calibration
    i_preamble = sent.index("M84 S0")  # print/start's "keep motors enabled" preamble
    i_pressurize = sent.index("G1 B-0.2 F400")  # numbered, so sent without its comment
    i_manual_jog = sent.index("G1 X1.0 F300")  # sent only while PAUSED
    i_manual_raw = sent.index("G4 P10")
    i_m410_first = sent.index("M410")
    i_m410_second = sent.index("M410", i_m410_first + 1)

    assert i_m92 < i_m115 < i_g92_zero < i_preamble < i_pressurize
    assert i_preamble < i_manual_jog < i_manual_raw < i_m410_first < i_m410_second

    # Each stop reads the position, then retracts.
    for i_m410 in (i_m410_first, i_m410_second):
        assert sent[i_m410 + 1 : i_m410 + 6] == ["M400", "M114", "G91", "G1 B0.2 F400", "G90"]

    # The resumed checkpoint returns to the stop point before continuing —
    # at the *scaled* B values (flow was still 80% at the stop and stays so
    # for the rest of this print), not the planned, unscaled ones.
    resumed = sent[i_m410_first + 6 :]
    assert resumed[:8] == [
        "G91",
        "G1 Z5 A5 F300",  # lift clear of the print
        "G90",
        "G1 X15 Y10 F300",  # back over the stop point
        "G1 Z0.5 A0 F300",  # down to it
        "G1 B-1.8 F400",  # undo the plunger retract, at the stop position
        "G1 X20 Y10 Z0.5 A0 B-2 C0 F200",  # finish the stopped line, at its scaled target
        "G1 F400 X20 Y20 B-2.4",  # and carry on after it, still flow-scaled
    ]

    # The printer itself saw every M410 acked in order, with nothing mixed up:
    # each stop's M114 reply carried the stop position.
    m114_replies = [
        e["content"]
        for e in entries
        if e["direction"] == "received" and e["content"].startswith("X:15.00 Y:10.00 Z:0.50")
    ]
    assert m114_replies

    # --- each print's serial traffic, in its own file ------------------------

    session1 = next(s for s in sessions if s["id"] == session1_id)
    log1 = Path(session1["serial_log"]).read_text().splitlines()
    log2 = Path(session2["serial_log"]).read_text().splitlines()
    assert Path(session1["serial_log"]).parent == data_dir / "logs" / "prints"
    assert session1["serial_log"] != session2["serial_log"]
    # Print #1: from its first line, through the stop's position read, the
    # resume (appended to the same file) and the footer.
    assert log1[0].endswith("> M84 S0")
    assert any(line.endswith("> N1 G90") for line in log1)
    assert any(line.endswith("> M410") for line in log1)
    assert any("< X:15.00 Y:10.00 Z:0.50" in line for line in log1)
    assert any(line.endswith("> G1 Z5 A5 F300") for line in log1)  # resume's lift
    assert log1[-1].endswith("< ok")
    # A manual command sent while it was paused is part of its traffic.
    assert any(line.endswith("> G4 P10") for line in log1)
    assert not any("G4 P10" in line for line in log2)
    assert any(line.endswith("> M410") for line in log2)

    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    app.state.event_bus.unsubscribe(sub_id)

    # Every event matches its model in backend/schemas.py
    ws_event = TypeAdapter(WsEvent)
    for event in events:
        assert ws_event.validate_python(event).dump() == event

    status_values = [e["value"] for e in events if e["type"] == "status"]
    assert status_values == [
        "ready",  # upload #1
        "printing",  # start #1
        "paused",  # pause
        "printing",  # resume
        "stopped",  # stop
        "printing",  # resume from checkpoint
        "completed",  # natural completion
        "ready",  # upload #2
        "printing",  # start #2
        "stopped",  # second stop
    ]

    stop_events = [e for e in events if e["type"] == "stop"]
    assert stop_events == [
        {"type": "stop", "resumable": True, "reason": None, "line": k},
        {"type": "stop", "resumable": True, "reason": None, "line": k2},
    ]

    assert {"type": "extrusion_rate", "value": 80} in events
    assert {"type": "extrusion_rate", "value": 100} in events
    assert {"type": "calibration", "value": "calibrated"} in events
    assert {"type": "printer", "connected": True, "port": "virtual"} in events
    assert any(e["type"] == "progress" for e in events)
    assert any(e["type"] == "temperature" for e in events)  # M155 auto-reports


# --- faults on the wire ---------------------------------------------------------


SAMPLE_END = {"X": 0.0, "Y": 0.0, "Z": 5.5, "A": 0.0, "B": -3.8, "C": 0.0}


@pytest.fixture
async def printer(client):
    printer = VirtualPrinter(**PRINTER_OPTIONS)
    attach(app.state.serial_manager, printer)
    app.state.session.calibrated = True
    return printer


async def test_timeout_pauses_print_and_retries_on_resume(client, printer, monkeypatch):
    # Make the reply deadline short so a genuine SerialTimeout is fast to hit.
    monkeypatch.setattr(serial_module, "REPLY_DEADLINE_S", 0.2)
    worker = app.state.queue_worker
    await upload_sample(client)

    target = "G1 F200 X20 Y10 B-0.5"
    printer.drop_next(target)  # lost on the wire: no reply at all

    resp = await client.post("/print/start")
    assert resp.status_code == 200

    await wait_for(lambda: worker.status == PrintStatus.PAUSED)
    stall_index = worker_index(target)
    assert worker.lines_sent == stall_index  # never advanced past the stalled line
    assert sent_commands(await all_log_entries(client)).count(target) == 1

    resp = await client.post("/print/resume")
    assert resp.status_code == 200

    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert worker.lines_sent == worker.lines_total
    # The failed attempt, then the retry, after agreeing on line numbers again.
    assert sent_commands(await all_log_entries(client)).count(target) == 2
    assert printer.executed.count(target) == 1
    await wait_for(lambda: printer.moves_planned == 0)
    assert printer.position == pytest.approx(SAMPLE_END)

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["end_reason"] == "completed"
    assert session["completed"] is True


async def test_corrupted_line_is_resent_mid_print(client, printer):
    worker = app.state.queue_worker
    await upload_sample(client)
    printer.corrupt_next("X10 Y20 B-1.5")

    assert (await client.post("/print/start")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    received = [e["content"] for e in await all_log_entries(client) if e["direction"] == "received"]
    assert any(line.startswith("Error:checksum mismatch") for line in received)
    assert any(line.startswith("Resend: ") for line in received)
    # Ran once, in order, and the print ends where it should.
    assert printer.executed.count("G1 F200 X10 Y20 B-1.5") == 1
    await wait_for(lambda: printer.moves_planned == 0)
    assert printer.position == pytest.approx(SAMPLE_END)
