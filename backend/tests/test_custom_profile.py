"""Any axis letters, and the Custom profile edited in the app.

A four-syringe Custom profile (heights Z, A, U, W; plungers B, C, V, K)
goes through everything that moves an axis: processing, calibration, the
M92 on connect, jogs and Lower to zero height, M114 and stop/resume on the
virtual printer. The Custom profile is saved in settings.json, validated
like the editor on the Setup screen does, and refused during a print.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.checkpoint import parse_m114
from backend.config import Config
from backend.database import init_db
from backend.gcode_processor import process_gcode
from backend.main import app, wire
from backend.printer import AxisRange, PrinterProfile, custom_profile
from backend.queue_worker import PrintStatus
from backend.settings import SettingsStore
from backend.virtual_printer import VirtualPrinter
from tests.conftest import make_config
from tests.nozzles import AXES as PRINTESS_AXES
from tests.nozzles import LEFT, PRINTESS_1
from tests.serial_fakes import attach
from tests.test_legacy_modes import FakeSerial
from tests.test_relative_printing import RAW_SAMPLE, from_last, jog, wait_for

# (id, name, height, plunger)
SYRINGES = [
    ("s1", "One", "Z", "B"),
    ("s2", "Two", "A", "C"),
    ("s3", "Three", "U", "V"),
    ("s4", "Four", "W", "K"),
]
NOZZLES = [
    {"id": sid, "name": name, "height_axis": height, "plunger_axis": plunger, "offset_x": 15.0 * i}
    for i, (sid, name, height, plunger) in enumerate(SYRINGES)
]
FOUR = custom_profile(PRINTESS_1, NOZZLES, nozzle_offset_measured=True)
FOUR_AXES = FOUR.axes


def rename(lines: list[str], letters: dict[str, str]) -> list[str]:
    """`lines` with the words of `letters`' keys renamed, and the axes the
    comments name ("; pressurize B")."""
    keys = "".join(letters)
    word, named = re.compile(rf"\b([{keys}])(?=-?\.?\d)"), re.compile(rf"\b([{keys}])\b")
    out = []
    for line in lines:
        code, sep, comment = line.partition(";")
        swap = lambda m: letters[m.group(1)]  # noqa: E731
        out.append(word.sub(swap, code) + sep + named.sub(swap, comment))
    return out


def words(line: str) -> dict[str, str]:
    return dict(re.findall(r"\b([A-Z])(-?\d*\.?\d+)", line.split(";", 1)[0]))


# --- the profile ---------------------------------------------------------------------------


def test_axes_come_from_the_profile():
    assert FOUR_AXES.heights == ("Z", "A", "U", "W")
    assert FOUR_AXES.plungers == ("B", "C", "V", "K")
    assert FOUR_AXES.all == ("X", "Y", "Z", "A", "U", "W", "B", "C", "V", "K")
    assert FOUR_AXES.plunger_name("V") == "three"
    assert FOUR_AXES.describe_plungers() == "B (one), C (two), V (three) and K (four)"
    assert PRINTESS_AXES.describe_plungers() == "B (left) and C (right)"


def test_steps_per_mm_command():
    assert PRINTESS_1.steps_per_mm_command == "M92 X800 Y800 Z800 A800 B800 C800"
    assert FOUR.steps_per_mm_command == "M92 X800 Y800 Z800 A800 U800 W800 B800 C800 V800 K800"
    tuned = PrinterProfile.model_validate({**FOUR.model_dump(), "steps_per_mm": 400})
    assert tuned.steps_per_mm_command == "M92 X400 Y400 Z400 A400 U400 W400 B400 C400 V400 K400"


def test_axis_letters_are_upper_cased():
    profile = custom_profile(PRINTESS_1, [{**NOZZLES[0], "height_axis": "u", "plunger_axis": " v "}], False)
    assert (profile.nozzles[0].height_axis, profile.nozzles[0].plunger_axis) == ("U", "V")


# --- processing ----------------------------------------------------------------------------


@pytest.mark.parametrize("index", range(4))
def test_each_syringe_alone_prints_what_the_left_one_does(index):
    """A syringe on its own prints the Printess 1.0 left nozzle's program,
    on its own height and plunger: Cura's Z → height, E → plunger, negated."""
    sid, _, height, plunger = SYRINGES[index]
    left = process_gcode(RAW_SAMPLE, LEFT, axes=PRINTESS_AXES)

    result = process_gcode(RAW_SAMPLE, FOUR.select([sid]), axes=FOUR_AXES)

    assert result.lines == rename(left.lines, {"Z": height, "B": plunger})
    assert result.extrusion_axes == (plunger,)
    assert result.height_axes == (height,)
    assert result.start_position == {"X": 0.0, "Y": 0.0, height: 0.0}
    assert result.state_before[0].pos == {
        **dict.fromkeys(FOUR_AXES.heights),
        **dict.fromkeys(FOUR_AXES.plungers, 0.0),
        "X": 0.0,
        "Y": 0.0,
        height: 0.0,
    }


