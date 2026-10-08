"""E-stop, checkpoint and resume, end to end against the virtual printer.

Each stop is placed by holding the printer's motion part way through a
chosen move: the e-stop lands there, and the printer itself works out where
the axes stopped from what was actually sent (flow scaling included), so
the tests never script an M114 reply.
"""

import asyncio
from pathlib import Path

import pytest

from backend.checkpoint import Checkpoint, build_resume_commands, locate_line, parse_m114
from backend.gcode_processor import MachineState, ProcessedGcode, process_gcode, simulate_states
from backend.main import app
from backend.queue_worker import PrintStatus
from backend.session import LoadedPrint
from backend.virtual_printer import Match, VirtualPrinter
from tests.serial_fakes import attach

FIXTURES = Path(__file__).parent / "fixtures"
RAW_SAMPLE = (FIXTURES / "raw_sample.gcode").read_text()

# Where the sample print ends up, stopped and resumed or not: the footer
# raises Z by 5 and returns to X0 Y0; B ends 0.2 above its last print move
# (8 moves of B-0.5, between the pressurize and the depressurize).
SAMPLE_END = {"X": 0.0, "Y": 0.0, "Z": 5.5, "A": 0.0, "B": -4.0, "C": 0.0}
# The first extrusion move: X10 Y10 B-0.2 → X20 Y10 B-0.7
FIRST_MOVE = "G1 F200 X10 B-0.5"


def make_printer() -> VirtualPrinter:
    # A two-move planner keeps the host just ahead of the motion: when the
    # printer is held mid-move, one more move is planned and the next line
    # is in flight, blocked on the full planner, as the e-stop comes in.
    return VirtualPrinter(planner_depth=2, speed=200, busy_interval_s=0.05)


def after(printer: VirtualPrinter, marker: str) -> list[str]:
    """Commands the printer ran after the last `marker`."""
    executed = printer.executed
    idx = len(executed) - 1 - executed[::-1].index(marker)
    return executed[idx + 1 :]


async def wait_for(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.005)


@pytest.fixture
async def printer(client):
    printer = make_printer()
    attach(app.state.serial_manager, printer)
    app.state.session.calibrated = True
    return printer


@pytest.fixture
def events(client) -> list[dict]:
    """The worker's own events, which are still published as well."""
    recorded: list[dict] = []
    publish = app.state.queue_worker._on_event

    def record(event: dict) -> None:
        recorded.append(event)
        publish(event)

    app.state.queue_worker._on_event = record
    return recorded


