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