def test_all_four_print_the_same_path():
    one = process_gcode(RAW_SAMPLE, FOUR.select(["s1"]), axes=FOUR_AXES)

    result = process_gcode(RAW_SAMPLE, FOUR.select(["s1", "s2", "s3", "s4"]), axes=FOUR_AXES)

    assert result.extrusion_axes == ("B", "C", "V", "K")
    assert result.height_axes == ("Z", "A", "U", "W")
    assert result.start_position == {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0, "U": 0.0, "W": 0.0}
    moves = [words(line) for line in result.lines if line.startswith(("G0", "G1"))]
    for move in moves:
        # Every height move and print move carries all four syringes', with
        # one value (pressurize and retract lines are one per plunger)
        groups = [FOUR_AXES.heights] + ([FOUR_AXES.plungers] if "X" in move or "Y" in move else [])
        for group in groups:
            present = [move[ax] for ax in group if ax in move]
            assert len(present) in (0, 4) and len(set(present)) <= 1, move

    # The same path as the first syringe alone: its print moves, each with
    # the other three syringes' heights and plungers alongside
    def print_moves(lines: list[str], keep: str) -> list[dict[str, str]]:
        moves = [words(line) for line in lines if line.startswith(("G0", "G1"))]
        return [{ax: v for ax, v in m.items() if ax in keep} for m in moves if "X" in m or "Y" in m]

    assert print_moves(result.lines, "GXYZBF") == print_moves(one.lines, "GXYZBF")


def test_a_plunger_called_z_is_not_taken_for_curas_height():
    profile = custom_profile(
        PRINTESS_1, [{"id": "s", "name": "S", "height_axis": "U", "plunger_axis": "Z"}], False
    )

    result = process_gcode(RAW_SAMPLE, profile.select(["s"]), axes=profile.axes)

    left = process_gcode(RAW_SAMPLE, LEFT, axes=PRINTESS_AXES)
    assert result.lines == rename(left.lines, {"Z": "U", "B": "Z"})
    assert result.height_axes == ("U",) and result.extrusion_axes == ("Z",)


# --- the API and the session -------------------------------------------------------------


@pytest.fixture
def published(client):
    events: list[dict] = []
    session = app.state.session
    publish = session._bus_publish

    def both(event: dict) -> None:
        events.append(event)
        publish(event)

    session._bus_publish = both
    return events