async def upload_sample(client, mode: str = "left"):
    resp = await client.post(
        "/upload/gcode",
        params={"syringe_mode": mode},
        files={"file": ("sample.gcode", RAW_SAMPLE.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text


async def estop_at(client, printer: VirtualPrinter, hold: Match, fraction: float = 0.5) -> dict:
    """Start the sample print and e-stop it `fraction` of the way through
    the move whose line contains `hold`."""
    printer.hold_at(hold, fraction)
    await upload_sample(client)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    resp = await client.post("/print/stop")
    assert resp.status_code == 200
    return resp.json()


def on_wire(lines: list[str]) -> list[str]:
    """Print lines as sent numbered: without their comments."""
    return [line.split(";", 1)[0].strip() for line in lines]


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

    # Planned from the zero point the print starts at, relative throughout.
    assert result.state_before[0].pos == {"X": 0, "Y": 0, "Z": 0, "A": None, "B": 0, "C": 0}
    assert all(state.relative for state in result.state_after)

    _, state = after("G1 B-0.2 F400")  # pressurize
    assert state.pos["B"] == -0.2 and state.feed == 400

    before, state = after(FIRST_MOVE)
    assert before.pos == {"X": 10, "Y": 10, "Z": 0.3, "A": None, "B": -0.2, "C": 0}
    assert state.pos == pytest.approx({"X": 20, "Y": 10, "Z": 0.3, "A": None, "B": -0.7, "C": 0})
    assert state.feed == 200

    # Layer change: depressurize, up 0.2, repressurize.
    before, state = after("G1 B0.2 F400")
    assert before.pos["B"] == pytest.approx(-2.2) and state.pos["B"] == pytest.approx(-2.0)
    _, state = after("G0 F400 Z0.2")
    assert state.pos["Z"] == pytest.approx(0.5)

    # Footer: Z raise, then the way back to the origin.
    _, state = after("G1 Z5 F300")
    assert state.pos["Z"] == pytest.approx(5.5)
    final = result.state_after[-1]
    assert final.pos == pytest.approx({**SAMPLE_END, "A": None})


def test_parse_m114():
    reply = "X:15.00 Y:10.00 Z:0.30 A:0.00 B:-0.25 C:0.00 Count X:12000 Y:8000 Z:240\nok"
    assert parse_m114(reply) == {
        "X": 15.0,
        "Y": 10.0,
        "Z": 0.3,
        "A": 0.0,
        "B": -0.25,
        "C": 0.0,
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


def _segments(before, after, indices=None):
    indices = range(len(before)) if indices is None else indices
    return [(i, before[i], after[i]) for i in indices]


@pytest.mark.parametrize("b, expected_line", [(-0.5, 2), (-2.5, 4), (-2.47, 4)])
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
    assert locate_line(segments, SELF_CROSSING, {"X": 5, "Y": 5, "Z": 0.3, "B": -1.5}) is None
    # Off the XY path
    assert locate_line(segments, SELF_CROSSING, {"X": 5, "Y": 5.2, "Z": 0.3, "B": -0.5}) is None
    # Only lines that were actually sent are considered
    assert (
        locate_line(
            _segments(before, after, [0, 1, 2, 3]),
            SELF_CROSSING,
            {"X": 5, "Y": 5, "Z": 0.3, "B": -2.5},
        )
        is None
    )


async def test_estop_locates_line_and_retracts(client, printer, events):
    result = await estop_at(client, printer, FIRST_MOVE)

    worker = app.state.queue_worker
    k = worker_index(FIRST_MOVE)
    # As actually sent: unlike the planned path, A is known here too — the
    # print-start M114 seed gives every axis a known value from line one.
    assert result == {"status": "stopped", "resumable": True, "reason": None}
    checkpoint = worker.checkpoint
    assert checkpoint.line == k
    assert checkpoint.position == {"X": 15, "Y": 10, "Z": 0.3, "A": 0, "B": -0.45, "C": 0}
    assert checkpoint.after.pos == pytest.approx({"X": 20, "Y": 10, "Z": 0.3, "A": 0, "B": -0.7, "C": 0})
    assert checkpoint.after.feed == 200 and checkpoint.after.relative
    assert checkpoint.retract == {"B": 0.2}
    # M410 went out while line k+2 was in flight, blocked on the full
    # planner; the printer dropped it, and the position is read after it.
    assert printer.executed[-7] == "G1 F200 X-10 B-0.5"
    assert after(printer, "M410") == ["M400", "M114", "G91", "G1 B0.2 F400", "G90"]
    assert printer.position["B"] == pytest.approx(-0.25)  # retracted
    assert {"type": "stop", "resumable": True, "reason": None, "line": k} in events

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["end_reason"] == "stopped"
    assert session["resume_line"] == k


async def test_estop_without_retract(client, printer):
    app.state.queue_worker._retract_on_estop = False
    await estop_at(client, printer, FIRST_MOVE)

    assert after(printer, "M410") == ["M400", "M114"]
    assert app.state.queue_worker.checkpoint.retract == {}


async def test_estop_off_path_is_not_resumable(client, printer, events):
    printer.hold_at(FIRST_MOVE, 0.5)
    await upload_sample(client)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    # Someone moves the stage from the printer's own display.
    printer.move_externally(X=35, Y=40)
    resp = await client.post("/print/stop")
    result = resp.json()

    assert result["resumable"] is False
    assert "(X50 Y50 Z0.3 A0 B-0.45 C0) is not on any recently sent line" in result["reason"]
    assert app.state.queue_worker.checkpoint is None
    # Nothing is retracted for a stop that can't be resumed.
    assert after(printer, "M410") == ["M400", "M114"]
    stop_events = [e for e in events if e["type"] == "stop"]
    assert stop_events == [{"type": "stop", "resumable": False, "reason": result["reason"]}]
    assert any(e["type"] == "error" and result["reason"] in e["message"] for e in events)

    resp = await client.post("/print/resume")
    assert resp.status_code == 409
    assert "not on any recently sent line" in resp.json()["detail"]

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["resume_line"] is None


async def test_resume_without_stop_is_409(client, printer):
    assert (await client.post("/print/resume")).status_code == 409


# --- flow override & fully-relative files -----------------------------------
#
# The checkpoint is located against what was actually written to the
# printer (after the flow override scaling), not the planned path, and a
# fully relative (G91) file is resumable because the print-start M114 seed
# gives every axis a known absolute reference before any line is sent.


async def test_estop_at_flow_override_is_resumable_at_scaled_position(client, printer):
    await upload_sample(client)
    assert (await client.post("/extrusion", json={"rate": 80})).status_code == 200
    # Line k is planned as B-0.5 and sent as B-0.4 at 80% flow, after the
    # pressurize, planned as B-0.2 and sent as B-0.16.
    result = await estop_at(client, printer, "X10 B-0.4")
    assert result == {"status": "stopped", "resumable": True, "reason": None}

    worker = app.state.queue_worker
    k = worker_index(FIRST_MOVE)
    assert worker.checkpoint.line == k
    assert worker.checkpoint.position["B"] == pytest.approx(-0.36)  # midway along the scaled move
    assert worker.checkpoint.after.pos["B"] == pytest.approx(-0.56)  # scaled, not -0.7

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    # The resume commands put B at the scaled value, not the planned one.
    resumed = after(printer, "M410")[5:]
    assert resumed[5] == "G1 B-0.36 F400"  # undo retract, at the scaled checkpoint position
    assert resumed[6] == "G1 X20 Y10 Z0.3 A0 B-0.56 C0 F200"  # finish the line, scaled target
    assert resumed[8] == "G1 F200 Y10 B-0.4"  # the next line, still scaled


async def test_flow_change_mid_recent_window_still_locates(client, printer):
    # Lines up to k+1 go out at 100% flow, line k+2 at 80%, and the stop
    # lands half way along line k+2. Each recorded segment must reflect the
    # flow active when *it* was sent: scaling them all by the rate at stop
    # time would put line k+2's start at B-0.96 instead of B-1.2, and the
    # stop (at B-1.4) off its path.
    printer.planner_depth = 1  # line k+1 in flight while held on line k
    printer.hold_at(FIRST_MOVE)
    await upload_sample(client)
    worker = app.state.queue_worker

    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    assert (await client.post("/extrusion", json={"rate": 80})).status_code == 200
    printer.hold_at("X-10 B-0.4", 0.5)
    printer.release()
    await wait_for(printer.held.is_set)

    resp = await client.post("/print/stop")
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}

    k = worker_index("G1 F200 X-10 B-0.5")
    assert worker.checkpoint.line == k
    assert worker.checkpoint.position["B"] == pytest.approx(-1.4)
    assert worker.checkpoint.after.pos["B"] == pytest.approx(-1.6)


G91_ONLY = [
    "G91",
    "G1 X5 Y5 B-0.5 F200",
    "G1 X5 Y0 B-0.5 F200",
    "G1 X0 Y5 B-0.5 F200",
    # Two more, so the stop comes while lines are still being sent: one
    # planned behind line 3, one in flight.
    "G1 X-5 Y0 B-0.5 F200",
    "G1 X-5 Y0 B-0.5 F200",
]


async def test_g91_only_file_resumable_after_seed(client, printer):
    # No G90/absolute move anywhere in this file — without the print-start
    # M114 seed, every axis would stay None forever (nothing to accumulate
    # relative deltas from) and the stop could never be located.
    app.state.session.loaded = LoadedPrint(
        ProcessedGcode(lines=list(G91_ONLY), extrusion_axes=("B",), pressurize_mm=0.2),
        source="gcode",
    )
    printer.hold_at("X0 Y5 B-0.5", 0.5)

    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    resp = await client.post("/print/stop")
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}

    worker = app.state.queue_worker
    k = worker_index("G1 X0 Y5 B-0.5 F200")
    assert worker.checkpoint.line == k
    # Seeded at 0; after lines 1-2 at X10 Y5 B-1, stopped half way along line 3.
    assert worker.checkpoint.position == {"X": 10, "Y": 7.5, "Z": 0, "A": 0, "B": -1.25, "C": 0}
    assert worker.checkpoint.after.pos == {"X": 10.0, "Y": 10.0, "Z": 0.0, "A": 0.0, "B": -1.5, "C": 0.0}


# --- resuming ----------------------------------------------------------------


async def test_resume_command_sequence(client, printer):
    await estop_at(client, printer, FIRST_MOVE)
    worker = app.state.queue_worker
    k = worker_index(FIRST_MOVE)

    resp = await client.post("/print/resume")
    assert resp.status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    # Skip M400, M114 and the three retract lines that followed the e-stop.
    resumed = after(printer, "M410")[5:]
    assert resumed[:9] == [
        "G91",
        "G1 Z5 A5 F300",  # lift clear of the print
        "G90",
        "G1 X15 Y10 F300",  # back over the stop point
        "G1 Z0.3 A0 F300",  # down to it
        "G1 B-0.45 F400",  # undo the plunger retract
        "G1 X20 Y10 Z0.3 A0 B-0.7 C0 F200",  # finish the stopped line, all axes known
        "G91",  # the print is relative
        "G1 F200 Y10 B-0.5",  # and carry on after it
    ]
    # Every line after k is sent exactly once more, in order, then the drain.
    assert resumed[8:] == [*on_wire(worker._lines[k + 1 :]), "M400"]
    assert worker.lines_sent == worker.lines_total
    # The printer ends where the uninterrupted print would have.
    assert printer.position == pytest.approx(SAMPLE_END)

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["end_reason"] == "completed"
    assert session["resume_line"] is None


async def test_resume_after_a_plunger_only_move(client, printer):
    # Stopped during the layer-change depressurize.
    await estop_at(client, printer, "B0.2 F400")
    worker = app.state.queue_worker
    k = worker_index("G1 B0.2 F400")
    assert worker.checkpoint.line == k

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    resumed = after(printer, "M410")[5:]
    assert resumed[:9] == [
        "G91",
        "G1 Z5 A5 F300",
        "G90",
        "G1 X10 Y10 F300",
        "G1 Z0.3 A0 F300",
        "G1 B-2.1 F400",
        "G1 X10 Y10 Z0.3 A0 B-2 C0 F400",  # the relative line, as an absolute target
        "G91",  # back to relative
        "G0 F400 Z0.2",  # line k+1
    ]
    assert resumed[8:] == [*on_wire(worker._lines[k + 1 :]), "M400"]
    assert printer.position == pytest.approx(SAMPLE_END)


def test_resume_commands_for_non_moving_line():
    _, after = simulate_states(["G90", "G1 X1 Y1 Z1 F250", "M106"])
    cp = Checkpoint(line=2, position={"X": 1, "Y": 1, "Z": 1}, after=after[2])

    assert build_resume_commands(cp) == [
        "G91",
        "G1 Z5 F300",
        "G90",
        "G1 X1 Y1 F300",
        "G1 Z1 F300",
        "G1 X1 Y1 Z1 F250",
    ]

    _, after = simulate_states(["M106"])
    cp = Checkpoint(line=0, position={"X": 1, "Y": 1, "Z": 1}, after=after[0])
    assert build_resume_commands(cp)[-1] == "G1 Z1 F300"


async def test_pause_and_resume_still_work(client, printer):
    await upload_sample(client)
    worker = app.state.queue_worker
    await client.post("/print/start")
    await client.post("/print/pause")
    assert worker.status == PrintStatus.PAUSED
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert printer.executed[0] == "M114"  # start seeds the as-sent tracker first
    assert printer.executed[1] == "M84 S0"
    assert "M410" not in printer.executed
    assert printer.position == pytest.approx(SAMPLE_END)


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
async def test_checkpoint_invalidated(client, printer, events, action):
    await estop_at(client, printer, FIRST_MOVE)
    worker = app.state.queue_worker
    assert worker.resumable

    await INVALIDATING[action](client)

    assert not worker.resumable
    assert worker.stop_reason
    assert events[-2] == {"type": "stop", "resumable": False, "reason": worker.stop_reason}
    assert (await client.post("/print/resume")).status_code == 409


@pytest.mark.parametrize("axis", ["X", "Y", "Z", "A"])
async def test_stage_jog_keeps_checkpoint(client, printer, axis):
    await estop_at(client, printer, FIRST_MOVE)

    resp = await client.post("/jog", json={"axis": axis, "distance": 2})
    assert resp.status_code == 200

    worker = app.state.queue_worker
    assert worker.resumable
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert printer.position == pytest.approx(SAMPLE_END)


# --- the end of a print ------------------------------------------------------------


async def test_stop_during_the_last_planned_moves_is_resumable(client, printer):
    # A real planner holds 16 moves: every line can be sent while the printer
    # still has the last ones to run. The print isn't done until they have.
    printer.planner_depth = 16
    printer.hold_at("X-10 Y-10 F300", 0.5)  # half way back to the origin, the last line
    await upload_sample(client)
    worker = app.state.queue_worker
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    await wait_for(lambda: worker.lines_sent == worker.lines_total)

    assert worker.status == PrintStatus.PRINTING
    [session] = (await client.get("/history")).json()["sessions"]
    assert session["end_reason"] is None

    resp = await client.post("/print/stop")
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}
    assert worker.checkpoint.line == worker.lines_total - 1
    assert worker.checkpoint.position == pytest.approx({"X": 5, "Y": 5, "Z": 5.5, "A": 0, "B": -4, "C": 0})

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert printer.position == pytest.approx(SAMPLE_END)


async def test_completed_once_the_printer_has_finished_moving(client, printer):
    printer.planner_depth = 16
    printer.hold_at("X-10 Y-10 F300", 0.5)
    await upload_sample(client)
    worker = app.state.queue_worker
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    await wait_for(lambda: worker.lines_sent == worker.lines_total)
    await asyncio.sleep(0.1)
    assert worker.status == PrintStatus.PRINTING

    printer.release()
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert printer.moves_planned == 0
    assert printer.executed[-1] == "M400"
    [session] = (await client.get("/history")).json()["sessions"]
    assert session["end_reason"] == "completed"


# --- manual commands while paused ------------------------------------------------------


# A fully relative print: a manual jog (which ends in G90) mustn't turn its
# remaining moves into absolute ones, nor shift them.
RELATIVE_MOVES = ["G91", *[f"G1 X1 B-0.{i} F600" for i in range(1, 9)]]
RELATIVE_END = {"X": 8.0, "Y": 0.0, "Z": 0.0, "A": 0.0, "B": -3.6, "C": 0.0}


async def pause_relative_print(client, printer: VirtualPrinter) -> None:
    """Start RELATIVE_MOVES and pause it after the 5th move, with the
    printer idle."""
    app.state.session.loaded = LoadedPrint(
        ProcessedGcode(lines=list(RELATIVE_MOVES), extrusion_axes=("B",), pressurize_mm=0.2),
        source="gcode",
    )
    worker = app.state.queue_worker
    printer.hold_at("X1 B-0.3", 0.5)  # moves 3 and 4 planned, 5 in flight
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    assert (await client.post("/print/pause")).status_code == 200
    printer.release()
    await wait_for(lambda: worker.lines_sent == 6 and printer.moves_planned == 0)


async def test_jog_while_paused_in_a_relative_block_is_undone(client, printer):
    await pause_relative_print(client, printer)
    worker = app.state.queue_worker
    assert (await client.post("/jog", json={"axis": "X", "distance": 3})).status_code == 200
    assert (await client.post("/jog", json={"axis": "Y", "distance": -2})).status_code == 200

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    # Back over where it paused (X5 after five moves), then relative again.
    restore = printer.executed[printer.executed.index("M400") :]
    assert restore[:9] == [
        "M400",
        "M114",
        "G91",
        "G1 Z5 A5 F300",
        "G90",
        "G1 X5 Y0 F300",
        "G1 Z0 A0 F300",
        "G1 F600",
        "G91",
    ]
    assert printer.position == pytest.approx(RELATIVE_END)


async def test_stop_on_the_first_line_after_a_paused_jog_is_resumable(client, printer):
    await pause_relative_print(client, printer)
    worker = app.state.queue_worker
    assert (await client.post("/jog", json={"axis": "Y", "distance": 4})).status_code == 200
    printer.hold_at("X1 B-0.6", 0.5)

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(printer.held.is_set)
    resp = await client.post("/print/stop")
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}
    assert worker.checkpoint.line == RELATIVE_MOVES.index("G1 X1 B-0.6 F600")

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert printer.position == pytest.approx(RELATIVE_END)


