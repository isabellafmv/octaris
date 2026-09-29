import asyncio
import threading
import time
from pathlib import Path

import pytest

from backend.checkpoint import Checkpoint, build_resume_commands, locate_line, parse_m114
from backend.gcode_processor import process_gcode, simulate_states
from backend.main import app
from backend.queue_worker import PrintStatus

FIXTURES = Path(__file__).parent / "fixtures"
RAW_SAMPLE = (FIXTURES / "raw_sample.gcode").read_text()


def m114(**pos: float) -> str:
    logical = " ".join(f"{axis}:{value:.2f}" for axis, value in pos.items())
    return f"{logical} Count X:0 Y:0 Z:0"


class FakePrinter:
    """Mimics a Marlin printer on the serial port.

    Every line is answered with "ok"; M114 with `position` — except the very
    first M114, which QueueWorker.start() sends before anything else to seed
    its as-sent position tracker, and which gets `start_position` instead (a
    real printer's actual position at print start, not wherever the test
    wants the *stop* to be found). When `block_on` is written, its "ok" is
    held back until M410 arrives — like a planner that is full when the
    e-stop comes in.
    """

    def __init__(
        self,
        position: str = "",
        block_on: str | None = None,
        start_position: str | None = None,
    ):
        self.is_open = True
        self.position = position
        self.start_position = start_position if start_position is not None else m114(
            X=0, Y=0, Z=0, A=0, B=0, C=0
        )
        self.block_on = block_on
        self.written: list[str] = []
        self.reached = threading.Event()  # block_on was written
        self._released = threading.Event()  # M410 arrived
        self._blocked = False
        self._m114_calls = 0
        self._pending: list[str] = []

    def write(self, data: bytes) -> None:
        line = data.decode().strip()
        self.written.append(line)
        if line == "M410":
            self._released.set()  # emergency parser: acts on it immediately
            return
        if line == "M114":
            self._m114_calls += 1
            reply = self.start_position if self._m114_calls == 1 else self.position
            self._pending = [reply, "ok"]
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
            self._released.wait(timeout=2)
            self._blocked = False
        if not self._pending:
            time.sleep(0.01)
            return b""
        return (self._pending.pop(0) + "\n").encode()

    def close(self) -> None:
        self.is_open = False

    def after(self, marker: str) -> list[str]:
        """Lines written after the last occurrence of `marker`."""
        idx = len(self.written) - 1 - self.written[::-1].index(marker)
        return self.written[idx + 1:]


async def wait_for(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.005)


@pytest.fixture
def printer(client):
    fake = FakePrinter()
    app.state.serial_manager._serial = fake
    app.state.serial_manager._port = "/dev/fake"
    app.state.is_calibrated = True
    events: list[dict] = []
    app.state.queue_worker._on_event = events.append
    fake.events = events
    return fake


