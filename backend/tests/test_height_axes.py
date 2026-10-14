"""Each nozzle has its own height motor: Z left, A right (plungers B, C).

Cura only writes Z. The post-processor maps it onto the nozzle(s) in use,
calibration zeroes each nozzle's height with that nozzle on the bed, and
the bed limits, e-stop resume and pause return work on the height axes of
the nozzles printing.
"""

from __future__ import annotations

import pytest

from backend.checkpoint import Checkpoint, build_resume_commands, build_return_commands
from backend.gcode_processor import MachineState, parse_words, process_gcode
from backend.limits import LimitError, check_jog, check_path, start_state
from backend.main import app
from backend.printer import AxisRange, BedLimits
from backend.queue_worker import PrintStatus
from tests.nozzles import PRINTESS_1
from tests.serial_fakes import attach
from tests.test_relative_printing import (
    RAW_SAMPLE,
    cura_like,
    jog,
    make_printer,
    stop_half_way,
    upload,
    wait_for,
)

LAYERS = 4


def words(line: str) -> dict[str, float]:
    return dict(parse_words(line)[1:])


# --- the processed program ---------------------------------------------------------------


@pytest.mark.parametrize(
    "ids, moved, untouched",
    [(["left"], ("Z",), "A"), (["right"], ("A",), "Z"), (["left", "right"], ("Z", "A"), None)],
)
def test_every_layer_moves_the_height_axes_of_the_nozzles_in_use(ids, moved, untouched):
    lines = process_gcode(cura_like(layers=LAYERS), PRINTESS_1.select(ids)).lines
    layer_moves = [line for line in lines if line.startswith("G0") and any(ax in words(line) for ax in "ZA")]
    # The first layer's approach, then one per layer change
    assert len(layer_moves) == LAYERS
    for line in layer_moves:
        heights = {ax: value for ax, value in words(line).items() if ax in "ZA"}
        assert tuple(heights) == moved, line
        assert len(set(heights.values())) == 1  # both nozzles rise together
    if untouched:
        assert not any(untouched in words(line) for line in lines if line.startswith(("G0", "G1")))


@pytest.mark.parametrize(
    "ids, raise_line",
    [(["left"], "G1 Z5 F300"), (["right"], "G1 A5 F300"), (["left", "right"], "G1 Z5 A5 F300")],
)
def test_footer_raises_the_nozzles_in_use(ids, raise_line):
    lines = process_gcode(RAW_SAMPLE, PRINTESS_1.select(ids)).lines
    assert f"{raise_line} ; raise nozzle" in lines


@pytest.mark.parametrize(
    "ids, plungers", [(["left"], ["B"]), (["right"], ["C"]), (["left", "right"], ["B", "C"])]
)
def test_layer_change_depressurize_fires_for_every_selection(ids, plungers):
    lines = process_gcode(cura_like(layers=LAYERS), PRINTESS_1.select(ids)).lines
    starts = [i for i, line in enumerate(lines) if line == "; layer change — depressurize"]
    assert len(starts) == LAYERS - 1
    for i in starts:
        depressurize = lines[i + 1 : i + 1 + len(plungers)]
        assert depressurize == [f"G1 {ax}0.2 F400" for ax in plungers]
        layer_change = lines[i + 1 + len(plungers)]
        assert layer_change.startswith("G0")
        assert {ax for ax in "ZA" if ax in words(layer_change)} == set(
            PRINTESS_1.nozzle(n).height_axis for n in ids
        )


@pytest.mark.parametrize(
    "ids, start, height_axes",
    [
        (["left"], {"X": 0, "Y": 0, "Z": 0}, ("Z",)),
        (["right"], {"X": 0, "Y": 0, "A": 0}, ("A",)),
        (["left", "right"], {"X": 0, "Y": 0, "Z": 0, "A": 0}, ("Z", "A")),
    ],
)
def test_start_point_and_height_axes(ids, start, height_axes):
    result = process_gcode(RAW_SAMPLE, PRINTESS_1.select(ids))
    assert result.start_position == start
    assert result.height_axes == height_axes


# --- calibration -----------------------------------------------------------------------


