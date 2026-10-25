"""The built-in Printess 2.0 (four syringes, heights Z A B C, plungers U V W
D, 3200 steps/mm on a ±100 mm bed), the axis letter D, the Custom
profile's steps/mm, and the jog panel's limits, feed rates and plungers."""

from __future__ import annotations

import json

import pytest

from backend.checkpoint import parse_m114
from backend.gcode_processor import MachineState, parse_words, process_gcode, step
from backend.main import app
from backend.printer import BUILTIN_PROFILES, BuildVolume, PrinterProfile, custom_profile
from backend.queue_worker import PrintStatus
from backend.virtual_printer import VirtualPrinter
from tests.nozzles import PRINTESS_1
from tests.serial_fakes import attach
from tests.test_bed_copies import upload_gcode, wide_square
from tests.test_custom_profile import jog_axes, last_move, zero
from tests.test_relative_printing import RAW_SAMPLE, from_last, jog, wait_for
from tests.test_safety import FakePrinter

PRINTESS_2 = PrinterProfile.model_validate(BUILTIN_PROFILES["printess-2"])
AXES = PRINTESS_2.axes
ALL_FOUR = ["syringe-1", "syringe-2", "syringe-3", "syringe-4"]
M92 = "M92 X3200 Y3200 Z3200 A3200 B3200 C3200 U3200 V3200 W3200 D3200"


async def select_printess_2(client) -> None:
    resp = await client.put("/printer", json={"profile": "printess-2"})
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def printer(client):
    """The Printess 2.0 selected, on a virtual printer with its axes."""
    await select_printess_2(client)
    printer = VirtualPrinter(planner_depth=2, speed=200, busy_interval_s=0.05, axes=AXES.all)
    attach(app.state.serial_manager, printer)
    return printer


# --- the profile ---------------------------------------------------------------------------


def test_the_printess_2_profile():
    assert PRINTESS_2.name == "Printess 2.0"
    assert [
        (n.id, n.name, n.height_axis, n.plunger_axis, n.offset_x, n.offset_y) for n in PRINTESS_2.nozzles
    ] == [
        ("syringe-1", "Syringe 1", "Z", "U", 0.0, 0.0),
        ("syringe-2", "Syringe 2", "A", "V", 30.0, 0.0),
        ("syringe-3", "Syringe 3", "B", "W", 60.0, 0.0),
        ("syringe-4", "Syringe 4", "C", "D", 90.0, 0.0),
    ]
    assert PRINTESS_2.nozzle_offset_measured
    assert AXES.all == ("X", "Y", "Z", "A", "B", "C", "U", "V", "W", "D")
    assert PRINTESS_2.steps_per_mm_command == M92
    bed = PRINTESS_2.bed
    assert [(r.min, r.max) for r in (bed.x, bed.y, bed.z)] == [(-100, 100), (-100, 100), (0, 60)]
    assert PRINTESS_2.build_volume == BuildVolume(x=200, y=200, z=60)


def test_printess_1_is_unchanged():
    assert PRINTESS_1.steps_per_mm_command == "M92 X800 Y800 Z800 A800 B800 C800"
    assert PRINTESS_1.build_volume == BuildVolume(x=60, y=60, z=60)


async def test_listed_next_to_printess_1(client):
    body = (await client.get("/printer/profiles")).json()
    assert body["selected"] == "printess-1"
    assert [p["name"] for p in body["profiles"]] == ["Printess 1.0", "Printess 2.0"]


async def test_m92_with_3200_on_select_and_on_connect(client, monkeypatch):
    printer = VirtualPrinter(speed=float("inf"), sensors={}, busy_interval_s=None)
    monkeypatch.setattr("backend.serial_manager._open_port", lambda port, baud: printer)
    assert (await client.post("/connect", json={"port": "/dev/fake"})).status_code == 200
    assert printer.executed[-1] == "M92 X800 Y800 Z800 A800 B800 C800"

    await select_printess_2(client)
    assert printer.executed[-1] == M92
    assert printer.steps_per_mm == dict.fromkeys(AXES.all, 3200)

    await client.post("/disconnect")
    assert (await client.post("/connect", json={"port": "/dev/fake"})).status_code == 200
    assert printer.executed[-1] == M92


