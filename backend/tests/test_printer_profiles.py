"""Printer profiles (GET/PUT /printer) and nozzle selections."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend import slicer
from backend.config import Config
from backend.main import app
from backend.printer import BuildVolume, PrinterProfile
from backend.slicer import SlicingError
from tests.serial_fakes import attach
from tests.test_safety import FakePrinter, upload_sample

LEFT_ONLY = {
    "name": "One nozzle",
    "nozzles": [{"id": "only", "name": "Only", "height_axis": "Z", "plunger_axis": "B"}],
    "bed": {"x": {"min": -10, "max": 10}},
    "build_volume": {"x": 20, "y": 20, "z": 20},
}


@pytest.fixture
async def printer(client):
    fake = FakePrinter()
    attach(app.state.serial_manager, fake)
    return fake


def with_second_profile() -> None:
    """Make a one-nozzle profile "one" available, Printess 1.0 still selected."""
    app.state.session.config = Config(profiles={"one": LEFT_ONLY})


# --- profiles in config.json ----------------------------------------------------------


def test_builtin_printess_1():
    profile = Config().profile
    assert profile.name == "Printess 1.0"
    assert [(n.id, n.height_axis, n.plunger_axis, n.offset_x) for n in profile.nozzles] == [
        ("left", "Z", "B", 0.0),
        ("right", "A", "C", 30.0),
    ]
    assert profile.build_volume == BuildVolume(x=60, y=60, z=60)


def test_config_overrides_part_of_a_builtin_profile():
    config = Config(
        profiles={"printess-1": {"bed": {"x": {"min": -45, "max": 45}}, "nozzle_offset_measured": True}}
    )
    assert (config.profile.bed.x.min, config.profile.bed.y.min) == (-45, -30)
    assert config.profile.nozzle_offset_measured
    assert config.profile.nozzle_ids == ["left", "right"]  # untouched


def test_config_adds_and_selects_a_profile():
    config = Config(printer_profile="one", profiles={"one": LEFT_ONLY})
    assert config.profile.id == "one"
    assert config.profile.nozzle_ids == ["only"]
    assert set(config.profiles) == {"printess-1", "printess-2", "one"}


@pytest.mark.parametrize(
    "nozzles, message",
    [
        ([], "at least 1"),
        ([{"id": "a", "name": "A", "height_axis": "Q", "plunger_axis": "B"}], "height_axis 'Q'"),
        (
            [
                {"id": "a", "name": "A", "height_axis": "Z", "plunger_axis": "B"},
                {"id": "b", "name": "B", "height_axis": "Z", "plunger_axis": "C"},
            ],
            "its own height and plunger axis",
        ),
        (
            [
                {"id": "a", "name": "A", "height_axis": "Z", "plunger_axis": "B"},
                {"id": "a", "name": "B", "height_axis": "A", "plunger_axis": "C"},
            ],
            "must be different",
        ),
    ],
)
def test_invalid_profiles_are_refused(nozzles, message):
    with pytest.raises(ValidationError, match=message):
        PrinterProfile(id="x", name="X", nozzles=nozzles)


def test_unknown_selected_profile_is_refused():
    with pytest.raises(ValidationError, match="isn't a profile"):
        Config(printer_profile="printess-9")


def test_selection_is_put_left_to_right():
    profile = Config().profile
    assert [n.id for n in profile.select(["right", "left"])] == ["left", "right"]
    for bad, message in ((["left", "left"], "twice"), (["middle"], "Unknown nozzle"), ([], "at least one")):
        with pytest.raises(ValueError, match=message):
            profile.select(bad)


# --- the API ------------------------------------------------------------------------------


async def test_get_printer_is_the_active_profile(client):
    resp = await client.get("/printer")
    assert resp.status_code == 200
    assert resp.json() == Config().profile.model_dump()


async def test_list_profiles(client):
    with_second_profile()
    body = (await client.get("/printer/profiles")).json()
    assert body["selected"] == "printess-1"
    assert [p["id"] for p in body["profiles"]] == ["printess-1", "printess-2", "one"]


async def test_switching_profile_resets_calibration_checkpoint_and_print(client, printer):
    with_second_profile()
    session = app.state.session
    assert (await client.post("/calibration/zero", json={"nozzles": ["left"]})).status_code == 200
    assert (await upload_sample(client)).status_code == 200
    published: list[dict] = []
    session._bus_publish = published.append
    reasons: list[str] = []
    session.worker.invalidate_checkpoint = reasons.append

    resp = await client.put("/printer", json={"profile": "one"})

    assert resp.status_code == 200
    assert resp.json()["id"] == "one"
    assert session.zeroed == set() and session.loaded is None
    assert reasons == ["The printer profile was changed"]
    assert session.snapshot().nozzles == ["only"]
    assert session.snapshot().calibrated_nozzles == {"only": False}
    assert {"type": "printer_profile", "printer": session.profile.model_dump()} in published
    assert (await client.get("/printer")).json()["id"] == "one"


async def test_switching_to_an_unknown_profile_is_refused(client):
    resp = await client.put("/printer", json={"profile": "nope"})
    assert resp.status_code == 400
    assert "Unknown printer profile" in resp.json()["detail"]


async def test_switching_profile_refused_during_a_print(client, printer):
    with_second_profile()
    await client.post("/calibration/zero", json={"nozzles": ["left"]})
    await upload_sample(client)
    assert (await client.post("/print/start")).status_code == 200

    assert (await client.put("/printer", json={"profile": "one"})).status_code == 409
    await client.post("/print/stop")


# --- nozzle selections -----------------------------------------------------------------------


@pytest.mark.parametrize("nozzles", [["middle"], ["left", "left"]])
async def test_upload_refuses_a_bad_selection(client, nozzles):
    resp = await upload_sample(client, nozzles=tuple(nozzles))
    assert resp.status_code == 400


async def test_upload_defaults_to_the_first_nozzle(client):
    resp = await client.post(
        "/upload/gcode", files={"file": ("p.gcode", b"G91\nG1 X1 B-0.1 F100", "text/plain")}
    )
    assert resp.status_code == 200
    assert app.state.session.loaded.nozzles == ("left",)


async def test_calibrating_an_unselected_nozzle_is_refused(client, printer):
    resp = await client.post("/calibration/zero", json={"nozzle": "right", "nozzles": ["left"]})
    assert resp.status_code == 400
    assert "isn't selected" in resp.json()["detail"]
    assert printer.written == []


async def test_snapshot_has_the_profile_and_selection(client, printer):
    await client.post("/calibration/zero", json={"nozzle": "right", "nozzles": ["right"]})
    snapshot = (await client.get("/status")).json()
    assert snapshot["printer"]["id"] == "printess-1"
    assert snapshot["nozzles"] == ["right"]
    assert snapshot["calibrated_nozzles"] == {"left": False, "right": True}


# --- the slicer's build volume ------------------------------------------------------------------


def test_model_larger_than_the_build_volume_is_refused(tmp_path):
    stl = tmp_path / "big.stl"
    stl.write_text("solid x\nvertex 0 0 0\nvertex 50 10 10\nendsolid x\n")
    slicer._check_stl_dimensions(stl, BuildVolume(x=60, y=60, z=60))
    with pytest.raises(SlicingError, match="limit 40 mm"):
        slicer._check_stl_dimensions(stl, BuildVolume(x=40, y=60, z=60))
