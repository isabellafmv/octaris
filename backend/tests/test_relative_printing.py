"""Relative (G91) printing, against the virtual printer.

Every print goes out fully relative: the post-processor converts the
slicer's absolute program as its last step, and a lab file that is relative
already is sent as it is. These tests check that the converted program
moves the printer exactly where the absolute one would have, without
rounding drift, and that the runtime (start, jog, pause, e-stop, resend,
flow override) keeps a relative print where it belongs.
"""

from __future__ import annotations

import asyncio
import math
import random
from decimal import Decimal
from pathlib import Path

import pytest

from backend.gcode_processor import build_print, parse, parse_words, process_gcode, render
from backend.main import app
from backend.queue_worker import PrintStatus
from backend.virtual_printer import VirtualPrinter
from tests.serial_fakes import attach

FIXTURES = Path(__file__).parent / "fixtures"
RAW_SAMPLE = (FIXTURES / "raw_sample.gcode").read_text()
# Where the sample print ends, from the zero point: see test_estop_resume.py
SAMPLE_END = {"X": 0.0, "Y": 0.0, "Z": 5.5, "A": 0.0, "B": -4.0, "C": 0.0}


def cura_like(layers: int = 3, seed: int = 1, extrusion: str = "M82") -> str:
    """A small Cura-style program: absolute X/Y/Z with Cura's 3 decimals,
    E with 5 (absolute after G92 E0, or per move with M83), several
    perimeters per layer joined by in-layer travel (G0 without Z)."""
    rng = random.Random(seed)
    lines = [";FLAVOR:Marlin", ";TIME:120", extrusion, "G92 E0"]
    e = Decimal(0)
    for layer in range(layers):
        z = Decimal("0.2") * (layer + 1)
        for island in range(2):
            x0 = Decimal(rng.randint(-15000, 5000)) / 1000
            y0 = Decimal(rng.randint(-15000, 5000)) / 1000
            size = Decimal(rng.randint(2000, 9000)) / 1000
            travel = f"G0 F600 X{x0} Y{y0}"
            lines.append(f"{travel} Z{z}" if island == 0 else travel)
            for x, y in [(x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size), (x0, y0)]:
                de = Decimal(rng.randint(10000, 90000)) / 100000
                e += de
                lines.append(f"G1 F200 X{x} Y{y} E{e if extrusion == 'M82' else de}")
    return "\n".join([*lines, "M82", ";End of Gcode"])


def run_on_firmware(lines: list[str]) -> list[dict[str, float]]:
    """Run `lines` on a fresh virtual printer, moving instantly; returns the
    physical (machine) position after each move that changed it."""
    printer = VirtualPrinter(speed=math.inf, sensors={}, busy_interval_s=None)
    positions: list[dict[str, float]] = []
    try:
        for line in lines:
            command = line.split(";", 1)[0].strip()
            if not command:
                continue
            printer.run_direct(command)
            if command.startswith(("G0", "G1")):
                where = printer.machine_coordinates()
                if not positions or where != positions[-1]:
                    positions.append(where)
    finally:
        printer.close()
    return positions


# --- the converted program --------------------------------------------------------


@pytest.mark.parametrize("mode", ["left", "right", "both"])
@pytest.mark.parametrize("raw", [RAW_SAMPLE, cura_like()], ids=["sample", "cura_like"])
def test_relative_program_reaches_the_same_positions_as_the_absolute_one(raw, mode):
    # The absolute program is the slicer's own coordinates with the preamble,
    # footer and retracts added: what the printer used to be sent. Both start
    # at the zero point (the virtual printer starts at 0 on every axis).
    absolute, _ = build_print(parse(raw.splitlines()), mode)
    relative = process_gcode(raw, mode).lines

    assert not any(line.startswith("G90") for line in relative)
    expected = run_on_firmware(render(absolute))
    actual = run_on_firmware(relative)
    assert len(actual) == len(expected) > 10
    for got, want in zip(actual, expected, strict=True):
        assert got == pytest.approx(want, abs=1e-9)