async def test_jogs_keep_to_the_bed_once_zeroed(client, printer):
    await zero(client, ["syringe-1"])
    await jog(client, "X", 100)
    resp = await client.post("/jog", json={"axis": "X", "distance": 1})
    assert resp.status_code == 400 and "X to 101 mm" in resp.json()["detail"]
    resp = await client.post("/jog", json={"axis": "Z", "distance": -1})
    assert resp.status_code == 400 and "Z to -1 mm" in resp.json()["detail"]


# --- a print on all four syringes ---------------------------------------------------------------

# A lab G91 file for the first syringe: mirrored to the other three
SYRINGE_1_FILE = "\n".join(["G91", "G92 U0", "G0 Z1 F300", "G1 X{x} U-0.1 F300", "G0 Z-1 F300"])


async def test_a_file_for_syringe_1_is_mirrored_to_all_four(client, printer):
    await zero(client, ALL_FOUR)
    resp = await upload_gcode(client, SYRINGE_1_FILE.format(x=10), ALL_FOUR, needs_changes=False)
    assert resp.status_code == 200, resp.text
    assert app.state.session.loaded.gcode.lines == [
        "G91",
        "G92 U0 V0 W0 D0",
        "G0 Z1 A1 B1 C1 F300",
        "G1 X10 U-0.1 V-0.1 W-0.1 D-0.1 F300",
        "G0 Z-1 A-1 B-1 C-1 F300",
    ]

    assert (await client.post("/print/start")).status_code == 200
    await wait_for(lambda: app.state.queue_worker.status == PrintStatus.COMPLETED)
    end = {**dict.fromkeys(AXES.all, 0.0), "X": 10.0, **dict.fromkeys(AXES.plungers, -0.1)}
    assert printer.position == pytest.approx(end, abs=1e-6)


async def test_each_copy_is_checked_at_print_start(client, printer):
    # Syringe 4 is 90 mm over: X 15 takes its copy to 105 mm
    await zero(client, ALL_FOUR)
    resp = await upload_gcode(client, SYRINGE_1_FILE.format(x=15), ALL_FOUR, needs_changes=False)
    assert resp.status_code == 200, resp.text

    resp = await client.post("/print/start")

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "syringe 4" in detail and "X 105 mm (bed −100 to 100 mm)" in detail
    assert app.state.queue_worker.status != PrintStatus.PRINTING


async def test_each_copy_of_a_converted_print_is_checked_on_upload(client, printer):
    assert (await upload_gcode(client, wide_square(10), ALL_FOUR)).status_code == 200

    resp = await upload_gcode(client, wide_square(15), ALL_FOUR)

    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("The syringe 4 syringe's copy leaves the bed: line ")
    assert "X 105 mm (bed −100 to 100 mm)" in resp.json()["detail"]
    # Without syringe 4 it fits
    assert (await upload_gcode(client, wide_square(15), ALL_FOUR[:3])).status_code == 200


# --- the letter D ------------------------------------------------------------------------------


def test_d_is_parsed_as_an_axis():
    assert parse_words("G1 X1 D-1.5 U-.5 ; D in a comment") == [
        ("G", 1),
        ("X", 1),
        ("D", -1.5),
        ("U", -0.5),
    ]
    state = step(MachineState({"X": 0.0, "D": 0.0}, relative=True), "G1 D-1.5")
    assert state.pos == {"X": 0.0, "D": -1.5}
    assert step(state, "G92 D0").pos["D"] == 0.0


def test_d_in_m114():
    report = "X:1.00 Y:2.00 Z:0.30 A:0.00 B:0.00 C:0.00 U:0.00 V:0.00 W:0.00 D:-1.20 Count X:3200 D:-3840"
    assert parse_m114(report, AXES.all)["D"] == -1.2


