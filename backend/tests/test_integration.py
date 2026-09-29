"""End-to-end print lifecycle through the FastAPI test client.

Exercises the real HTTP routes (not the QueueWorker/SerialManager directly)
against a fake serial device, and checks the serial log, the WebSocket event
stream (via the real EventBus), the mid-print 409 gating, timeout -> pause
recovery, and the SQLite session rows the run leaves behind.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend import serial_manager as serial_module
from backend.main import app
from backend.queue_worker import PrintStatus

FIXTURES = Path(__file__).parent / "fixtures"
RAW_SAMPLE = (FIXTURES / "raw_sample.gcode").read_text()


def m114(**pos: float) -> str:
    logical = " ".join(f"{axis}:{value:.2f}" for axis, value in pos.items())
    return f"{logical} Count X:0 Y:0 Z:0"


class FakePrinter:
    """Mimics a Marlin printer on the serial port for the test client.

    Every line is answered "ok" after a short delay (so a print is still
    in flight while the test talks to the API). M114 answers with
    `position`; M115 answers with a firmware string. While `block_on` is
    in flight, its "ok" is held back until M410 arrives (EMERGENCY_PARSER
    semantics), letting the test e-stop deterministically mid-line. A line
    named in `fail_once_on` gets no reply at all the first time it's sent
    (so it times out), then answers normally on any later attempt (retry).
    """

    def __init__(self, delay: float = 0.005):
        self.is_open = True
        self.delay = delay
        self.position = ""
        self.firmware = "FIRMWARE_NAME:Marlin bugfix-2.1.2 MACHINE_TYPE:Octaris EXTRUDER_COUNT:1"
        self.block_on: str | None = None
        self.fail_once_on: str | None = None
        self.written: list[str] = []
        self.reached = threading.Event()  # block_on was written
        self._released = threading.Event()  # M410 arrived
        self._blocked = False
        self._already_failed: set[str] = set()
        self._pending: list[str] = []

    def write(self, data: bytes) -> None:
        line = data.decode().strip()
        self.written.append(line)
        if line == "M410":
            self._released.set()  # emergency parser: acts on it immediately
            return

        if line == "M114":
            self._pending = [self.position, "ok"]
        elif line == "M115":
            self._pending = [self.firmware, "ok"]
        elif line == self.fail_once_on and line not in self._already_failed:
            self._already_failed.add(line)
            self._pending = []  # never answers this attempt -> timeout
        else:
            self._pending = ["ok"]

        if line == self.block_on:
            self.block_on = None
            self._blocked = True
            self.reached.set()

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def readline(self) -> bytes:
        if self._blocked:
            self._released.wait(timeout=5)
            self._blocked = False
        if not self._pending:
            time.sleep(0.01)
            return b""
        time.sleep(self.delay)
        return (self._pending.pop(0) + "\n").encode()

    def close(self) -> None:
        self.is_open = False


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


# --- the full lifecycle -----------------------------------------------------


async def test_full_print_lifecycle(client):
    worker = app.state.queue_worker
    sub_id, event_queue = app.state.event_bus.subscribe()

    fake1 = FakePrinter()

    # 1. connect — through the real endpoint, not by poking _serial directly.
    with patch("backend.serial_manager.serial.Serial", return_value=fake1):
        resp = await client.post("/connect", json={"port": "/dev/fake"})
    assert resp.status_code == 200
    assert app.state.serial_manager.is_connected

    # Mock device answers M115 too (not exercised elsewhere, but required
    # of the mock, so check it directly).
    resp = await client.post("/gcode/send", json={"line": "M115"})
    assert resp.status_code == 200
    assert "FIRMWARE_NAME:Marlin" in resp.json()["response"]

    # 2. calibrate
    resp = await client.post("/calibration/zero")
    assert resp.status_code == 200
    assert resp.json()["command"] == "G92 X0 Y0 Z0 B0"
    assert app.state.is_calibrated is True

    # 3. upload the fixture G-code
    await upload_sample(client)

    # 4. start — and arm the soft-stop point before any line can reach it.
    fake1.block_on = "G1 F200 X10 Y20 B-1.5"  # worker line 6
    fake1.position = m114(X=15, Y=10, Z=0.3, A=0, B=-0.25, C=0)

    resp = await client.post("/print/start")
    assert resp.status_code == 200
    assert worker.status == PrintStatus.PRINTING

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

    await wait_for(lambda: worker.lines_sent >= 2)

    # 5. change flow — allowed regardless of print status
    resp = await client.post("/extrusion", json={"rate": 80})
    assert resp.status_code == 200
    assert worker.flow_rate == 80

    # Put it back to 100 before the checkpoint below: a non-100 flow rate
    # scales the B/C values on every line sent from here on (_apply_flow_rate),
    # which the checkpoint locator can't see (it matches against the
    # *unscaled* simulated path) — the backend itself flags this as a reason
    # a stop may come back not-resumable. Keep this test's stop resumable.
    resp = await client.post("/extrusion", json={"rate": 100})
    assert resp.status_code == 200
    assert worker.flow_rate == 100

    # 6. pause
    resp = await client.post("/print/pause")
    assert resp.status_code == 200
    assert worker.status == PrintStatus.PAUSED

    # -- while PAUSED: jog and raw G-code are allowed, G92/calibration are not --
    resp = await client.post("/jog", json={"axis": "X", "distance": 1})
    assert resp.status_code == 200

    resp = await client.post("/gcode/send", json={"line": "G28"})
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

    # 8. soft stop, while a specific line is in flight
    await wait_for(fake1.reached.is_set)
    k = worker_index("G1 F200 X20 Y10 B-0.5")

    resp = await client.post("/print/stop")
    assert resp.status_code == 200
    stop_result = resp.json()
    assert stop_result == {"status": "stopped", "resumable": True, "reason": None}
    assert worker.status == PrintStatus.STOPPED
    assert worker.checkpoint.line == k

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

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["id"] == session1_id  # the same session was reopened
    assert session["end_reason"] == "completed"
    assert session["completed"] is True
    assert session["resume_line"] is None

    # --- second print, which gets a hard e-stop -----------------------------

    fake2 = FakePrinter()
    app.state.serial_manager._serial = fake2  # still "connected"; swap the wire
    fake2.block_on = "G1 F200 X10 Y20 B-1.5"
    fake2.position = m114(X=15, Y=10, Z=0.3, A=0, B=-0.25, C=0)

    await upload_sample(client)
    resp = await client.post("/print/start")
    assert resp.status_code == 200

    await wait_for(fake2.reached.is_set)
    k2 = worker_index("G1 F200 X20 Y10 B-0.5")

    resp = await client.post("/print/estop")
    assert resp.status_code == 200
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}
    assert worker.status == PrintStatus.STOPPED

    sessions = (await client.get("/history")).json()["sessions"]
    assert len(sessions) == 2
    session2 = next(s for s in sessions if s["id"] != session1_id)
    assert session2["end_reason"] == "estop"
    assert session2["completed"] is False
    assert session2["resume_line"] == k2

    # --- SQLite rows, read straight off the connection ----------------------

    rows = app.state.db.execute(
        "SELECT id, filename, source, completed, end_reason, resume_line "
        "FROM sessions ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0] == (session1_id, "sample.gcode", "gcode", 1, "completed", None)
    assert rows[1] == (session2["id"], "sample.gcode", "gcode", 0, "estop", k2)

    # --- serial log command order --------------------------------------------

    entries = await all_log_entries(client)
    sent = sent_commands(entries)

    i_m92 = sent.index("M92 X800 Y800 Z800 A800 B800 C800")  # auto-sent by /connect
    i_m115 = sent.index("M115")
    i_g92_zero = sent.index("G92 X0 Y0 Z0 B0")  # calibration
    i_preamble = sent.index("M84 S0")  # print/start's "keep motors enabled" preamble
    i_pressurize = sent.index("G1 B-0.2 F400 ; pressurize B")
    i_manual_jog = sent.index("G1 X1.0 F300")  # sent only while PAUSED
    i_manual_g28 = sent.index("G28")  # only ever sent manually; fixture's own G28 is stripped
    i_m410_soft = sent.index("M410")
    i_m410_hard = sent.index("M410", i_m410_soft + 1)

    assert i_m92 < i_m115 < i_g92_zero < i_preamble < i_pressurize
    assert i_preamble < i_manual_jog < i_manual_g28 < i_m410_soft < i_m410_hard

    # The soft-stop checkpoint sequence: read position, then retract.
    after_soft_stop = sent[i_m410_soft + 1:i_m410_soft + 6]
    assert after_soft_stop == ["M400", "M114", "G91", "G1 B0.2 F400", "G90"]

    # The hard e-stop does the same thing.
    after_hard_stop = sent[i_m410_hard + 1:i_m410_hard + 6]
    assert after_hard_stop == ["M400", "M114", "G91", "G1 B0.2 F400", "G90"]

    # The resumed checkpoint returns to the stop point before continuing.
    resumed = sent[i_m410_soft + 6:]
    assert resumed[:8] == [
        "G91",
        "G1 Z5 A5 F300",  # lift clear of the print (both Z and A: A=0 is known too)
        "G90",
        "G1 X15 Y10 F300",  # back over the stop point
        "G1 Z0.3 A0 F300",  # down to it
        "G1 B-0.25 F400",  # undo the plunger retract
        "G1 X20 Y10 Z0.3 B-0.5 F200",  # finish the stopped line
        "G1 F200 X20 Y20 B-1",  # and carry on after it
    ]

    # --- the WebSocket event stream (drained from the real event bus) -------

    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    app.state.event_bus.unsubscribe(sub_id)

    status_values = [e["value"] for e in events if e["type"] == "status"]
    assert status_values == [
        "ready",       # upload #1
        "printing",    # start #1
        "paused",      # pause
        "printing",    # resume
        "stopped",     # soft stop
        "printing",    # resume from checkpoint
        "completed",   # natural completion
        "ready",       # upload #2
        "printing",    # start #2
        "stopped",     # hard e-stop
    ]

    stop_events = [e for e in events if e["type"] == "stop"]
    assert stop_events == [
        {"type": "stop", "resumable": True, "reason": None, "line": k},
        {"type": "stop", "resumable": True, "reason": None, "line": k2},
    ]

    assert {"type": "extrusion_rate", "value": 80} in events
    assert {"type": "extrusion_rate", "value": 100} in events
    assert {"type": "calibration", "value": "calibrated"} in events
    assert {"type": "printer", "connected": True, "port": "/dev/fake"} in events
    assert any(e["type"] == "progress" for e in events)


# --- timeout -> pause behaviour ----------------------------------------------


async def test_timeout_pauses_print_and_retries_on_resume(client, monkeypatch):
    # Make the reply deadline short so a genuine SerialTimeout is fast to hit.
    monkeypatch.setattr(serial_module, "REPLY_DEADLINE_S", 0.08)

    worker = app.state.queue_worker
    fake = FakePrinter()
    app.state.serial_manager._serial = fake
    app.state.serial_manager._port = "/dev/fake"
    app.state.is_calibrated = True

    await upload_sample(client)

    target = "G1 F200 X20 Y10 B-0.5"  # worker line 4
    fake.fail_once_on = target

    resp = await client.post("/print/start")
    assert resp.status_code == 200

    await wait_for(lambda: worker.status == PrintStatus.PAUSED)
    stall_index = worker_index(target)
    assert worker.lines_sent == stall_index  # never advanced past the stalled line

    entries = await all_log_entries(client)
    sent = sent_commands(entries)
    assert sent.count(target) == 1  # only the failed attempt so far

    resp = await client.post("/print/resume")
    assert resp.status_code == 200

    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert worker.lines_sent == worker.lines_total

    entries = await all_log_entries(client)
    sent = sent_commands(entries)
    assert sent.count(target) == 2  # the failed attempt, then the successful retry

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["end_reason"] == "completed"
    assert session["completed"] is True
