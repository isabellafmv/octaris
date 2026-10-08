"""The /temperature endpoints, mostly against the virtual printer in dev mode."""

from __future__ import annotations

import asyncio
import csv
import io
import time

import pytest

from backend import virtual_printer
from backend.main import app
from backend.queue_worker import PrintStatus
from tests.test_print_gate import connect_fake_serial, set_status

# Heaters at 100 °C/s, a temperature report every 0.1 s
FAST = {"speed": 50, "report_scale": 0.05}


async def wait_for(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


@pytest.fixture
def dev_mode(monkeypatch):
    monkeypatch.setenv("OCTARIS_VIRTUAL_PRINTER", "1")
    monkeypatch.setattr(virtual_printer, "DEV_OPTIONS", dict(FAST))


async def connect_virtual(client, **options) -> virtual_printer.VirtualPrinter:
    """Connect to the virtual printer; returns it once it reported temperatures."""
    virtual_printer.DEV_OPTIONS.update(options)
    assert (await client.post("/connect", json={"port": "virtual"})).status_code == 200
    printer = app.state.serial_manager._serial
    if printer.temperatures:
        await wait_for(lambda: app.state.temperature.status().state == "ok")
    return printer


async def get_status(client) -> dict:
    resp = await client.get("/temperature")
    assert resp.status_code == 200
    return resp.json()


# --- GET /temperature -------------------------------------------------------------------


async def test_disconnected(client):
    assert await get_status(client) == {"state": "disconnected", "sensors": []}


async def test_sensors_from_the_virtual_printer(client, dev_mode):
    await connect_virtual(client)

    status = await get_status(client)
    assert status["state"] == "ok"
    sensors = {s["sensor"]: s for s in status["sensors"]}
    # config.json names them; Marlin's extra "T:" for the active tool is dropped
    assert list(sensors) == ["T0", "T1", "B", "C"]
    assert sensors["T0"]["name"] == "Left syringe"
    assert (sensors["T0"]["min"], sensors["T0"]["max"]) == (4, 60)
    assert sensors["T0"]["status"] == "off"
    assert sensors["T0"]["settable"] is True


async def test_no_sensor_reported(client, dev_mode):
    app.state.temperature.config.report_timeout_s = 0.3
    await connect_virtual(client, sensors={})

    assert (await get_status(client))["state"] == "waiting"
    await asyncio.sleep(0.35)
    assert await get_status(client) == {"state": "no_sensors", "sensors": []}


# --- POST /temperature/target -------------------------------------------------------------


async def test_target_heats_the_virtual_printer(client, dev_mode):
    printer = await connect_virtual(client)

    resp = await client.post("/temperature/target", json={"sensor": "T1", "target": 37})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "sensor": "T1", "target": 37, "command": "M104 T1 S37"}
    for sensor, target in (("B", 30), ("C", 25), ("T0", 10)):
        resp = await client.post("/temperature/target", json={"sensor": sensor, "target": target})
        assert resp.is_success
    assert [c for c in printer.executed if c.startswith(("M104", "M140", "M141"))] == [
        "M104 T1 S37",
        "M140 S30",
        "M141 S25",
        "M104 T0 S10",
    ]

    def reached() -> bool:
        latest = app.state.temperature.latest
        return all(
            abs(latest[key].actual - target) < 0.5 and latest[key].target == target
            for key, target in (("T1", 37), ("B", 30), ("C", 25), ("T0", 10))
        )

    await wait_for(reached)
    statuses = {s["sensor"]: s["status"] for s in (await get_status(client))["sensors"]}
    assert statuses == {"T0": "at_target", "T1": "at_target", "B": "at_target", "C": "at_target"}

    assert (await client.post("/temperature/target", json={"sensor": "T1", "target": 0})).is_success
    await wait_for(lambda: abs(app.state.temperature.latest["T1"].actual - 21.1) < 0.5)
    assert app.state.temperature.latest["T1"].target == 0


@pytest.mark.parametrize(
    ("sensor", "target", "detail"),
    [
        ("T0", 61, "Left syringe accepts targets from 4 to 60 °C (or 0 for off)"),
        ("T0", 2, "Left syringe accepts targets from 4 to 60 °C (or 0 for off)"),
        ("C", 50, "Chamber accepts targets from 4 to 45 °C (or 0 for off)"),
        ("T7", 121, "T7 accepts targets from 0 to 120 °C (or 0 for off)"),
        ("P", 30, "Sensor P has no heater"),
    ],
)
async def test_target_out_of_range_is_rejected(client, sensor, target, detail):
    connect_fake_serial()
    resp = await client.post("/temperature/target", json={"sensor": sensor, "target": target})
    assert resp.status_code == 400
    assert resp.json()["detail"] == detail
    app.state.serial_manager.send.assert_not_called()


async def test_target_needs_a_connection(client):
    resp = await client.post("/temperature/target", json={"sensor": "T0", "target": 37})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Printer not connected"