def test_syringe_4_extrudes_on_d():
    result = process_gcode(RAW_SAMPLE, PRINTESS_2.select(["syringe-4"]), axes=AXES)
    assert result.extrusion_axes == ("D",) and result.height_axes == ("C",)
    assert any(" D-" in line for line in result.lines)
    assert not any(" U" in line or " Z" in line for line in result.lines)


def test_virtual_printer_has_d():
    printer = VirtualPrinter(speed=float("inf"), sensors={}, busy_interval_s=None)  # the Printess 1.0's
    printer.run_direct("M92 D3200")
    printer.run_direct("G92 D-2")
    [report, _] = printer.run_direct("M114")
    assert parse_m114(report, ["D"])["D"] == -2
    assert printer.steps_per_mm["D"] == 3200


def test_custom_profile_with_d():
    nozzles = [{"id": "s", "name": "S", "height_axis": "d", "plunger_axis": "V"}]
    assert custom_profile(PRINTESS_1, nozzles, False).nozzles[0].height_axis == "D"


async def test_stop_and_resume_on_d(client, printer):
    worker = app.state.queue_worker
    await zero(client, ["syringe-4"])
    resp = await upload_gcode(client, RAW_SAMPLE, ["syringe-4"])
    assert resp.status_code == 200, resp.text

    # Half way along the first layer's second side, as in test_relative_printing
    printer.hold_at("G1 F200 Y10 D-0.5", 0.5)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    resp = await client.post("/print/stop")
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}
    stop = worker.checkpoint.position
    assert {ax: stop[ax] for ax in ("X", "Y", "C", "D")} == {"X": 20, "Y": 15, "C": 0.3, "D": -0.95}
    assert set(stop) == set(AXES.all)
    assert from_last(printer, "M114")[1:3] == ["G91", "G1 D0.2 F400"]  # the retract

    await jog(client, "C", 3)
    await jog(client, "X", -4)
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    assert from_last(printer, "M114")[:7] == [
        "M114",
        "G91",
        "G1 C5 F300",
        "G1 X4 F300",
        "G1 C-8 F300",
        "G1 D-0.2 F400",
        "G1 Y5 D-0.25 F200",
    ]
    end = {ax: 0.0 for ax in AXES.all} | {"C": 5.5, "D": -4.0}
    assert printer.position == pytest.approx(end, abs=1e-6)


# --- steps/mm in the Custom profile -------------------------------------------------------------


async def save(client, **extra):
    nozzles = [n.model_dump() for n in PRINTESS_2.nozzles]
    return await client.put("/printer/custom", json={"nozzles": nozzles, "base": "printess-2", **extra})


@pytest.mark.parametrize("steps", [0, -800])
async def test_steps_per_mm_must_be_above_0(client, steps):
    resp = await save(client, steps_per_mm=steps)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Steps per mm: Input should be greater than 0"
    assert (await client.get("/printer/custom")).json() is None


async def test_steps_per_mm_is_saved_and_sent(client, monkeypatch, data_dir):
    printer = VirtualPrinter(speed=float("inf"), sensors={}, busy_interval_s=None)
    monkeypatch.setattr("backend.serial_manager._open_port", lambda port, baud: printer)
    assert (await client.post("/connect", json={"port": "/dev/fake"})).status_code == 200

    resp = await save(client, steps_per_mm=1600)

    assert resp.status_code == 200, resp.text
    assert resp.json()["steps_per_mm"] == 1600
    assert printer.executed[-1] == "M92 X1600 Y1600 Z1600 A1600 B1600 C1600 U1600 V1600 W1600 D1600"
    stored = json.loads((data_dir / "settings.json").read_text())
    assert stored["printer"]["custom"]["steps_per_mm"] == 1600

    # Left out: the Custom profile's so far
    resp = await client.put("/printer/custom", json={"nozzles": stored["printer"]["custom"]["nozzles"][:2]})
    assert resp.json()["steps_per_mm"] == 1600
    assert printer.executed[-1] == "M92 X1600 Y1600 Z1600 A1600 U1600 V1600"


