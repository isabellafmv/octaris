"""The nozzle selections ["left"], ["right"] and ["left", "right"] on the
Printess 1.0 do exactly what the syringe modes left, right and both did.

fixtures/legacy_modes.json was recorded from the code before printer
profiles (commit cc115ca), with the inputs it was recorded for: processed
G-code, uploads (converted and as uploaded, with mirroring), the G92 of
each calibration step, Go to Origin / Lower to zero height, and the slicer.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend import slicer
from backend.config import Config
from backend.database import init_db
from backend.gcode_processor import ProcessedGcode, process_gcode
from backend.main import app, wire

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "legacy_modes.json").read_text())
INPUTS = GOLDEN["inputs"]

MODE_NOZZLES = {"left": ["left"], "right": ["right"], "both": ["left", "right"]}
# The one wording that changed: there are no modes any more
OLD_MIRROR_NOTE, NEW_MIRROR_NOTE = "Both mode: copied ", "Copied "


def as_dict(gcode: ProcessedGcode) -> dict:
    return {
        "lines": gcode.lines,
        "feed_log": gcode.feed_log,
        "extrusion_axes": list(gcode.extrusion_axes),
        "height_axes": list(gcode.height_axes),
        "start_position": gcode.start_position,
        "pressurize_mm": gcode.pressurize_mm,
        "warnings": gcode.warnings,
        "time_estimate_s": gcode.time_estimate_s,
    }


def old_wording(result: dict) -> dict:
    if "warnings" in result:
        result["warnings"] = [w.replace(NEW_MIRROR_NOTE, OLD_MIRROR_NOTE, 1) for w in result["warnings"]]
    return result


def selection(mode: str):
    return Config().profile.select(MODE_NOZZLES[mode])


@pytest.mark.parametrize("key", GOLDEN["process"])
def test_processed_gcode_matches_the_old_mode(key):
    name, params, mode = key.split("/")
    gcode = process_gcode(INPUTS["process"][name], selection(mode), **INPUTS["params"][params])

    assert as_dict(gcode) == GOLDEN["process"][key]


@pytest.fixture
def session():
    config = Config(
        profiles={"printess-1": {"bed": {"x": {"min": -100, "max": 100}, "y": {"min": -100, "max": 100}}}}
    )
    wire(app, config, init_db(Path(":memory:")))
    yield app.state.session
    app.state.history._traffic.stop()
    app.state.db.close()


@pytest.mark.parametrize("key", GOLDEN["load"])
def test_upload_matches_the_old_mode(session, key):
    name, needs_changes, mode = key.split("/")
    try:
        result = as_dict(
            session.load_gcode("f.gcode", INPUTS["load"][name], MODE_NOZZLES[mode], needs_changes == "True")
        )
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}

    assert old_wording(result) == GOLDEN["load"][key]


class FakeSerial:
    def __init__(self):
        self.sent: list[str] = []
        self.is_connected = True
        self.relative = False

    async def send(self, line):
        self.sent.append(line)
        return "ok"

    async def send_lines(self, lines):
        self.sent.extend(lines)


@pytest.fixture
def fake_serial(session):
    fake = FakeSerial()
    session.serial = fake
    return fake


@pytest.mark.parametrize("mode", GOLDEN["calibrate"])
async def test_calibration_matches_the_old_mode(session, fake_serial, mode):
    calibrated = []
    for nozzle in MODE_NOZZLES[mode]:
        await session.calibrate(nozzle, MODE_NOZZLES[mode])
        calibrated.append(session.calibrated_for(MODE_NOZZLES[mode]))

    assert fake_serial.sent == GOLDEN["calibrate"][mode]["sent"]
    assert calibrated == GOLDEN["calibrate"][mode]["calibrated_after_each"]


@pytest.mark.parametrize("key", GOLDEN["origin"])
async def test_go_to_origin_matches_the_old_mode(session, fake_serial, key):
    mode, lower = key.split("/")
    session.zeroed = {"X", "Y", "Z", "A"}
    session._calibration_nozzles = tuple(MODE_NOZZLES[mode])

    await session.go_to_origin(lower == "True")

    assert fake_serial.sent == GOLDEN["origin"][key]


@pytest.mark.parametrize("key", GOLDEN["slicer"])
async def test_slicer_matches_the_old_mode(tmp_path, monkeypatch, key):
    suffix, mode = key.split("/")
    model = tmp_path / f"model{suffix}"
    model.write_bytes(b"")
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[cmd.index("-o") + 1]).write_text(INPUTS["process"]["raw"])
        proc = MagicMock(returncode=0, pid=1)
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    monkeypatch.setattr(slicer, "_check_stl_dimensions", lambda path, volume: None)
    monkeypatch.setattr(slicer, "_check_3mf", lambda path: None)
    monkeypatch.setattr(slicer, "_find_cura_engine", lambda: "CuraEngine")
    monkeypatch.setattr(slicer.asyncio, "create_subprocess_exec", fake_exec)
    profile = Config().profile

    gcode = await slicer.slice_model(
        model,
        profile.select(MODE_NOZZLES[mode]),
        nozzle_diameter=0.4,
        print_speed=3.5,
        build_volume=profile.build_volume,
    )

    [cmd] = calls
    args = [Path(a).name if "/" in a else a for a in cmd]
    args[args.index("-o") + 1] = "<out>"
    # The machine size now comes from the profile: the 60 mm the old code
    # left to octaris_settings.json
    size = ["-s", "machine_width=60", "-s", "machine_depth=60", "-s", "machine_height=60"]
    at = args.index(
        "machine_extruder_count=1" if suffix == ".stl" or mode != "both" else "machine_extruder_count=2"
    )
    assert args[at + 1 : at + 7] == size
    del args[at + 1 : at + 7]
    assert args == GOLDEN["slicer"][key]["args"]
    assert gcode.lines == GOLDEN["slicer"][key]["lines"]