def test_relative_extrusion_gives_the_same_program():
    # Cura now slices with relative_extrusion: M83 and per-move E.
    assert process_gcode(cura_like(extrusion="M83"), "left").lines == process_gcode(cura_like(), "left").lines


MOVES = 100_000


def test_no_drift_over_a_100k_move_file():
    """A long Cura-style program with relative E and Cura's resolution. The
    converted moves must add up to the absolute targets: to well under
    0.001 mm on the printer, and exactly in the text sent."""
    rng = random.Random(7)
    lines = ["M83", "G0 F600 X0 Y0 Z0.2"]
    targets = []  # (X, Y, B) in thousandths/hundred-thousandths, exact
    x = y = 0
    e_total = 0
    for _ in range(MOVES):
        x = max(-25000, min(25000, x + rng.choice([-1, 1]) * rng.randint(1, 3000)))
        y = max(-25000, min(25000, y + rng.randint(-3000, 3000)))
        de = rng.randint(1, 20000)
        e_total += de
        lines.append(f"G1 F200 X{x / 1000:.3f} Y{y / 1000:.3f} E{de / 100000:.5f}")
        targets.append((x, y, e_total))

    result = process_gcode("\n".join(lines), "left")
    body = [line for line in result.lines if line.startswith("G1 F200")]
    assert len(body) == MOVES

    # The text: the X distances sent add up to each target exactly.
    sent_x = 0
    for line, (x, _, _) in zip(body, targets, strict=True):
        sent_x += round(Decimal(dict(parse_words(line)).get("X", 0)) * 1000)
        assert sent_x == x

    # The printer: run it all, and compare the position after every move.
    printer = VirtualPrinter(speed=math.inf, sensors={}, busy_interval_s=None)
    worst = 0.0
    try:
        moves = iter(targets)
        for line in result.lines:
            command = line.split(";", 1)[0].strip()
            if not command:
                continue
            printer.run_direct(command)
            if command.startswith("G1 F200"):
                x, y, e = next(moves)
                where = printer.position
                # The plunger pushes E (negated) after the 0.2 mm pressurize
                expected = {"X": x / 1000, "Y": y / 1000, "B": -0.2 - e / 100000}
                worst = max(worst, *(abs(where[ax] - value) for ax, value in expected.items()))
        end = printer.position
    finally:
        printer.close()
    assert worst < 0.001
    assert worst < 1e-6  # in fact: float rounding only
    assert end["X"] == pytest.approx(0, abs=1e-6) and end["Y"] == pytest.approx(0, abs=1e-6)


# --- printing through the app ------------------------------------------------------


def make_printer() -> VirtualPrinter:
    return VirtualPrinter(planner_depth=2, speed=200, busy_interval_s=0.05)


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


