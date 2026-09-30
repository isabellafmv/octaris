import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.gcode_processor import ProcessedGcode
from backend.main import app
from backend.queue_worker import PrintStatus, QueueWorker
from backend.serial_manager import SerialError
from tests.serial_fakes import attach, unframe

FIXTURES = Path(__file__).parent / "fixtures"


class FakeSerial:
    """Answers every command with "ok" after a short delay, so a print of a
    few hundred lines is still running while the test talks to the API.
    M114 reports the origin, except after M400 (the position read after a
    stop), so stops are never resumable here."""

    def __init__(self, delay: float = 0.005):
        self.is_open = True
        self.delay = delay
        self.written: list[str] = []
        self._pending: list[bytes] = []

    def write(self, data: bytes) -> None:
        _, line = unframe(data.decode().strip())
        self.written.append(line)
        if line == "M114" and self.written[-2:-1] != ["M400"]:
            self._pending = [b"X:0.00 Y:0.00 Z:0.00 A:0.00 B:0.00 C:0.00 Count X:0\n", b"ok\n"]
        else:
            self._pending = [b"ok\n"]

    def flush(self) -> None:
        pass

    def readline(self) -> bytes:
        time.sleep(self.delay)
        if self._pending:
            return self._pending.pop(0)
        return b""

    def close(self) -> None:
        self.is_open = False


@pytest.fixture
async def printer(client):
    fake = FakeSerial()
    attach(app.state.serial_manager, fake)
    app.state.session.calibrated = True
    # The synthetic prints below use both syringes
    app.state.config.nozzle_offset_measured = True
    return fake


async def upload_stl(client, tmp_path, n_lines: int, **params):
    result = ProcessedGcode(lines=[f"G1 X{i % 20} B0.01 F300" for i in range(n_lines)])
    with patch("backend.session.slice_model", AsyncMock(return_value=result)), \
         patch("backend.session.DATA_DIR", tmp_path):
        resp = await client.post(
            "/upload",
            params={"syringe_mode": "both", **params},
            files={"file": ("cube.stl", b"solid cube", "application/octet-stream")},
        )
    assert resp.status_code == 200, resp.text