@pytest.fixture
async def printer(client):
    printer = make_printer()
    attach(app.state.serial_manager, printer)
    app.state.session.profile.nozzle_offset_measured = True
    # Room for the sample, as in the other height-axis tests
    app.state.session.profile.bed = BedLimits(x=AxisRange(min=-30, max=60))
    return printer


async def zero(client, nozzle: str, nozzles: list[str]) -> dict:
    resp = await client.post("/calibration/zero", json={"nozzle": nozzle, "nozzles": nozzles})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize(
    "ids, nozzle, command, nozzles",
    [
        (["left"], "left", "G92 X0 Y0 Z0 B0", {"left": True, "right": False}),
        (["right"], "right", "G92 X0 Y0 A0 C0", {"left": False, "right": True}),
    ],
)
async def test_single_nozzle_calibration(client, printer, ids, nozzle, command, nozzles):
    assert await zero(client, nozzle, ids) == {"command": command, "calibrated": True, "nozzles": nozzles}
    assert printer.executed[-1] == command
    status = (await client.get("/calibration/status")).json()
    assert status == {"calibrated": True, "nozzles": nozzles}


async def test_two_nozzles_calibrate_in_two_steps(client, printer):
    published: list[dict] = []
    app.state.session._bus_publish = published.append

    # (a) The left nozzle on the bed over the start point
    first = await zero(client, "left", ["left", "right"])
    assert first == {
        "command": "G92 X0 Y0 Z0 B0",
        "calibrated": False,
        "nozzles": {"left": True, "right": False},
    }
    status = (await client.get("/calibration/status")).json()
    assert status == {"calibrated": False, "nozzles": {"left": True, "right": False}}

    # (b) Then the right nozzle lowered onto the bed: its height only, the
    # start point stays where the left nozzle set it.
    second = await zero(client, "right", ["left", "right"])
    assert second == {"command": "G92 A0 C0", "calibrated": True, "nozzles": {"left": True, "right": True}}
    assert printer.executed[-2:] == ["G92 X0 Y0 Z0 B0", "G92 A0 C0"]

    assert [e for e in published if e["type"] == "calibration"] == [
        {"type": "calibration", "value": "uncalibrated", "nozzles": {"left": True, "right": False}},
        {"type": "calibration", "value": "calibrated", "nozzles": {"left": True, "right": True}},
    ]
    snapshot = app.state.session.snapshot()
    assert snapshot.calibrated is True
    assert snapshot.calibrated_nozzles == {"left": True, "right": True}

    app.state.session.reset_calibration()
    assert (await client.get("/calibration/status")).json() == {
        "calibrated": False,
        "nozzles": {"left": False, "right": False},
    }


async def test_two_nozzle_print_needs_both_nozzles_zeroed(client, printer):
    await zero(client, "left", ["left", "right"])
    await upload(client, nozzles=("left", "right"))

    resp = await client.post("/print/start")
    assert resp.status_code == 400
    assert "zero the right nozzle" in resp.json()["detail"]

    await zero(client, "right", ["left", "right"])
    assert (await client.post("/print/start")).status_code == 200


async def test_left_zero_doesnt_calibrate_a_right_print(client, printer):
    await zero(client, "left", ["left"])
    await upload(client, nozzles=("right",))
    resp = await client.post("/print/start")
    assert resp.status_code == 400
    assert "zero the right nozzle" in resp.json()["detail"]


# --- limits ---------------------------------------------------------------------------------

BED = BedLimits()


def test_a_has_the_same_jog_limits_as_z():
    check_jog(BED, "A", 10.0, 50.0)
    with pytest.raises(LimitError, match="A to -0.5"):
        check_jog(BED, "A", 0.0, -0.5)
    with pytest.raises(LimitError, match="A to 60.5"):
        check_jog(BED, "A", 60.0, 0.5)