async def test_steps_per_mm_left_out_is_the_base_profiles(client):
    resp = await save(client)
    assert resp.json()["steps_per_mm"] == 3200


# --- the jog panel -----------------------------------------------------------------------------


@pytest.fixture
async def fake(client):
    fake = FakePrinter()
    attach(app.state.serial_manager, fake)
    return fake


def moves(fake: FakePrinter) -> list[str]:
    return [line for line in fake.written if line.startswith("G1")]


@pytest.mark.parametrize("distance", [200, -200])
async def test_a_200_mm_step_is_accepted(client, fake, distance):
    resp = await client.post("/jog", json={"axis": "X", "distance": distance})
    assert resp.status_code == 200, resp.text
    assert moves(fake) == [f"G1 X{distance:.1f} F300"]


@pytest.mark.parametrize("distance", [201, -201, 200.5])
async def test_a_longer_step_is_refused(client, fake, distance):
    resp = await client.post("/jog", json={"axis": "X", "distance": distance})
    assert resp.status_code == 400
    assert resp.json()["detail"] == f"A jog moves at most 200 mm (not {abs(distance):g} mm)"
    assert moves(fake) == []


async def test_jog_feed_rate_is_sent(client, fake):
    assert (
        await client.post("/jog", json={"axes": ["X"], "distance": 1, "feed_rate": 1200})
    ).status_code == 200
    assert (
        await client.post("/jog", json={"axes": ["Z", "A"], "distance": -1, "feed_rate": 60})
    ).status_code == 200
    assert moves(fake) == ["G1 X1.0 F1200.0", "G1 Z-1.0 A-1.0 F60.0"]


@pytest.mark.parametrize("feed", [0, 0.5, 3001])
async def test_jog_feed_rate_out_of_range_is_refused(client, fake, feed):
    resp = await client.post("/jog", json={"axis": "X", "distance": 1, "feed_rate": feed})
    assert resp.status_code == 400
    assert resp.json()["detail"] == f"Jog must be between 1 and 3000 mm/min (not {feed:g})"
    assert moves(fake) == []


async def test_go_to_origin_uses_the_feed_rates(client, fake):
    app.state.session.zeroed = {"X", "Y", "Z"}
    resp = await client.post("/jog/origin", json={"lower": True, "xy_feed": 1500, "z_feed": 120})
    assert resp.status_code == 200, resp.text
    assert resp.json()["lines"] == ["G90", "G1 X0 Y0 F1500", "G1 Z0 F120"]
    # Left out: 300 as before
    resp = await client.post("/jog/origin", json={"lower": False})
    assert resp.json()["lines"] == ["G90", "G1 X0 Y0 F300"]


@pytest.mark.parametrize("field", ["xy_feed", "z_feed"])
async def test_go_to_origin_feed_rate_out_of_range_is_refused(client, fake, field):
    resp = await client.post("/jog/origin", json={field: 3001})
    assert resp.status_code == 400
    assert "must be between 1 and 3000 mm/min" in resp.json()["detail"]
    assert moves(fake) == []


async def test_several_plungers_jog_in_one_block(client, printer):
    reasons: list[str] = []
    app.state.queue_worker.invalidate_checkpoint = reasons.append

    resp = await jog_axes(client, ["U", "V", "D"], -1)

    assert resp.status_code == 200, resp.text
    assert resp.json()["axes"] == ["U", "V", "D"]
    assert last_move(printer) == "G1 U-1.0 V-1.0 D-1.0 F300"
    assert sum(line.startswith("G1") for line in printer.executed) == 1
    assert reasons == ["The U, V, D plunger was jogged"]
    await wait_for(lambda: printer.position["D"] == pytest.approx(-1))
    assert (await jog_axes(client, ["W", "D"], 2)).status_code == 200
    assert last_move(printer) == "G1 W2.0 D2.0 F300"