async def wait_for(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def get_sessions(client) -> list[dict]:
    resp = await client.get("/history")
    assert resp.status_code == 200
    return resp.json()["sessions"]


async def test_start_change_flow_stop_records_history(client, printer, tmp_path):
    await upload_stl(
        client, tmp_path, 400,
        nozzle_diameter=0.41, syringe_diameter=4.6, layer_height=0.3,
        pressurize_mm=0.5, flow_multiplier=1.1, travel_retract_multiplier=0.8,
    )
    worker = app.state.queue_worker

    resp = await client.post("/print/start")
    assert resp.status_code == 200

    await wait_for(lambda: worker.lines_sent >= 10)
    assert (await client.post("/extrusion", json={"rate": 80})).status_code == 200
    lines_at_first_change = worker.lines_sent

    await wait_for(lambda: worker.lines_sent >= lines_at_first_change + 10)
    assert (await client.post("/extrusion", json={"rate": 120})).status_code == 200

    assert (await client.post("/print/stop")).status_code == 200
    assert worker.status == PrintStatus.STOPPED
    assert worker.lines_sent < 400

    # After the session has ended, flow changes are no longer recorded.
    assert (await client.post("/extrusion", json={"rate": 100})).status_code == 200

    [session] = await get_sessions(client)
    assert session["filename"] == "cube.stl"
    assert session["syringe_config"] == "both"
    assert session["total_lines"] == 400
    assert session["source"] == "stl"
    assert session["nozzle_diameter"] == 0.41
    assert session["syringe_diameter"] == 4.6
    assert session["layer_height"] == 0.3
    assert session["pressurize_mm"] == 0.5
    assert session["flow_multiplier"] == 1.1
    assert session["travel_retract_multiplier"] == 0.8
    assert session["ended_at"] is not None
    assert session["end_reason"] == "stopped"
    assert session["completed"] is False
    assert session["resume_line"] is None

    events = session["extrusion_events"]
    assert [e["extrusion_rate"] for e in events] == [80, 120]
    assert events[0]["lines_sent"] >= 10
    assert events[1]["lines_sent"] >= events[0]["lines_sent"] + 10


async def test_completed_print_records_history(client, printer, tmp_path):
    await upload_stl(client, tmp_path, 5, layer_height=0.2)
    worker = app.state.queue_worker

    await client.post("/print/start")
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    [session] = await get_sessions(client)
    assert session["end_reason"] == "completed"
    assert session["completed"] is True
    assert session["layer_height"] == 0.2
    assert session["nozzle_diameter"] is None


async def test_estop_records_history(client, printer, tmp_path):
    await upload_stl(client, tmp_path, 400)
    worker = app.state.queue_worker

    await client.post("/print/start")
    await wait_for(lambda: worker.lines_sent >= 5)
    await client.post("/print/stop")

    [session] = await get_sessions(client)
    assert session["end_reason"] == "stopped"
    assert "M410" in printer.written


async def test_gcode_upload_is_recorded_as_gcode_source(client, printer):
    raw = (FIXTURES / "raw_sample.gcode").read_bytes()
    resp = await client.post(
        "/upload/gcode",
        params={"syringe_mode": "left"},
        files={"file": ("sample.gcode", raw, "text/plain")},
    )
    assert resp.status_code == 200, resp.text

    await client.post("/print/start")
    await client.post("/print/stop")

    [session] = await get_sessions(client)
    assert session["source"] == "gcode"
    assert session["filename"] == "sample.gcode"
    assert session["layer_height"] is None


async def test_history_limit(client, printer, tmp_path):
    await upload_stl(client, tmp_path, 400)
    for _ in range(3):
        await client.post("/print/start")
        await client.post("/print/stop")

    assert len(await get_sessions(client)) == 3
    resp = await client.get("/history", params={"limit": 2})
    assert [s["id"] for s in resp.json()["sessions"]] == [3, 2]
    assert (await client.get("/history", params={"limit": 0})).status_code == 422


# --- QueueWorker end-reason callback --------------------------------------


def make_worker(send):
    serial = MagicMock()
    serial.send = AsyncMock(side_effect=send)
    serial.emergency_write = AsyncMock()
    reasons: list[str] = []

    def on_event(event: dict) -> None:
        if event["type"] == "print_end":
            reasons.append(event["reason"])

    return QueueWorker(serial, on_event=on_event), reasons


async def test_worker_reports_error_on_serial_failure():
    async def fail_on_third(line, numbered=False):
        if line == "G1 X2":
            raise SerialError("Send failed")
        return "ok"

    worker, reasons = make_worker(fail_on_third)
    worker.load_gcode(["G1 X0", "G1 X1", "G1 X2", "G1 X3"])
    worker.start()
    await asyncio.sleep(0.2)

    assert worker.status == PrintStatus.STOPPED
    assert reasons == ["error"]


async def test_worker_reports_each_print_end_once():
    async def ok(line, numbered=False):
        return "ok"

    worker, reasons = make_worker(ok)
    worker.load_gcode(["G1 X0"])
    worker.start()
    await asyncio.sleep(0.2)
    # An e-stop after the print already finished is not a second end.
    await worker.estop()

    assert reasons == ["completed"]


async def test_estop_without_print_reports_nothing():
    async def ok(line, numbered=False):
        return "ok"

    worker, reasons = make_worker(ok)
    await worker.estop()

    assert reasons == []


def test_history_follows_print_events_on_the_bus():
    from backend.database import init_db
    from backend.events import EventBus
    from backend.history import PrintHistory

    bus = EventBus()
    history = PrintHistory(init_db(Path(":memory:")))
    bus.listen(history.on_event)

    session_id = history.start("a.gcode", "left", 10, source="gcode", settings={})
    bus.publish({"type": "print_end", "reason": "stopped", "resume_line": 4})
    assert history.session_id is None
    bus.publish({"type": "print_resumed"})
    assert history.session_id == session_id
    bus.publish({"type": "print_end", "reason": "completed", "resume_line": None})

    [row] = history.list(10)
    assert (row["end_reason"], row["completed"], row["resume_line"]) == ("completed", True, None)
