"""Machine settings changed in the app rather than in config.json (which is
read-only in the installed app): the Custom profile's bed limits, saved
with the rest of the profile, and the heater protocol, a setting in
settings.json that applies at once."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import virtual_printer
from backend.database import init_db
from backend.main import app, wire
from backend.queue_worker import PrintStatus
from backend.settings import SettingsStore
from tests.conftest import make_config
from tests.test_custom_profile import NOZZLES, four_printer, save_four, zero  # noqa: F401
from tests.test_print_gate import connect_fake_serial
from tests.test_relative_printing import wait_for

BED = {"x": {"min": -45, "max": 45}, "y": {"min": -40, "max": 40}, "z": {"min": 0, "max": 3}}


def restart(data_dir: Path, config=None) -> None:
    """A new backend on the same settings.json."""
    app.state.history._traffic.stop()
    app.state.db.close()
    wire(app, config or make_config(), init_db(Path(":memory:")), SettingsStore(data_dir / "settings.json"))


# --- bed limits of the Custom profile -----------------------------------------------------


async def test_bed_limits_are_saved_and_kept_across_restarts(client, data_dir):
    body = await save_four(client, base="printess-1", bed=BED)

    assert body["bed"] == BED
    assert app.state.session.profile.bed.model_dump() == BED
    stored = json.loads((data_dir / "settings.json").read_text())
    assert stored["printer"]["custom"]["bed"] == BED

    restart(data_dir)
    assert (await client.get("/printer")).json()["bed"] == BED


async def test_saving_without_bed_keeps_the_custom_bed(client):
    await save_four(client, base="printess-1", bed=BED)
    body = await save_four(client)
    assert body["bed"] == BED


async def test_built_in_profiles_keep_their_bed_from_config_json(client):
    await save_four(client, base="printess-1", bed=BED)
    profiles = {p["id"]: p for p in (await client.get("/printer/profiles")).json()["profiles"]}
    assert profiles["printess-1"]["bed"]["x"] == {"min": -30, "max": 30}


@pytest.mark.parametrize(
    "bed, messages",
    [
        ({**BED, "x": {"min": 45, "max": -45}}, ["Bed X: min (45.0) must be below max (-45.0)"]),
        ({**BED, "z": {"min": 3, "max": 3}}, ["Bed Z: min (3.0) must be below max (3.0)"]),
        (
            {**BED, "y": {"min": 1, "max": 0}, "z": {"min": 5, "max": 0}},
            ["Bed Y: min (1.0) must be below max (0.0)", "Bed Z: min (5.0) must be below max (0.0)"],
        ),
    ],
)
async def test_invalid_bed_limits_are_refused(client, bed, messages):
    resp = await client.put("/printer/custom", json={"nozzles": NOZZLES, "bed": bed})

    assert resp.status_code == 400
    assert resp.json()["detail"].split("\n") == messages
    assert (await client.get("/printer/custom")).json() is None


async def test_bed_and_syringe_problems_are_reported_together(client):
    nozzles = [{**NOZZLES[0], "plunger_axis": "X"}]
    resp = await client.put(
        "/printer/custom", json={"nozzles": nozzles, "bed": {**BED, "x": {"min": 1, "max": 0}}}
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Syringe 1: nozzle 's1': plunger_axis 'X'" in detail
    assert "Bed X: min (1.0) must be below max (0.0)" in detail


async def test_bed_change_refused_while_printing(client, four_printer):  # noqa: F811
    await zero(client, ["s1"])
    await upload_lab_file(client, "G91\nG1 Y10 B-0.5 F200\n")
    four_printer.hold_at("G1 Y10 B-0.5 F200", 0.5)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(four_printer.held.is_set)

    resp = await client.put("/printer/custom", json={"nozzles": NOZZLES, "bed": BED})

    assert resp.status_code == 409
    assert app.state.session.profile.bed.x.max == 30
    four_printer.release()
    assert (await client.post("/print/stop")).status_code == 200


async def test_saving_bed_resets_the_calibration_and_unloads(client, four_printer):  # noqa: F811
    await zero(client, ["s1"])
    await upload_lab_file(client, "G91\nG1 X1 F300\n")

    await save_four(client, bed=BED)

    assert app.state.session.zeroed == set()
    assert app.state.session.loaded is None


async def test_jog_uses_the_custom_bed(client, four_printer):  # noqa: F811
    await save_four(client, bed={**BED, "x": {"min": -2, "max": 2}})
    await zero(client, ["s1"])

    assert (await client.post("/jog", json={"axis": "Z", "distance": 2})).status_code == 200
    resp = await client.post("/jog", json={"axis": "Z", "distance": 2})
    assert resp.status_code == 400
    assert "Z to 4 mm, outside the bed (0 to 3 mm)" in resp.json()["detail"]
    # Every syringe's height has the z range
    resp = await client.post("/jog", json={"axes": ["U"], "distance": 1})
    assert resp.status_code == 200  # unzeroed: free
    resp = await client.post("/jog", json={"axis": "X", "distance": 3})
    assert resp.status_code == 400
    assert "X to 3 mm, outside the bed (-2 to 2 mm)" in resp.json()["detail"]


async def test_go_to_origin_uses_the_custom_bed(client, four_printer):  # noqa: F811
    await save_four(client, bed={**BED, "x": {"min": 5, "max": 10}})
    await zero(client, ["s1"])

    resp = await client.post("/jog/origin", json={})

    assert resp.status_code == 400
    assert "X to 0 mm, outside the bed (5 to 10 mm)" in resp.json()["detail"]


async def upload_lab_file(client, text: str) -> None:
    resp = await client.post(
        "/upload/gcode",
        params={"nozzles": ["s1"]},
        files={"file": ("lab.gcode", text.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text


async def test_print_start_uses_the_custom_bed(client, four_printer):  # noqa: F811
    await save_four(client, bed={**BED, "x": {"min": -10, "max": 6}})
    await zero(client, ["s1"])
    # A lab file starts wherever the head is: checked at print start
    await upload_lab_file(client, "G91\nG1 X5 F300\n")
    assert (await client.post("/jog", json={"axis": "X", "distance": 3})).status_code == 200

    resp = await client.post("/print/start")

    assert resp.status_code == 400
    assert "X to 8 mm, outside the bed (-10 to 6 mm)" in resp.json()["detail"]
    assert app.state.queue_worker.status != PrintStatus.PRINTING
    # Back at X0 it fits
    assert (await client.post("/jog", json={"axis": "X", "distance": -3})).status_code == 200
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(lambda: app.state.queue_worker.status == PrintStatus.COMPLETED)


# --- the heater protocol ----------------------------------------------------------------


async def put_settings(client, **changes):
    return await client.put("/settings", json=changes)


async def sensors_on(client) -> None:
    assert (await put_settings(client, temperature_sensors=True)).status_code == 200


def report(**actuals: float) -> None:
    """A temperature report, as the serial manager publishes it."""
    app.state.temperature.record({key: {"actual": value, "target": None} for key, value in actuals.items()})


async def test_protocol_defaults_to_config_json(client, data_dir):
    assert (await client.get("/settings")).json()["temperature_protocol"] is None
    assert (await client.get("/temperature")).json()["protocol"] == "marlin"

    config = make_config()
    config.temperature.protocol = "printess"
    restart(data_dir, config)

    assert (await client.get("/settings")).json()["temperature_protocol"] is None
    assert (await client.get("/temperature")).json()["protocol"] == "printess"


async def test_protocol_is_saved_and_kept_across_restarts(client, data_dir):
    resp = await put_settings(client, temperature_protocol="printess")

    assert resp.status_code == 200
    assert resp.json() == {"temperature_sensors": False, "temperature_protocol": "printess"}
    stored = json.loads((data_dir / "settings.json").read_text())
    assert stored["temperature_protocol"] == "printess"

    restart(data_dir)  # config.json says marlin; the choice wins
    assert (await client.get("/temperature")).json()["protocol"] == "printess"


async def test_settings_not_given_are_kept(client):
    await put_settings(client, temperature_protocol="printess")
    await sensors_on(client)
    assert (await client.get("/settings")).json() == {
        "temperature_sensors": True,
        "temperature_protocol": "printess",
    }
    await put_settings(client, temperature_sensors=False)
    assert app.state.temperature.config.protocol == "printess"


async def test_protocol_applies_at_once(client):
    await sensors_on(client)
    connect_fake_serial()
    send = app.state.serial_manager.send
    report(T0=21.0)
    assert (await client.post("/temperature/target", json={"sensor": "T0", "target": 37})).is_success
    send.assert_awaited_with("M104 T0 S37")

    sub_id, events = app.state.event_bus.subscribe()
    assert (await put_settings(client, temperature_protocol="printess")).status_code == 200
    published = []
    while not events.empty():
        published.append(events.get_nowait())
    app.state.event_bus.unsubscribe(sub_id)
    assert {"type": "settings", "temperature_sensors": True, "temperature_protocol": "printess"} in published
    assert [e["protocol"] for e in published if e["type"] == "temperature_status"] == ["printess"]

    # No reconnect: the next report is read as channels, and targets go out as M104 I<n>
    report(T0=21.0)
    resp = await client.post("/temperature/target", json={"sensor": "I0", "target": 37})
    assert resp.status_code == 200, resp.text
    send.assert_awaited_with("M104 I0 T37 H")
    resp = await client.post("/temperature/target", json={"sensor": "T0", "target": 37})
    assert resp.status_code == 400

    assert (await put_settings(client, temperature_protocol="marlin")).status_code == 200
    report(T0=21.0)
    assert (await client.post("/temperature/target", json={"sensor": "T0", "target": 30})).is_success
    send.assert_awaited_with("M104 T0 S30")


async def test_switching_forgets_the_old_protocols_readings_and_targets(client):
    await sensors_on(client)
    connect_fake_serial()
    await put_settings(client, temperature_protocol="printess")
    report(T0=21.0, T1=22.0)
    assert (await client.post("/temperature/target", json={"sensor": "I0", "target": 37})).is_success
    store = app.state.temperature
    assert store.latest["I0"].target == 37

    await put_settings(client, temperature_protocol="marlin")

    assert store.latest == {}
    assert store.history(3600) == {}
    assert (await client.get("/temperature")).json()["sensors"] == []
    # Back on printess, the target sent before isn't remembered either
    await put_settings(client, temperature_protocol="printess")
    report(T0=21.0)
    assert store.latest["I0"].target is None


async def test_choosing_the_protocol_in_use_changes_nothing(client):
    await sensors_on(client)
    report(T0=21.0)
    assert (await put_settings(client, temperature_protocol="marlin")).status_code == 200
    assert "T0" in app.state.temperature.latest


async def test_protocol_change_refused_while_waiting_for_temperatures(client, data_dir):
    worker = app.state.queue_worker
    worker._waiting_for_temperature = True
    try:
        resp = await put_settings(client, temperature_protocol="printess")
        assert resp.status_code == 409
        assert "waiting for its temperatures" in resp.json()["detail"]
        assert app.state.temperature.config.protocol == "marlin"
        assert not (data_dir / "settings.json").exists()
        # Other settings, and the protocol already in use, still go through
        assert (
            await put_settings(client, temperature_protocol="marlin", temperature_sensors=True)
        ).is_success
    finally:
        worker._waiting_for_temperature = False


async def test_virtual_printer_speaks_the_chosen_protocol(client, monkeypatch):
    monkeypatch.setenv("OCTARIS_VIRTUAL_PRINTER", "1")
    monkeypatch.setattr(virtual_printer, "DEV_OPTIONS", {"speed": 50, "report_scale": 0.05})
    await put_settings(client, temperature_protocol="printess")

    assert (await client.post("/connect", json={"port": "virtual"})).status_code == 200

    assert app.state.serial_manager._serial.protocol == "printess"