async def upload(client, raw: str = RAW_SAMPLE, mode: str = "left") -> None:
    resp = await client.post(
        "/upload/gcode",
        params={"syringe_mode": mode},
        files={"file": ("print.gcode", raw.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text


async def print_to_the_end(client) -> None:
    worker = app.state.queue_worker
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)


async def test_print_starts_from_the_zero_point(client, printer):
    # Jogged away after zeroing: the absolute program's first move would have
    # gone to its first point anyway; the relative one travels back first.
    printer.move_externally(X=7, Y=-3, Z=2)
    await upload(client)
    await print_to_the_end(client)

    assert printer.executed[1:5] == [
        "G91",
        "G1 Z5 F300",  # lift clear
        "G1 X-7 Y3 F300",  # back over the zero point
        "G1 Z-7 F300",  # and down onto it
    ]
    assert printer.position == pytest.approx(SAMPLE_END)


async def test_print_at_the_zero_point_starts_straight_away(client, printer):
    await upload(client)
    await print_to_the_end(client)
    assert printer.executed[:3] == ["M114", "M84 S0", "M110 N0"]
    assert printer.position == pytest.approx(SAMPLE_END)


# --- manual control leaves the positioning mode alone ----------------------------


async def send(client, line: str) -> str:
    resp = await client.post("/gcode/send", json={"line": line})
    assert resp.status_code == 200, resp.text
    return resp.json()["response"]


async def jog(client, axis: str, distance: float) -> None:
    resp = await client.post("/jog", json={"axis": axis, "distance": distance})
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    "mode, jog_lines",
    [
        # (M114 reads the position for the bed limits)
        ("G90", ["M114", "G91", "G1 X2.0 F300", "G90"]),
        ("G91", ["M114", "G91", "G1 X2.0 F300"]),
    ],
)
async def test_jog_without_a_print_restores_the_mode(client, printer, mode, jog_lines):
    await send(client, mode)
    await jog(client, "X", 2)
    assert printer.executed[-len(jog_lines) :] == jog_lines
    assert printer.relative == (mode == "G91")
    assert app.state.serial_manager.relative == (mode == "G91")


async def test_jog_with_a_print_loaded_never_sends_g90(client, printer):
    assert not printer.relative  # Marlin starts absolute
    await upload(client)
    await jog(client, "Y", -1)
    assert printer.executed[-2:] == ["G91", "G1 Y-1.0 F300"]
    assert printer.relative  # the mode every print runs in


@pytest.mark.parametrize("mode", ["G90", "G91"])
async def test_calibration_leaves_the_mode_alone(client, printer, mode):
    await send(client, mode)
    assert (await client.post("/calibration/zero")).status_code == 200
    assert printer.executed[-1] == "G92 X0 Y0 Z0 B0"
    assert printer.relative == (mode == "G91")


# --- e-stop and resume ---------------------------------------------------------------


async def stop_half_way(client, printer: VirtualPrinter, hold: str) -> None:
    printer.hold_at(hold, 0.5)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    resp = await client.post("/print/stop")
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}


def from_last(printer: VirtualPrinter, marker: str) -> list[str]:
    executed = printer.executed
    return executed[len(executed) - 1 - executed[::-1].index(marker) :]


async def test_estop_and_resume_in_g91(client, printer):
    await upload(client)
    worker = app.state.queue_worker
    # Half way along the first layer's second side: X20 Y15, B-0.95
    await stop_half_way(client, printer, "G1 F200 Y10 B-0.5")
    assert worker.checkpoint.position == {"X": 20, "Y": 15, "Z": 0.3, "A": 0, "B": -0.95, "C": 0}
    assert printer.relative  # the retract kept the print's mode

    # Moved out of the way while stopped, as one would to look at the nozzle
    await jog(client, "Z", 3)
    await jog(client, "X", -4)

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    assert from_last(printer, "M114")[:7] == [
        "M114",  # where the head is now: X16 Y15 Z3.3, B-0.75 (retracted)
        "G91",
        "G1 Z5 A5 F300",  # lift clear
        "G1 X4 F300",  # back over the stop point
        "G1 Z-8 A-5 F300",  # down onto it
        "G1 B-0.2 F400",  # undo the retract
        "G1 Y5 B-0.25 F200",  # the rest of the stopped line
    ]
    assert "G90" not in printer.executed
    assert printer.position == pytest.approx(SAMPLE_END, abs=1e-6)


# --- the flow override ------------------------------------------------------------------


def b_distances(lines: list[str]) -> list[float]:
    return [dict(parse_words(line))["B"] for line in lines if dict(parse_words(line)).get("B")]


async def test_flow_override_at_80_percent(client, printer):
    await upload(client)
    assert (await client.post("/extrusion", json={"rate": 80})).status_code == 200
    await print_to_the_end(client)

    planned = [line.split(";")[0].strip() for line in app.state.session.loaded.gcode.lines]
    sent = [line for line in printer.executed if line.startswith(("G0", "G1"))]
    planned_moves = [line for line in planned if line.startswith(("G0", "G1"))]
    # Every move goes out with its B distance scaled, nothing else changed
    assert b_distances(sent) == pytest.approx([0.8 * b for b in b_distances(planned_moves)])
    assert [line.split(" B")[0] for line in sent] == [line.split(" B")[0] for line in planned_moves]
    # So the plunger travels 80% as far, and the stage exactly as far.
    assert printer.position == pytest.approx({**SAMPLE_END, "B": 0.8 * SAMPLE_END["B"]})