async def upload_sample(client, mode: str = "left"):
    resp = await client.post(
        "/upload/gcode",
        params={"syringe_mode": mode},
        files={"file": ("sample.gcode", RAW_SAMPLE.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text


async def estop_at(client, printer: FakePrinter, block_on: str, position: str) -> dict:
    """Start the sample print and e-stop it while `block_on` is in flight,
    with the printer reporting `position`."""
    printer.block_on = block_on
    printer.position = position
    await upload_sample(client)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.reached.is_set)
    resp = await client.post("/print/estop")
    assert resp.status_code == 200
    return resp.json()


def worker_index(text: str, occurrence: int = 0) -> int:
    lines = app.state.queue_worker._lines
    return [i for i, line in enumerate(lines) if line == text][occurrence]


# --- state simulation -------------------------------------------------------


def test_state_simulation_on_fixture():
    result = process_gcode(RAW_SAMPLE, "left")
    lines = result.lines
    assert len(result.state_before) == len(result.state_after) == len(lines)
    assert result.extrusion_axes == ("B",)
    assert result.pressurize_mm == 0.2

    def after(text: str, occurrence: int = 0):
        idx = [i for i, line in enumerate(lines) if line.startswith(text)][occurrence]
        return result.state_before[idx], result.state_after[idx]

    # Nothing is known before the program sets it.
    first = result.state_before[0]
    assert all(v is None for v in first.pos.values())
    assert first.relative is False

    before, state = after("G1 B-0.2 F400")  # pressurize
    assert state.pos["B"] == -0.2 and state.feed == 400
    _, state = after("G92 B0")
    assert state.pos["B"] == 0 and state.pos["X"] is None

    before, state = after("G1 F200 X20 Y10 B-0.5")
    assert before.pos == {"X": 10, "Y": 10, "Z": 0.3, "A": None, "B": 0, "C": None}
    assert state.pos == {"X": 20, "Y": 10, "Z": 0.3, "A": None, "B": -0.5, "C": None}
    assert state.feed == 200

    # Layer change: relative depressurize inside G91 ... G90.
    _, state = after("G91")
    assert state.relative is True
    before, state = after("G1 B0.2 F400")
    assert before.pos["B"] == -2 and state.pos["B"] == pytest.approx(-1.8)
    assert state.relative is True
    _, state = after("G0 F400 X10 Y10 Z0.5")
    assert state.relative is False and state.pos["Z"] == 0.5

    # Footer: relative Z raise, then absolute return to origin.
    _, state = after("G1 Z5 F300")
    assert state.pos["Z"] == pytest.approx(5.5)
    final = result.state_after[-1]
    assert final.pos["X"] == 0 and final.pos["Y"] == 0
    assert final.pos["B"] == pytest.approx(-3.8)


def test_parse_m114():
    reply = "X:15.00 Y:10.00 Z:0.30 A:0.00 B:-0.25 C:0.00 Count X:12000 Y:8000 Z:240\nok"
    assert parse_m114(reply) == {
        "X": 15.0, "Y": 10.0, "Z": 0.3, "A": 0.0, "B": -0.25, "C": 0.0,
    }
    assert parse_m114("ok") is None


# --- locating the stop line ------------------------------------------------


SELF_CROSSING = [
    "G90",
    "G1 X0 Y0 Z0.3 B0 F200",
    "G1 X10 Y10 B-1",  # crosses (5, 5) at B-0.5
    "G1 X10 Y0 B-2",
    "G1 X0 Y10 B-3",  # crosses (5, 5) again, at B-2.5
    "G1 X0 Y20 B-4",
]


@pytest.mark.parametrize("b, expected_line", [(-0.5, 2), (-2.5, 4), (-2.47, 4)])
def _segments(before, after, indices=None):
    indices = range(len(before)) if indices is None else indices
    return [(i, before[i], after[i]) for i in indices]


def test_locate_line_on_self_crossing_path(b, expected_line):
    before, after = simulate_states(SELF_CROSSING)
    point = {"X": 5.0, "Y": 5.0, "Z": 0.3, "A": 7.0, "B": b, "C": 0.0}

    located = locate_line(_segments(before, after), SELF_CROSSING, point)

    assert located is not None
    line, matched_after = located
    assert line == expected_line
    assert matched_after == after[expected_line]


def test_locate_line_rejects_point_off_every_path():
    before, after = simulate_states(SELF_CROSSING)
    segments = _segments(before, after)

    # On the XY path of line 4, but with B beyond the tolerance
    assert locate_line(
        segments, SELF_CROSSING, {"X": 5, "Y": 5, "Z": 0.3, "B": -1.5}
    ) is None
    # Off the XY path
    assert locate_line(
        segments, SELF_CROSSING, {"X": 5, "Y": 5.2, "Z": 0.3, "B": -0.5}
    ) is None
    # Only lines that were actually sent are considered
    assert locate_line(
        _segments(before, after, [0, 1, 2, 3]), SELF_CROSSING,
        {"X": 5, "Y": 5, "Z": 0.3, "B": -2.5},
    ) is None


async def test_estop_locates_line_and_retracts(client, printer):
    result = await estop_at(
        client, printer,
        block_on="G1 F200 X10 Y20 B-1.5",
        position=m114(X=15, Y=10, Z=0.3, A=0, B=-0.25, C=0),
    )

    worker = app.state.queue_worker
    k = worker_index("G1 F200 X20 Y10 B-0.5")
    expected_after = process_gcode(RAW_SAMPLE, "left").state_after[k]
    assert result == {"status": "stopped", "resumable": True, "reason": None}
    assert worker.checkpoint == Checkpoint(
        line=k,
        position={"X": 15, "Y": 10, "Z": 0.3, "A": 0, "B": -0.25, "C": 0},
        after=expected_after,
        retract={"B": 0.2},
    )
    # M410 goes out while a line is in flight; position is read after it.
    assert printer.after("M410") == ["M400", "M114", "G91", "G1 B0.2 F400", "G90"]
    assert {"type": "stop", "resumable": True, "reason": None, "line": k} in printer.events

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["end_reason"] == "estop"
    assert session["resume_line"] == k


async def test_estop_without_retract(client, printer):
    app.state.queue_worker._retract_on_estop = False
    await estop_at(
        client, printer,
        block_on="G1 F200 X10 Y20 B-1.5",
        position=m114(X=15, Y=10, Z=0.3, A=0, B=-0.25, C=0),
    )

    assert printer.after("M410") == ["M400", "M114"]
    assert app.state.queue_worker.checkpoint.retract == {}


async def test_estop_off_path_is_not_resumable(client, printer):
    result = await estop_at(
        client, printer,
        block_on="G1 F200 X10 Y20 B-1.5",
        position=m114(X=50, Y=50, Z=0.3, A=0, B=-0.25, C=0),
    )

    assert result["resumable"] is False
    assert "not on any recently sent line" in result["reason"]
    assert app.state.queue_worker.checkpoint is None
    # Nothing is retracted for a stop that can't be resumed.
    assert printer.after("M410") == ["M400", "M114"]
    stop_events = [e for e in printer.events if e["type"] == "stop"]
    assert stop_events == [{"type": "stop", "resumable": False, "reason": result["reason"]}]
    assert any(e["type"] == "error" and result["reason"] in e["message"] for e in printer.events)

    resp = await client.post("/print/resume")
    assert resp.status_code == 409
    assert "not on any recently sent line" in resp.json()["detail"]

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["resume_line"] is None


async def test_resume_without_stop_is_409(client, printer):
    assert (await client.post("/print/resume")).status_code == 409


# --- resuming ----------------------------------------------------------------


async def test_resume_command_sequence(client, printer):
    await estop_at(
        client, printer,
        block_on="G1 F200 X10 Y20 B-1.5",
        position=m114(X=15, Y=10, Z=0.3, A=0, B=-0.25, C=0),
    )
    worker = app.state.queue_worker
    k = worker_index("G1 F200 X20 Y10 B-0.5")

    resp = await client.post("/print/resume")
    assert resp.status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    # Skip M400, M114 and the three retract lines that followed the e-stop.
    resumed = printer.after("M410")[5:]
    assert resumed[:8] == [
        "G91",
        "G1 Z5 A5 F300",  # lift clear of the print
        "G90",
        "G1 X15 Y10 F300",  # back over the stop point
        "G1 Z0.3 A0 F300",  # down to it
        "G1 B-0.25 F400",  # undo the plunger retract
        "G1 X20 Y10 Z0.3 B-0.5 F200",  # finish the stopped line
        "G1 F200 X20 Y20 B-1",  # and carry on after it
    ]
    # Every line after k is sent exactly once more, in order.
    assert resumed[7:] == worker._lines[k + 1:]
    assert worker.lines_sent == worker.lines_total

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["end_reason"] == "completed"
    assert session["resume_line"] is None


async def test_resume_inside_relative_block(client, printer):
    # Stopped during the layer-change depressurize, which runs under G91.
    await estop_at(
        client, printer,
        block_on="G0 F400 X10 Y10 Z0.5",
        position=m114(X=10, Y=10, Z=0.3, A=0, B=-1.9, C=0),
    )
    worker = app.state.queue_worker
    k = worker_index("G1 B0.2 F400")
    assert worker.checkpoint.line == k

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    resumed = printer.after("M410")[5:]
    assert resumed[:9] == [
        "G91",
        "G1 Z5 A5 F300",
        "G90",
        "G1 X10 Y10 F300",
        "G1 Z0.3 A0 F300",
        "G1 B-1.9 F400",
        "G1 X10 Y10 Z0.3 B-1.8 F400",  # the relative line, as an absolute target
        "G91",  # back into the relative block
        "G90",  # line k+1
    ]
    assert resumed[8:] == worker._lines[k + 1:]


def test_resume_commands_for_non_moving_line():
    _, after = simulate_states(["G90", "G1 X1 Y1 Z1 F250", "M106"])
    cp = Checkpoint(line=2, position={"X": 1, "Y": 1, "Z": 1})

    assert build_resume_commands(cp, after[2]) == [
        "G91", "G1 Z5 F300", "G90", "G1 X1 Y1 F300", "G1 Z1 F300", "G1 X1 Y1 Z1 F250",
    ]

    _, after = simulate_states(["M106"])
    cp = Checkpoint(line=0, position={"X": 1, "Y": 1, "Z": 1})
    assert build_resume_commands(cp, after[0])[-1] == "G1 Z1 F300"


async def test_pause_and_resume_still_work(client, printer):
    await upload_sample(client)
    worker = app.state.queue_worker
    await client.post("/print/start")
    await client.post("/print/pause")
    assert worker.status == PrintStatus.PAUSED
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert printer.written[0] == "M84 S0"
    assert "M410" not in printer.written


# --- invalidation --------------------------------------------------------------


async def _send_gcode(client, line):
    return await client.post("/gcode/send", json={"line": line})


INVALIDATING = {
    "disconnect": lambda client: client.post("/disconnect"),
    "calibration zero": lambda client: client.post("/calibration/zero"),
    "manual G92": lambda client: _send_gcode(client, "G92 X0 Y0"),
    "manual plunger move": lambda client: _send_gcode(client, "G1 B-1 F100"),
    "B jog": lambda client: client.post("/jog", json={"axis": "B", "distance": -0.5}),
    "C jog": lambda client: client.post("/jog", json={"axis": "C", "distance": 0.5}),
    "new file": lambda client: upload_sample(client),
}


@pytest.mark.parametrize("action", list(INVALIDATING))
async def test_checkpoint_invalidated(client, printer, action):
    await estop_at(
        client, printer,
        block_on="G1 F200 X10 Y20 B-1.5",
        position=m114(X=15, Y=10, Z=0.3, A=0, B=-0.25, C=0),
    )
    worker = app.state.queue_worker
    assert worker.resumable

    await INVALIDATING[action](client)

    assert not worker.resumable
    assert worker.stop_reason
    assert printer.events[-2] == {
        "type": "stop", "resumable": False, "reason": worker.stop_reason,
    }
    assert (await client.post("/print/resume")).status_code == 409


@pytest.mark.parametrize("axis", ["X", "Y", "Z", "A"])
async def test_stage_jog_keeps_checkpoint(client, printer, axis):
    await estop_at(
        client, printer,
        block_on="G1 F200 X10 Y20 B-1.5",
        position=m114(X=15, Y=10, Z=0.3, A=0, B=-0.25, C=0),
    )

    resp = await client.post("/jog", json={"axis": axis, "distance": 2})
    assert resp.status_code == 200

    assert app.state.queue_worker.resumable
    assert (await client.post("/print/resume")).status_code == 200

