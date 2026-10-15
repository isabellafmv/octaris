"""The temperature_sensors setting, against the virtual printer in dev mode:
off, nothing temperature-related goes out by itself; on, the monitor
auto-reports or polls without ever holding up other commands."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from backend import serial_manager as serial_module
from backend import virtual_printer
from backend.config import ChannelConfig, load_config
from backend.database import init_db
from backend.main import app, wire
from backend.schemas import Settings
from backend.settings import SettingsStore
from tests.test_temperature import MARLIN

# Heaters at 100 °C/s, a temperature report every 0.1 s
FAST = {"speed": 50, "report_scale": 0.05}
# A printer that doesn't auto-report, so the monitor polls with M105
POLLED = {**FAST, "autoreport": False}


async def wait_for(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


@pytest.fixture
def dev_mode(monkeypatch):
    monkeypatch.setenv("OCTARIS_VIRTUAL_PRINTER", "1")
    monkeypatch.setattr(virtual_printer, "DEV_OPTIONS", dict(FAST))


@pytest.fixture
def quick_monitor(monkeypatch):
    """Give up on auto-reports after 0.1 s, and poll every 0.05 s."""
    monkeypatch.setattr(serial_module, "AUTOREPORT_WAIT_S", 0.1)
    monkeypatch.setattr(serial_module, "TEMPERATURE_POLL_S", 0.05)


async def connect_virtual(client, **options) -> virtual_printer.VirtualPrinter:
    virtual_printer.DEV_OPTIONS.update(options)
    assert (await client.post("/connect", json={"port": "virtual"})).status_code == 200
    return app.state.serial_manager._serial


async def set_sensors(client, on: bool) -> None:
    resp = await client.put("/settings", json={"temperature_sensors": on})
    assert resp.status_code == 200
    assert resp.json() == {"temperature_sensors": on, "temperature_protocol": None}


def temperature_commands(printer: virtual_printer.VirtualPrinter) -> list[str]:
    return [line for line in printer.received if line.startswith(("M155", "M105"))]


def monitor() -> str:
    return app.state.serial_manager.temperature_monitor


# --- the setting ----------------------------------------------------------------------


async def test_off_by_default_and_kept_across_restarts(client, data_dir):
    assert (await client.get("/settings")).json() == {
        "temperature_sensors": False,
        "temperature_protocol": None,
    }
    assert (await client.get("/status")).json()["settings"] == {
        "temperature_sensors": False,
        "temperature_protocol": None,
    }

    await set_sensors(client, True)

    assert (data_dir / "settings.json").exists()
    assert SettingsStore().current == Settings(temperature_sensors=True)
    # A restart: the app is wired again from what was saved
    await app.state.temperature.close()
    app.state.history._traffic.stop()
    app.state.db.close()
    wire(app, load_config(), init_db(Path(":memory:")))
    assert (await client.get("/settings")).json() == {
        "temperature_sensors": True,
        "temperature_protocol": None,
    }
    assert monitor() == "idle"


async def test_unreadable_settings_file_falls_back_to_the_defaults(data_dir):
    data_dir.mkdir(parents=True)
    (data_dir / "settings.json").write_text("{not json")
    assert SettingsStore().current == Settings()


async def test_change_is_published(client):
    sub_id, events = app.state.event_bus.subscribe()
    await set_sensors(client, True)
    published = []
    while not events.empty():
        published.append(events.get_nowait())
    app.state.event_bus.unsubscribe(sub_id)
    assert {"type": "settings", "temperature_sensors": True, "temperature_protocol": None} in published
    status = [e for e in published if e["type"] == "temperature_status"][-1]
    assert (status["state"], status["monitor"]) == ("disconnected", "idle")


# --- off ------------------------------------------------------------------------------


async def test_off_sends_no_m155_or_m105_after_connecting(client, dev_mode, quick_monitor):
    printer = await connect_virtual(client, **POLLED)

    # Long enough for the auto-report wait and several polls, had they run
    await asyncio.sleep(0.5)

    assert temperature_commands(printer) == []
    assert monitor() == "off"
    status = (await client.get("/temperature")).json()
    assert status == {"state": "off", "monitor": "off", "sensors": [], **MARLIN}


async def test_off_ignores_manual_reports_and_warns_about_nothing(client, dev_mode):
    printer = await connect_virtual(client)
    sub_id, events = app.state.event_bus.subscribe()

    # Take Over's M105 quick command still works...
    resp = await client.post("/gcode/send", json={"line": "M105"})
    assert resp.status_code == 200
    assert resp.json()["response"].startswith("ok T:")
    # ...but nothing is shown, logged or warned about
    app.state.temperature.config.report_timeout_s = 0.0
    app.state.temperature.check()
    assert app.state.temperature.latest == {}
    assert (await client.get("/temperature")).json()["state"] == "off"
    published = []
    while not events.empty():
        published.append(events.get_nowait())
    app.state.event_bus.unsubscribe(sub_id)
    assert not any(e["type"] == "warning" for e in published)
    assert temperature_commands(printer) == ["M105"]


async def test_off_refuses_to_wait_for_temperatures(client):
    from tests.test_print_gate import connect_fake_serial

    connect_fake_serial()
    app.state.session.loaded = object()  # anything loaded

    resp = await client.post("/print/start", json={"wait_for_temperature": True})

    assert resp.status_code == 400
    assert "temperature control is off" in resp.json()["detail"]


# --- on -------------------------------------------------------------------------------


async def test_on_auto_reports(client, dev_mode):
    await set_sensors(client, True)
    printer = await connect_virtual(client)

    await wait_for(lambda: monitor() == "autoreport")
    await wait_for(lambda: app.state.temperature.state() == "ok")
    assert temperature_commands(printer) == ["M155 S2"]


async def test_on_falls_back_to_polling(client, dev_mode, quick_monitor):
    await set_sensors(client, True)
    printer = await connect_virtual(client, **POLLED)

    await wait_for(lambda: printer.executed.count("M105") >= 2)
    assert monitor() == "polling"
    assert app.state.temperature.state() == "ok"


async def test_jog_goes_through_while_m105_goes_unanswered(client, dev_mode, quick_monitor):
    """The poll's deadline is 5 s, but a jog doesn't wait for it."""
    await set_sensors(client, True)
    printer = await connect_virtual(client, **POLLED, answers_m105=False)
    manager = app.state.serial_manager

    for distance in (1, -1, 1):
        # A poll is in flight, and won't be answered
        await wait_for(lambda: manager._pending is not None and manager._pending.poll)
        jogs = len([c for c in printer.executed if c.startswith("G1 X")])
        started = time.monotonic()
        resp = await client.post("/jog", json={"axis": "X", "distance": distance})
        elapsed = time.monotonic() - started

        assert resp.status_code == 200, resp.text
        assert elapsed < 1.0
        assert len([c for c in printer.executed if c.startswith("G1 X")]) == jogs + 1
    # Given up on, not failed: polling carries on
    assert monitor() == "polling"


async def test_polling_pauses_after_two_unanswered_m105s(client, dev_mode, quick_monitor, monkeypatch):
    monkeypatch.setattr(serial_module, "POLL_DEADLINE_S", 0.2)
    await set_sensors(client, True)
    printer = await connect_virtual(client, **POLLED, answers_m105=False)

    await wait_for(lambda: monitor() == "paused")

    assert printer.executed.count("M105") == 2
    status = (await client.get("/temperature")).json()
    assert (status["state"], status["monitor"]) == ("no_sensors", "paused")
    # Nothing else is held up, and no more polls go out
    resp = await client.post("/gcode/send", json={"line": "M114"})
    assert resp.status_code == 200
    await asyncio.sleep(0.3)
    assert printer.executed.count("M105") == 2

    # Retry: polling starts again (and pauses again, the printer being as it was)
    printer.answers_m105 = True
    resp = await client.post("/temperature/retry")
    assert resp.status_code == 200
    await wait_for(lambda: monitor() == "polling")
    await wait_for(lambda: app.state.temperature.state() == "ok")


async def test_polling_pauses_when_m105_reports_no_sensor(client, dev_mode, quick_monitor):
    await set_sensors(client, True)
    printer = await connect_virtual(client, **POLLED, sensors={})

    await wait_for(lambda: monitor() == "paused")
    assert printer.executed.count("M105") == 2


async def test_no_poll_while_a_command_is_queued(client, dev_mode, quick_monitor):
    await set_sensors(client, True)
    await connect_virtual(client, **POLLED)
    await wait_for(lambda: monitor() == "polling")

    manager = app.state.serial_manager
    # G4 holds the line for 0.5 s (speed 50: P25000 is 0.5 s), with polls due every 0.05 s
    dwell = asyncio.create_task(client.post("/gcode/send", json={"line": "G4 P25000"}))
    await wait_for(lambda: manager._pending is not None and manager._pending.command == "G4 P25000")
    await asyncio.sleep(0.3)

    # No poll is queued up behind it, to go out before the next command
    assert not manager._send_lock._waiters
    assert (await dwell).status_code == 200


async def test_retry_needs_the_setting(client):
    resp = await client.post("/temperature/retry")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Temperature control is off — enable it in Setup"


# --- switching while connected ----------------------------------------------------------


async def test_switching_on_while_connected_starts_at_once(client, dev_mode):
    printer = await connect_virtual(client)
    assert temperature_commands(printer) == []

    await set_sensors(client, True)

    await wait_for(lambda: monitor() == "autoreport")
    await wait_for(lambda: app.state.temperature.state() == "ok")
    assert temperature_commands(printer) == ["M155 S2"]


async def test_switching_off_while_auto_reporting_stops_the_reports(client, dev_mode):
    await set_sensors(client, True)
    printer = await connect_virtual(client)
    await wait_for(lambda: monitor() == "autoreport")

    await set_sensors(client, False)

    assert monitor() == "off"
    assert (await client.get("/temperature")).json()["state"] == "off"
    await wait_for(lambda: "M155 S0" in printer.executed)
    assert app.state.temperature.latest == {}


async def test_switching_off_while_polling_stops_at_once(client, dev_mode, quick_monitor):
    await set_sensors(client, True)
    printer = await connect_virtual(client, **POLLED)
    await wait_for(lambda: printer.executed.count("M105") >= 2)

    await set_sensors(client, False)
    # At most the poll already in flight still finishes
    await asyncio.sleep(0.05)
    polls = printer.executed.count("M105")
    await asyncio.sleep(0.3)

    assert printer.executed.count("M105") == polls
    assert monitor() == "off"
    assert "M155 S0" not in printer.executed  # nothing to stop

    # And back on: polling resumes without a reconnect
    await set_sensors(client, True)
    await wait_for(lambda: printer.executed.count("M105") > polls)
    assert monitor() == "polling"


# --- test sensors ------------------------------------------------------------------------


async def test_test_sensors_shows_the_reply(client, dev_mode):
    printer = await connect_virtual(client)  # works with the setting off

    resp = await client.post("/temperature/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "ok " + printer.temperature_report()
    assert [(s["sensor"], s["name"]) for s in body["sensors"]] == [
        ("T0", "Left syringe"),
        ("T1", "Right syringe"),
        ("B", "Print bed"),
        ("C", "Chamber"),
    ]
    assert body["sensors"][0]["actual"] == pytest.approx(21.3, abs=0.5)


async def test_test_sensors_shows_the_channels(client, dev_mode):
    config = app.state.temperature.config
    config.protocol = "printess"
    config.channels = [
        ChannelConfig(index=n, name=f"Heater {n}", reply_key=key)
        for n, key in enumerate(["T1", "T0", "T3", "T2"])
    ]
    await connect_virtual(client, protocol="printess")

    body = (await client.post("/temperature/test")).json()

    assert body["reply"] == "ok T0:21.30 T1:21.10 T2:20.80 T3:21.60"
    assert [(s["sensor"], s["name"], s["actual"], s["status"]) for s in body["sensors"]] == [
        ("I0", "Heater 0", 21.1, "off"),
        ("I1", "Heater 1", 21.3, "off"),
        ("I2", "Heater 2", 21.6, "off"),
        ("I3", "Heater 3", 20.8, "off"),
    ]


async def test_test_sensors_without_a_reply(client, dev_mode, monkeypatch):
    monkeypatch.setattr(serial_module, "POLL_DEADLINE_S", 0.2)
    await connect_virtual(client, answers_m105=False)

    resp = await client.post("/temperature/test")

    assert resp.status_code == 200
    assert resp.json() == {"reply": None, "sensors": []}


async def test_test_sensors_needs_a_connection(client):
    resp = await client.post("/temperature/test")
    assert resp.status_code == 400