@pytest.mark.parametrize("status", [PrintStatus.PRINTING, PrintStatus.PAUSED])
async def test_target_is_allowed_while_printing(client, status):
    connect_fake_serial()
    set_status(status)

    # Unlike a raw line, which has to wait for a pause
    if status == PrintStatus.PRINTING:
        assert (await client.post("/gcode/send", json={"line": "M104 T0 S37"})).status_code == 409
    resp = await client.post("/temperature/target", json={"sensor": "T0", "target": 37})

    assert resp.status_code == 200
    app.state.serial_manager.send.assert_awaited_once_with("M104 T0 S37")


async def test_target_goes_out_between_print_lines(client, dev_mode, tmp_path):
    """During a real print, the target is sent between two numbered lines,
    and the print carries on."""
    from tests.test_history import upload_stl

    printer = await connect_virtual(client, speed=5)
    app.state.session.calibrated = True
    app.state.config.nozzle_offset_measured = True
    worker = app.state.queue_worker
    await upload_stl(client, tmp_path, 200)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(lambda: worker.lines_sent >= 5)

    resp = await client.post("/temperature/target", json={"sensor": "B", "target": 30})
    assert resp.status_code == 200
    sent_at = worker.lines_sent
    await wait_for(lambda: worker.lines_sent >= sent_at + 5)
    assert worker.status == PrintStatus.PRINTING
    assert "M140 S30" in printer.executed
    assert not any(c.startswith(("M109", "M190")) for c in printer.executed)
    await client.post("/print/stop")


# --- history and export --------------------------------------------------------------------


def seed(readings: list[tuple[float, str, float, float | None, int | None]]) -> None:
    from backend.database import insert_temperature_readings

    insert_temperature_readings(app.state.db, readings)


async def test_history_of_the_last_minutes(client):
    store = app.state.temperature
    store.record({"T0": {"actual": 20.0, "target": 0.0}, "P": {"actual": 22.0, "target": None}})
    store.record({"T0": {"actual": 21.0, "target": 37.0}, "P": {"actual": 22.5, "target": None}})

    resp = await client.get("/temperature/history", params={"minutes": 30})
    assert resp.status_code == 200
    [t0, p] = resp.json()["series"]
    assert (t0["sensor"], t0["name"], t0["actual"], t0["target"]) == ("T0", "Left syringe", [20, 21], [0, 37])
    assert len(t0["timestamps"]) == 2
    assert (p["sensor"], p["settable"], p["target"]) == ("P", False, [None, None])


async def test_history_of_a_print_session(client):
    seed([(1000.0, "T0", 20.0, 37.0, 1), (1002.0, "T0", 21.0, 37.0, 1), (1004.0, "T0", 22.0, 37.0, 2)])

    resp = await client.get("/temperature/history", params={"session_id": 1})
    [t0] = resp.json()["series"]
    assert (t0["timestamps"], t0["actual"]) == ([1000, 1002], [20, 21])
    assert (await client.get("/temperature/history", params={"session_id": 9})).json() == {"series": []}


@pytest.mark.parametrize(
    ("params", "status"),
    [({}, 400), ({"minutes": 10, "session_id": 1}, 400), ({"minutes": 121}, 422), ({"minutes": 0}, 422)],
)
async def test_history_needs_one_range(client, params, status):
    assert (await client.get("/temperature/history", params=params)).status_code == status


async def test_csv_export_of_a_print(client):
    seed([(1000.0, "T0", 20.5, 37.0, 1), (1000.0, "P", 22.0, None, 1), (1002.0, "T0", 21.0, 37.0, 2)])

    resp = await client.get("/temperature/export.csv", params={"session_id": 1})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == 'attachment; filename="temperature-print-1.csv"'
    assert list(csv.reader(io.StringIO(resp.text))) == [
        ["timestamp", "sensor", "name", "actual", "target"],
        ["1970-01-01T00:16:40.000+00:00", "T0", "Left syringe", "20.5", "37"],
        ["1970-01-01T00:16:40.000+00:00", "P", "P", "22", ""],
    ]


async def test_csv_export_of_a_time_range(client):
    now = time.time()
    seed([(now - 600, "B", 20.0, 0.0, None), (now - 60, "B", 21.0, 30.0, None)])
    # Readings not yet flushed are included too
    app.state.temperature.record({"B": {"actual": 22.0, "target": 30.0}})

    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 120))
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 60))
    resp = await client.get("/temperature/export.csv", params={"from": start, "to": end})
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert [(r["name"], r["actual"]) for r in rows] == [("Print bed", "21"), ("Print bed", "22")]
    assert resp.headers["content-disposition"].startswith('attachment; filename="temperature-')


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"from": "2026-01-01T00:00:00Z"},
        {"session_id": 1, "from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"},
    ],
)
async def test_csv_export_needs_one_range(client, params):
    assert (await client.get("/temperature/export.csv", params=params)).status_code == 400