async def test_plunger_moved_while_paused_stays_where_it_is(client, printer):
    await pause_relative_print(client, printer)
    worker = app.state.queue_worker
    assert (await client.post("/jog", json={"axis": "B", "distance": -0.5})).status_code == 200
    primed = printer.position["B"]  # commanded; the jog may still be running

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    restore = printer.executed[printer.executed.index("M400") :][:5]
    # Nothing on the stage moved: no return moves, the plunger is re-aligned.
    assert restore == ["M400", "M114", "G92 B-1.5", "G1 F600", "G91"]
    assert printer.position == pytest.approx(RELATIVE_END)  # the print's coordinates
    # ...while the plunger itself is 0.5 further on than the print alone
    # pushed it: M114's raw step counts (80 steps/mm) don't follow G92.
    m114 = (await client.post("/gcode/send", json={"line": "M114"})).json()["response"]
    steps = int(m114.split("Count", 1)[1].split("B:")[1].split()[0])
    assert steps / 80 == pytest.approx(primed - (3.6 - 1.5))


async def test_resume_without_manual_commands_sends_nothing_extra(client, printer):
    await pause_relative_print(client, printer)
    worker = app.state.queue_worker
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    assert printer.executed.count("M114") == 1  # only the one at the start
    assert printer.executed[-1] == "M400"  # the drain before COMPLETED
    assert printer.position == pytest.approx(RELATIVE_END)


async def test_unknown_return_position_pauses_again(client, printer, events):
    await pause_relative_print(client, printer)
    worker = app.state.queue_worker
    tracked = worker._tracker.state
    # As if the print's starting position couldn't be read.
    worker._tracker.state = MachineState(pos={**tracked.pos, "X": None}, relative=True)
    assert (await client.post("/jog", json={"axis": "X", "distance": 3})).status_code == 200
    sent_before = len(printer.executed)

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.PAUSED)
    assert any(
        e["type"] == "error" and "where the print left off isn't known (X)" in e["message"] for e in events
    )
    # Only the position read went out, no print line.
    assert printer.executed[sent_before:] == ["M400", "M114"]
    assert worker.lines_sent == 6

    # Retried on the next resume.
    worker._tracker.state = tracked
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert printer.position == pytest.approx(RELATIVE_END)