def test_path_check_covers_the_height_axes_in_use():
    start = start_state({"X": 0, "Y": 0, "Z": 0.5, "A": 0.5})
    with pytest.raises(LimitError, match="A to -0.5"):
        check_path(BED, ["G91", "G1 A-1"], start, height_axes=("A",))
    for axis in ("Z", "A"):
        with pytest.raises(LimitError, match=f"{axis} to -0.5"):
            check_path(BED, ["G91", f"G1 {axis}-1"], start, height_axes=("Z", "A"))
    # A right nozzle print doesn't use Z, nor a left one A: not checked
    check_path(BED, ["G91", "G1 Z-1"], start, height_axes=("A",))
    # A left nozzle print doesn't use A, whose zero means nothing for it
    check_path(BED, ["G91", "G1 X1"], start_state({"X": 0, "Y": 0, "Z": 0, "A": -40}))


async def test_a_jog_limits_once_the_right_nozzle_is_zeroed(client, printer):
    assert (await client.post("/jog", json={"axis": "A", "distance": -5})).status_code == 200  # unknown yet
    await zero(client, "right", ["right"])
    resp = await client.post("/jog", json={"axis": "A", "distance": -1})
    assert resp.status_code == 400
    assert "A to -1 mm" in resp.json()["detail"]
    await jog(client, "A", 2)


# --- e-stop resume and pause return -----------------------------------------------------------


@pytest.mark.parametrize(
    "ids, lift, lower",
    [(["right"], "G1 A5 F300", "G1 A-7 F300"), (["left", "right"], "G1 Z5 A5 F300", "G1 Z-7 A-7 F300")],
)
async def test_resume_lifts_the_height_axes_of_the_nozzles(client, printer, ids, lift, lower):
    for nozzle in ids:
        await zero(client, nozzle, ids)
    await upload(client, nozzles=ids)
    worker = app.state.queue_worker
    plunger = "C" if ids == ["right"] else "B"
    await stop_half_way(client, printer, f"G1 F200 Y10 {plunger}-0.5")

    # Raised out of the way while stopped
    for axis in ("Z", "A"):
        await jog(client, axis, 2)
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    resume = printer.executed[len(printer.executed) - 1 - printer.executed[::-1].index("M114") :]
    # Only the heights moved: lift, no X/Y travel, lower onto the stop point
    assert resume[1:4] == ["G91", lift, lower]
    end = printer.position
    if ids == ["right"]:
        # The left nozzle's height doesn't print: left where it was jogged
        assert end["Z"] == pytest.approx(2) and end["A"] == pytest.approx(5.5, abs=1e-6)
    else:
        assert end["Z"] == pytest.approx(5.5, abs=1e-6) and end["A"] == pytest.approx(5.5, abs=1e-6)


@pytest.mark.parametrize("height_axes", [("Z",), ("A",), ("Z", "A")])
def test_resume_commands_lift_the_given_height_axes(height_axes):
    after = MachineState(pos={"X": 1, "Y": 1, "Z": 0.3, "A": 0.3, "B": -1, "C": -1}, relative=True)
    stop = {"X": 0.5, "Y": 1, "Z": 0.3, "A": 0.3, "B": -1, "C": -1}
    moved = {**stop, "X": 3}
    cp = Checkpoint(line=0, position=stop, after=after, height_axes=height_axes)
    lift = " ".join(f"{ax}5" for ax in height_axes)
    lower = " ".join(f"{ax}-5" for ax in height_axes)
    assert build_resume_commands(cp, moved)[:4] == [
        "G91",
        f"G1 {lift} F300",
        "G1 X-2.5 F300",
        f"G1 {lower} F300",
    ]


@pytest.mark.parametrize("height_axes", [("Z",), ("A",), ("Z", "A")])
def test_pause_return_lifts_the_given_height_axes(height_axes):
    paused_at = {"X": 5, "Y": 5, "Z": 1, "A": 1, "B": -2, "C": -2}
    jogged = {**paused_at, "Y": 9, "Z": 4, "A": 4}  # both heights raised
    target = MachineState(pos={ax: float(v) for ax, v in paused_at.items()}, relative=True, feed=200)
    commands = build_return_commands(jogged, paused_at, target, restore_modes=False, height_axes=height_axes)
    lift = " ".join(f"{ax}5" for ax in height_axes)
    lower = " ".join(f"{ax}-8" for ax in height_axes)
    assert commands == ["G91", f"G1 {lift} F300", "G1 Y-4 F300", f"G1 {lower} F300", "G1 F200", "G91"]