async def test_flow_override_changed_mid_print(client, printer):
    await upload(client)
    worker = app.state.queue_worker
    printer.planner_depth = 1
    printer.hold_at("G1 F200 Y10 B-0.5")  # the first layer's second side; the third is in flight
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    assert (await client.post("/extrusion", json={"rate": 80})).status_code == 200
    printer.release()
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    # B-0.2 pressurize and three sides at 100%, then the rest at 80%: one
    # more side, layer change (+0.2 −0.2), four sides, +0.2 depressurize.
    expected_b = -0.2 - 3 * 0.5 - 0.8 * (0.5 + 4 * 0.5 - 0.2)
    assert printer.position == pytest.approx({**SAMPLE_END, "B": expected_b})


# --- pause ------------------------------------------------------------------------------


async def pause_after_first_layer(client, printer: VirtualPrinter) -> dict[str, float]:
    """Start the sample and pause it with the first layer done; returns where
    it paused (X10 Y10 Z0.3, as the layer's last move ends), once idle."""
    worker = app.state.queue_worker
    printer.hold_at("G1 F200 Y-10 B-0.5")  # the layer's last side
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    assert (await client.post("/print/pause")).status_code == 200
    printer.release()
    await wait_for(lambda: worker._paused_at is not None and printer.moves_planned == 0)
    return worker._paused_at


async def test_jog_while_paused_stays_g91_and_resume_returns_to_the_pause_point(client, printer):
    await upload(client)
    worker = app.state.queue_worker
    paused_at = await pause_after_first_layer(client, printer)
    # Recorded with M114 at the pause: after the planned moves (the next line
    # was in flight), before anything else
    assert paused_at == printer.position
    next_line = worker._lines[worker.lines_sent].split(";")[0].strip()
    feed = worker._tracker.state.feed

    await jog(client, "Z", 4)
    await jog(client, "X", 6)
    await jog(client, "Y", -2.5)
    assert printer.relative  # no G90 after a jog while paused
    assert "G90" not in printer.executed

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    back = printer.executed[printer.executed.index("G1 Y-2.5 F300") + 1 :][:9]
    assert back == [
        "M400",
        "M114",  # 6 mm along X, 2.5 back along Y and 4 up from the pause point
        "G91",
        "G1 Z5 A5 F300",  # lift clear
        "G1 X-6 Y2.5 F300",  # back over the pause point
        "G1 Z-9 A-5 F300",  # down onto it
        f"G1 F{feed:g}",  # the print's feed rate
        "G91",  # and mode
        next_line,  # then on with the print
    ]
    # Exactly where the uninterrupted print would have ended
    assert printer.position == pytest.approx(SAMPLE_END)


async def test_head_moved_from_the_display_while_paused_is_brought_back(client, printer):
    await upload(client)
    worker = app.state.queue_worker
    await pause_after_first_layer(client, printer)
    printer.move_externally(X=-3, Z=1)  # no manual command the app knows of

    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert "G1 X3 F300" in printer.executed
    assert printer.position == pytest.approx(SAMPLE_END)


async def test_pause_and_resume_without_moving_sends_no_moves(client, printer):
    await upload(client)
    worker = app.state.queue_worker
    await pause_after_first_layer(client, printer)
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    planned = [line.split(";")[0].strip() for line in worker._lines]
    assert [line for line in printer.executed if line not in planned] == [
        "M114",  # the start
        "M84 S0",
        "M110 N0",
        "M114",  # the pause
        "M400",  # the resume: nothing moved
        "M114",
        "M400",  # the end
    ]
    assert printer.position == pytest.approx(SAMPLE_END)