async def save_four(client, **extra) -> dict:
    resp = await client.put(
        "/printer/custom", json={"nozzles": NOZZLES, "nozzle_offset_measured": True, **extra}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_saving_the_custom_profile_selects_it(client, published):
    session = app.state.session
    session.zeroed = {"X", "Y", "Z"}

    body = await save_four(client, base="printess-1")

    assert body["id"] == "custom" and body["name"] == "Custom"
    assert [n["height_axis"] for n in body["nozzles"]] == ["Z", "A", "U", "W"]
    assert session.profile.axes == FOUR_AXES
    assert session.zeroed == set()
    assert app.state.serial_manager.axes == FOUR_AXES
    assert app.state.queue_worker.axes == FOUR_AXES
    listed = (await client.get("/printer/profiles")).json()
    assert listed["selected"] == "custom"
    assert [p["id"] for p in listed["profiles"]] == ["printess-1", "custom"]
    assert (await client.get("/printer/custom")).json() == body
    types = [e["type"] for e in published]
    assert "printer_profile" in types and "printer_profiles" in types
    profiles_event = next(e for e in published if e["type"] == "printer_profiles")
    assert profiles_event["selected"] == "custom"


async def test_custom_profile_copies_the_edited_profile(client):
    app.state.session.config = Config(
        profiles={"printess-1": {"bed": {"x": {"min": -45, "max": 45}}, "steps_per_mm": 400}}
    )
    body = await save_four(client, base="printess-1")
    assert body["bed"]["x"] == {"min": -45, "max": 45}
    assert body["steps_per_mm"] == 400


async def test_custom_profile_is_kept_across_restarts(client, data_dir):
    saved = await save_four(client)
    stored = json.loads((data_dir / "settings.json").read_text())
    assert stored["printer"]["selected"] == "custom"
    assert stored["printer"]["custom"]["nozzles"][2]["height_axis"] == "U"
    assert stored["temperature_sensors"] is False  # the other settings are kept

    # A restart: the same settings.json, a new backend
    app.state.history._traffic.stop()
    app.state.db.close()
    wire(app, make_config(), init_db(Path(":memory:")), SettingsStore(data_dir / "settings.json"))

    assert (await client.get("/printer")).json() == saved
    assert app.state.serial_manager.axes == FOUR_AXES


async def test_temperature_setting_keeps_the_custom_profile(client, data_dir):
    await save_four(client)
    assert (await client.put("/settings", json={"temperature_sensors": True})).status_code == 200
    assert SettingsStore(data_dir / "settings.json").printer.custom is not None


async def test_an_invalid_saved_profile_is_dropped(data_dir):
    path = data_dir / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    bad = {**FOUR.model_dump(), "nozzles": [{**NOZZLES[0], "plunger_axis": "Z"}]}
    path.write_text(json.dumps({"printer": {"selected": "custom", "custom": bad}}))

    wire(app, make_config(), init_db(Path(":memory:")), SettingsStore(path))

    assert app.state.session.profile.id == "printess-1"
    assert "custom" not in app.state.session.profiles
    app.state.history._traffic.stop()
    app.state.db.close()


async def test_selected_profile_is_kept_across_restarts(client, data_dir):
    await save_four(client)
    assert (await client.put("/printer", json={"profile": "printess-1"})).status_code == 200
    assert SettingsStore(data_dir / "settings.json").printer.selected == "printess-1"


async def test_reset_to_printess_1(client, published):
    await save_four(client)
    published.clear()

    resp = await client.delete("/printer/custom")

    assert resp.status_code == 200
    assert resp.json()["id"] == "printess-1"
    assert app.state.session.profile.id == "printess-1"
    assert app.state.serial_manager.axes == PRINTESS_AXES
    assert (await client.get("/printer/custom")).json() is None
    assert [p["id"] for p in (await client.get("/printer/profiles")).json()["profiles"]] == ["printess-1"]
    assert {e["type"] for e in published} >= {"printer_profile", "printer_profiles"}


def with_change(index: int, **change) -> list[dict]:
    nozzles = [dict(n) for n in NOZZLES]
    nozzles[index].update(change)
    return nozzles


@pytest.mark.parametrize(
    "nozzles, message",
    [
        (with_change(1, height_axis="Z"), "Z is used twice"),
        (with_change(3, plunger_axis="B"), "B is used twice"),
        (with_change(0, height_axis="X"), "Syringe 1: nozzle 's1': height_axis 'X' isn't one of Z, A, B"),
        (with_change(2, plunger_axis="Y"), "Syringe 3: nozzle 's3': plunger_axis 'Y' isn't one of"),
        (with_change(1, plunger_axis="E"), "plunger_axis 'E' isn't one of"),
        (with_change(1, plunger_axis="Q"), "plunger_axis 'Q' isn't one of"),
        (with_change(3, plunger_axis="W"), "the height and plunger axis must be different"),
        (with_change(0, name=""), "Syringe 1 name: String should have at least 1 character"),
        (with_change(1, id="s1"), "nozzle ids must be different"),
        ([], "at least one syringe"),
        ([{**NOZZLES[0], "id": f"n{i}"} for i in range(7)], "at most 6 syringes"),
    ],
)
async def test_invalid_custom_profiles_are_refused(client, nozzles, message):
    resp = await client.put("/printer/custom", json={"nozzles": nozzles})
    assert resp.status_code == 400
    assert message in resp.json()["detail"]
    assert app.state.session.profile.id == "printess-1"
    assert (await client.get("/printer/custom")).json() is None


async def test_unknown_base_is_refused(client):
    resp = await client.put("/printer/custom", json={"nozzles": NOZZLES, "base": "printess-9"})
    assert resp.status_code == 400
    assert "Unknown printer profile" in resp.json()["detail"]


def test_config_json_cant_use_the_custom_id():
    with pytest.raises(ValueError, match="kept for the profile edited in the app"):
        Config(profiles={"custom": {"name": "Mine", "nozzles": NOZZLES}})


# --- moving the axes -----------------------------------------------------------------------


@pytest.fixture
async def fake_serial(client):
    await save_four(client)
    fake = FakeSerial()
    app.state.session.serial = fake
    return fake


async def test_calibration_g92s(client, fake_serial):
    session = app.state.session
    all_four = ["s1", "s2", "s3", "s4"]
    for sid in all_four:
        await session.calibrate(sid, all_four)
    assert fake_serial.sent == ["G92 X0 Y0 Z0 B0", "G92 A0 C0", "G92 U0 V0", "G92 W0 K0"]
    assert session.calibrated_for(all_four)

    fake_serial.sent.clear()
    await session.calibrate("s3", ["s4", "s3"])  # the leftmost selected sets X/Y
    await session.calibrate("s4", ["s3", "s4"])
    assert fake_serial.sent == ["G92 X0 Y0 U0 V0", "G92 W0 K0"]


async def test_lower_to_zero_height(client, fake_serial):
    session = app.state.session
    await session.calibrate("s3", ["s3", "s4"])
    await session.calibrate("s4", ["s3", "s4"])
    fake_serial.sent.clear()

    assert await session.go_to_origin(lower=True) == ["G90", "G1 X0 Y0 F300", "G1 U0 W0 F300"]


async def test_jog_any_profile_axis(client, fake_serial):
    session = app.state.session
    for axis in ("U", "W", "V", "K", "z"):
        assert await session.jog(axis, 1.0, 300) == axis.upper()
    assert [line for line in fake_serial.sent if line.startswith("G1")] == [
        "G1 U1.0 F300",
        "G1 W1.0 F300",
        "G1 V1.0 F300",
        "G1 K1.0 F300",
        "G1 Z1.0 F300",
    ]
    for axis in ("E", "I", "Q"):
        resp = await client.post("/jog", json={"axis": axis, "distance": 1})
        assert resp.status_code == 400 and "Invalid axis" in resp.json()["detail"]


# --- on the virtual printer ------------------------------------------------------------------


@pytest.fixture
async def four_printer(client):
    await save_four(client)
    printer = VirtualPrinter(planner_depth=2, speed=200, busy_interval_s=0.05, axes=FOUR_AXES.all)
    attach(app.state.serial_manager, printer)
    return printer


def test_virtual_printer_reports_the_profiles_axes():
    printer = VirtualPrinter(speed=float("inf"), sensors={}, busy_interval_s=None, axes=FOUR_AXES.all)
    printer.run_direct("G92 U2 K-1")
    [report, _] = printer.run_direct("M114")
    assert parse_m114(report, FOUR_AXES.all) == {
        "X": 0, "Y": 0, "Z": 0, "A": 0, "U": 2, "W": 0, "B": 0, "C": 0, "V": 0, "K": -1
    }  # fmt: skip


def test_virtual_printer_learns_axes_from_m92():
    printer = VirtualPrinter(speed=float("inf"), sensors={}, busy_interval_s=None)  # the Printess 1.0's
    printer.run_direct(FOUR.steps_per_mm_command)
    printer.run_direct("G91")
    printer.run_direct("G1 W3 J1")
    [report, _] = printer.run_direct("M114")
    assert report.split(" Count")[0] == (
        "X:0.00 Y:0.00 Z:0.00 A:0.00 B:0.00 C:0.00 U:0.00 W:3.00 V:0.00 K:0.00 J:1.00"
    )
    assert printer.steps_per_mm["U"] == 800


async def test_m92_on_connect_and_on_switching_profile(client, monkeypatch):
    printer = VirtualPrinter(speed=float("inf"), sensors={}, busy_interval_s=None)
    monkeypatch.setattr("backend.serial_manager._open_port", lambda port, baud: printer)

    assert (await client.post("/connect", json={"port": "/dev/fake"})).status_code == 200
    assert printer.executed[-1] == "M92 X800 Y800 Z800 A800 B800 C800"

    await save_four(client)
    assert printer.executed[-1] == "M92 X800 Y800 Z800 A800 U800 W800 B800 C800 V800 K800"
    assert {"U", "W", "V", "K"} <= set(app.state.serial_manager.position)


async def upload(client, nozzles: list[str]) -> None:
    resp = await client.post(
        "/upload/gcode",
        params={"nozzles": nozzles},
        files={"file": ("print.gcode", RAW_SAMPLE.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text


async def zero(client, nozzles: list[str]) -> None:
    for nozzle in nozzles:
        resp = await client.post("/calibration/zero", json={"nozzle": nozzle, "nozzles": nozzles})
        assert resp.status_code == 200, resp.text


async def test_stop_and_resume_a_syringe_on_its_own_axes(client, four_printer):
    printer = four_printer
    worker = app.state.queue_worker
    await zero(client, ["s3"])
    await upload(client, ["s3"])

    # Half way along the first layer's second side, as in test_relative_printing
    printer.hold_at("G1 F200 Y10 V-0.5", 0.5)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(printer.held.is_set)
    resp = await client.post("/print/stop")
    assert resp.json() == {"status": "stopped", "resumable": True, "reason": None}
    stop = worker.checkpoint.position
    assert {ax: stop[ax] for ax in ("X", "Y", "U", "V")} == {"X": 20, "Y": 15, "U": 0.3, "V": -0.95}
    assert set(stop) == set(FOUR_AXES.all)  # every axis of the profile, from M114
    assert from_last(printer, "M114")[1:3] == ["G91", "G1 V0.2 F400"]  # the retract

    await jog(client, "U", 3)
    await jog(client, "X", -4)
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    assert from_last(printer, "M114")[:7] == [
        "M114",
        "G91",
        "G1 U5 F300",  # lift the syringe's own height clear
        "G1 X4 F300",
        "G1 U-8 F300",
        "G1 V-0.2 F400",  # undo the retract
        "G1 Y5 V-0.25 F200",  # the rest of the stopped line
    ]
    end = {ax: 0.0 for ax in FOUR_AXES.all} | {"U": 5.5, "V": -4.0}
    assert printer.position == pytest.approx(end, abs=1e-6)


async def test_all_four_print_to_the_end(client, four_printer):
    all_four = ["s1", "s2", "s3", "s4"]
    # Room for every copy of the sample (X 0 to 22): the last is 45 mm over
    app.state.session.profile.bed.x = AxisRange(min=-30, max=70)
    await zero(client, all_four)
    await upload(client, all_four)

    assert (await client.post("/print/start")).status_code == 200
    await wait_for(lambda: app.state.queue_worker.status == PrintStatus.COMPLETED)

    end = {
        "X": 0.0,
        "Y": 0.0,
        **dict.fromkeys(FOUR_AXES.heights, 5.5),
        **dict.fromkeys(FOUR_AXES.plungers, -4.0),
    }
    assert four_printer.position == pytest.approx(end, abs=1e-6)


# --- refused during a print ------------------------------------------------------------------


async def test_profile_changes_refused_while_printing_or_paused(client, four_printer):
    await zero(client, ["s1"])
    await upload(client, ["s1"])
    four_printer.hold_at("G1 F200 Y10 B-0.5", 0.5)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(four_printer.held.is_set)

    for status in ("printing", "paused"):
        if status == "paused":
            assert (await client.post("/print/pause")).status_code == 200
        assert (await client.put("/printer/custom", json={"nozzles": NOZZLES[:2]})).status_code == 409
        assert (await client.delete("/printer/custom")).status_code == 409
        assert (await client.put("/printer", json={"profile": "printess-1"})).status_code == 409
        assert app.state.session.profile.axes == FOUR_AXES
        assert app.state.session.loaded is not None

    four_printer.release()
    assert (await client.post("/print/stop")).status_code == 200


# --- jogging several height axes together ---------------------------------------------------


def last_move(printer: VirtualPrinter) -> str:
    return next(line for line in reversed(printer.executed) if line.startswith("G1"))


async def jog_axes(client, axes: list[str], distance: float):
    return await client.post("/jog", json={"axes": axes, "distance": distance})


async def test_jog_several_height_axes_in_one_block(client, four_printer):
    all_four = ["s1", "s2", "s3", "s4"]
    await zero(client, all_four)

    resp = await jog_axes(client, ["Z", "a", "U", "W"], 2)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "axis": "Z", "axes": ["Z", "A", "U", "W"], "distance": 2}
    assert last_move(four_printer) == "G1 Z2.0 A2.0 U2.0 W2.0 F300"
    assert sum(line.startswith("G1") for line in four_printer.executed) == 1  # one block
    await jog(client, "U", -1.5)  # U at 0.5, the others at 2

    # Each axis is checked: U would leave the bed, so nothing moves
    sent = len(four_printer.executed)
    resp = await jog_axes(client, ["Z", "U", "W"], -1)
    assert resp.status_code == 400
    assert "U to -0.5 mm" in resp.json()["detail"]
    assert not any(line.startswith("G1") for line in four_printer.executed[sent:])
    assert (await jog_axes(client, ["Z", "W"], -1)).status_code == 200
    assert four_printer.position["Z"] == pytest.approx(1) and four_printer.position["U"] == pytest.approx(0.5)


async def test_jog_axes_before_zeroing_is_not_limited(client, four_printer):
    # Unzeroed, the bed's position is unknown: jog freely to find it
    assert (await jog_axes(client, ["U", "W"], -5)).status_code == 200
    assert last_move(four_printer) == "G1 U-5.0 W-5.0 F300"


async def test_printess_1_jogs_both_heights_together(client):
    from tests.test_relative_printing import make_printer

    printer = make_printer()
    attach(app.state.serial_manager, printer)
    await zero_printess(client)
    assert (await jog_axes(client, ["Z", "A"], 1)).status_code == 200
    assert last_move(printer) == "G1 Z1.0 A1.0 F300"
    resp = await jog_axes(client, ["Z", "A"], -2)
    assert resp.status_code == 400 and "Z to -1 mm" in resp.json()["detail"]


async def zero_printess(client) -> None:
    for nozzle in ("left", "right"):
        resp = await client.post("/calibration/zero", json={"nozzle": nozzle, "nozzles": ["left", "right"]})
        assert resp.status_code == 200, resp.text


@pytest.mark.parametrize(
    "body, status, message",
    [
        ({"axes": ["Z", "Z"], "distance": 1}, 400, "given twice"),
        ({"axes": ["Z", "E"], "distance": 1}, 400, "Invalid axis: E"),
        ({"axes": [], "distance": 1}, 400, "No axis"),
        ({"axis": "Z", "axes": ["A"], "distance": 1}, 422, "either axis or axes"),
        ({"distance": 1}, 422, "either axis or axes"),
    ],
)
async def test_bad_jog_requests(client, body, status, message):
    from tests.test_relative_printing import make_printer

    attach(app.state.serial_manager, make_printer())
    resp = await client.post("/jog", json=body)
    assert resp.status_code == status
    assert message in str(resp.json()["detail"])
