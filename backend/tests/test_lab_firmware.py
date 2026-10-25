"""The lab firmware's heater channels (temperature protocol "printess"): its
temperature block, read off the serial line; targets and off; what each
channel heats (the temperature_channels setting); and all of it against the
virtual printer speaking that firmware."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import queue
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from backend import serial_manager as serial_module
from backend import virtual_printer
from backend.config import TemperatureConfig
from backend.database import init_db
from backend.main import app, wire
from backend.queue_worker import PrintStatus
from backend.serial_manager import (
    PrintessBlock,
    SerialManager,
    block_readings,
    parse_block,
    parse_block_line,
    reply_temperatures,
)
from backend.settings import SettingsStore
from backend.temperature import TargetError, TemperatureStore, channel_command
from backend.virtual_printer import PRINTESS_SENSORS, VirtualPrinter
from tests.conftest import make_config
from tests.serial_fakes import attach
from tests.test_custom_profile import save_four
from tests.test_temperature import Clock
from tests.test_temperature_api import dev_mode, ready_to_print  # noqa: F401 - fixtures

# The blocks seen on the lab's terminal
COOLING = [
    "Status:       [Cooling, Disabled, Disabled, Disabled, Disabled]",
    "Temperatures: [14.94, X, X, X, X]",
    "Targets:      [15.00, X, X, X, X]",
    "PWM:          [127, 0, 0, 0, 0]",
]
DISABLED = [
    "Status:       [Disabled, Disabled, Disabled, Disabled, Disabled]",
    "Temperatures: [X, X, X, X, X]",
    "Targets:      [X, X, X, X, X]",
    "PWM:          [0, 0, 0, 0, 0]",
]
OFF = {"actual": None, "target": None, "status": "Disabled", "pwm": 0.0}
# The firmware's unasked lines right after connecting
ON_CONNECT = [*DISABLED, "Printer is now online", *DISABLED]

# Heaters at 100 °C/s, a temperature report every 0.1 s
FAST = {"speed": 50, "report_scale": 0.05}


async def wait_for(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


# --- the block ------------------------------------------------------------------------------


def test_cooling_block():
    assert parse_block("\n".join(COOLING)) == {
        "I0": {"actual": 14.94, "target": 15.0, "status": "Cooling", "pwm": 127.0},
        # Five entries, of which I0..I3 are used
        "I1": OFF,
        "I2": OFF,
        "I3": OFF,
    }


def test_all_disabled_block():
    assert parse_block("\n".join(DISABLED)) == {f"I{n}": OFF for n in range(4)}


@pytest.mark.parametrize(
    ("line", "parsed"),
    [
        ("Temperatures: [14.94, X, X, X, X]", ("Temperatures", ["14.94", "X", "X", "X", "X"])),
        ("Status:       [Cooling, Disabled]", ("Status", ["Cooling", "Disabled"])),
        ("pwm: [127,0]", ("PWM", ["127", "0"])),
        ("Targets: []", ("Targets", [])),
        ("T:21.30 /0.00 B:20.10 /0.00 @:0 B@:0", None),
        ("X:0.00 Y:0.00 Z:0.00 Count X:0 Y:0 Z:0", None),
        ("Printer is now online", None),
        ("echo:Unknown command: M155", None),
        ("ok", None),
    ],
)
def test_block_lines(line, parsed):
    assert parse_block_line(line) == parsed


def test_a_block_ends_at_another_line_or_when_a_list_repeats():
    block = PrintessBlock()
    assert block.feed(*parse_block_line(COOLING[0])) is None
    assert block.feed(*parse_block_line(COOLING[1])) is None
    # The next block's Status: the first one is done, without targets or PWM
    first = block.feed(*parse_block_line(DISABLED[0]))
    assert first is not None and first["I0"] == {
        "actual": 14.94,
        "target": None,
        "status": "Cooling",
        "pwm": None,
    }
    assert block.end() is None  # no Temperatures yet
    assert block_readings({"Status": ["Heating"]}) is None


def test_reply_temperatures_by_protocol():
    reply = "\n".join([*COOLING, "ok"])
    assert reply_temperatures(reply, "printess")["I0"]["actual"] == 14.94
    assert reply_temperatures(reply, "marlin") == {}
    assert reply_temperatures("ok T:21.30 /0.00", "printess") == {}


# --- on the serial line -----------------------------------------------------------------------


class LabPort:
    """A fake port with the lab firmware's lines: `unasked` waiting to be
    read, and per command word the lines it answers with (default "ok")."""

    def __init__(self, unasked: list[str], replies: dict[str, list[str]] | None = None):
        self.is_open = True
        self.written: list[str] = []
        self.replies = replies or {}
        self._out: queue.SimpleQueue[str] = queue.SimpleQueue()
        for line in unasked:
            self._out.put(line)

    def write(self, data: bytes) -> int:
        for line in data.decode().splitlines():
            self.written.append(line)
            for reply in self.replies.get(line.split()[0], ["ok"]):
                self._out.put(reply)
        return len(data)

    def inject(self, lines: list[str]) -> None:
        for line in lines:
            self._out.put(line)

    def readline(self) -> bytes:
        try:
            return (self._out.get(timeout=0.01) + "\n").encode()
        except queue.Empty:
            return b""

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


@pytest.fixture
async def rig():
    """A SerialManager on the lab firmware's protocol, collecting its events."""
    events: list[dict] = []
    managers: list[SerialManager] = []

    def make(**options) -> SerialManager:
        manager = SerialManager(on_event=events.append, temperature_protocol="printess", **options)
        managers.append(manager)
        return manager

    yield make, events
    for manager in managers:
        await manager.disconnect()


def reports(events: list[dict]) -> list[dict]:
    return [e["temperatures"] for e in events if e["type"] == "temperature"]


async def test_blocks_unasked_on_connect(rig):
    make, events = rig
    manager = make()
    attach(manager, LabPort(ON_CONNECT))

    await wait_for(lambda: len(reports(events)) == 2)
    assert reports(events) == [{f"I{n}": OFF for n in range(4)}] * 2
    # Nothing is left half-read: the next command gets its own reply
    assert await manager.send("G1 X1") == "ok"
    assert await manager.send("G1 X2") == "ok"
    received = [e["content"] for e in manager.log_buffer if e["direction"] == "received"]
    assert received == ["Printer is now online", "ok", "ok"]


async def test_a_block_mid_command_leaves_its_reply_alone(rig):
    make, events = rig
    # The block arrives while G4 is in flight, before its ok
    port = LabPort([], {"G4": [*COOLING, "ok"], "M114": ["X:1.00 Y:2.00 Z:0.00 A:0.00 B:0.00 C:0.00", "ok"]})
    manager = make()
    attach(manager, port)

    assert await manager.send("G4 P10") == "ok"
    await wait_for(lambda: len(reports(events)) == 1)
    assert reports(events)[0]["I0"]["status"] == "Cooling"
    # Nor a reply with lines of its own
    port.replies["M114"] = ["X:1.00 Y:2.00 Z:0.00 A:0.00 B:0.00 C:0.00", *DISABLED, "ok"]
    assert await manager.send("M114") == "X:1.00 Y:2.00 Z:0.00 A:0.00 B:0.00 C:0.00\nok"
    assert manager.position["X"] == 1.0


async def test_a_block_is_no_error_and_no_reply_on_its_own(rig):
    make, events = rig
    port = LabPort([])
    manager = make()
    attach(manager, port)
    port.replies["G1"] = []  # no ok yet
    send = asyncio.create_task(manager.send("G1 X1"))
    await wait_for(lambda: manager._pending is not None)
    port.inject(COOLING)
    await asyncio.sleep(0.1)
    assert not send.done()
    port.inject(["ok"])
    assert await send == "ok"


async def test_m105_reply_carries_the_block(rig):
    make, events = rig
    manager = make()
    attach(manager, LabPort([], {"M105": [*COOLING, "ok"]}))

    reply = await manager.send("M105")

    assert reply == "\n".join([*COOLING, "ok"])
    assert reply_temperatures(reply, "printess")["I0"]["target"] == 15.0


async def test_marlin_protocol_reports_no_block(rig):
    make, events = rig
    manager = make()
    manager.set_temperature_protocol("marlin")
    attach(manager, LabPort(ON_CONNECT, {"G4": [*COOLING, "ok"]}))
    # Still kept out of other commands' replies
    assert await manager.send("G4 P10") == "ok"
    await asyncio.sleep(0.05)
    assert reports(events) == []


async def test_polls_and_resyncs_after_a_poll_given_up_on(rig, monkeypatch):
    """A slow M105 gives way to a jog; its block and "ok" come late, during
    the M114 that resyncs, and are taken for neither that nor the jog."""
    monkeypatch.setattr(serial_module, "TEMPERATURE_POLL_S", 0.05)
    make, events = rig
    printer = VirtualPrinter(protocol="printess", sensors=PRINTESS_SENSORS, speed=math.inf, m105_delay_s=0.8)
    manager = make(temperature_enabled=True)
    with patch("backend.serial_manager.serial.Serial", return_value=printer):
        await manager.connect("/dev/virtual", 115200)

    await wait_for(lambda: manager._pending is not None and manager._pending.poll)
    assert manager.temperature_monitor == "polling"
    assert "M155" not in " ".join(printer.received)  # the lab firmware is polled

    assert await manager.send("G1 X5 F6000") == "ok"
    assert (await manager.send("M114")).startswith("X:5.00")
    assert manager.position["X"] == 5.0
    assert "M114" in printer.executed  # the resync
    await wait_for(lambda: len(reports(events)) >= 2)
    assert manager.temperature_monitor == "polling"


# --- the store -----------------------------------------------------------------------------


@pytest.fixture
def db() -> sqlite3.Connection:
    return init_db(Path(":memory:"))


USES = {
    "I0": ("syringe:left", "Left syringe"),
    "I1": ("syringe:right", "Right syringe"),
    "I2": ("bed", "Bed"),
}


def lab_store(db, clock, events=None, printing=False, **config) -> TemperatureStore:
    store = TemperatureStore(
        db,
        TemperatureConfig(protocol="printess", **config),
        is_connected=lambda: True,
        is_printing=lambda: printing,
        publish=events.append if events is not None else None,
        clock=clock,
    )
    store.set_channel_uses(USES)
    return store


def block(**channels: tuple[str, float | None, float | None]) -> dict:
    """A parsed block: (status, actual, target) per channel, the others disabled."""
    readings = {f"I{n}": dict(OFF) for n in range(4)}
    for key, (status, actual, target) in channels.items():
        readings[key] = {"actual": actual, "target": target, "status": status, "pwm": 127.0}
    return readings


def test_status_comes_from_the_firmware(db):
    store = lab_store(db, Clock())
    store.record(parse_block("\n".join(COOLING)))
    status = store.status()

    assert [(s.sensor, s.name) for s in status.sensors] == [
        ("I0", "Left syringe (I0)"),
        ("I1", "Right syringe (I1)"),
        ("I2", "Bed (I2)"),
    ]
    i0, i1, _ = status.sensors
    assert (i0.actual, i0.target, i0.firmware_status, i0.mode) == (14.94, 15.0, "Cooling", "cool")
    assert i0.status == "at_target"  # within target_band_c
    assert (i1.actual, i1.target, i1.firmware_status, i1.mode, i1.status) == (
        None,
        None,
        "Disabled",
        None,
        "off",
    )

    store.record(block(I0=("Heating", 20.0, 37.0), I1=("Cooling", 30.0, 10.0)))
    i0, i1, _ = store.status().sensors
    assert (i0.status, i0.mode, i1.status, i1.mode) == ("heating", "heat", "cooling", "cool")
    # Above a target it heats to: still "heating", as the firmware says
    store.record(block(I0=("Heating", 40.0, 37.0)))
    assert store.status().sensors[0].status == "heating"


def test_unused_and_disabled_channels_are_not_logged(db):
    clock = Clock()
    store = lab_store(db, clock)
    store.record(block(I0=("Heating", 20.0, 37.0), I3=("Heating", 25.0, 30.0)))
    store.flush()

    # I3 isn't used: not shown, logged or charted
    assert "I3" not in {s.sensor for s in store.status().sensors}
    from backend.database import temperature_readings

    assert [(sensor, name) for _, sensor, _, _, _, name in temperature_readings(db)] == [
        ("I0", "Left syringe (I0)")
    ]
    assert list(store.history(60)) == ["I0"]
    assert [s.sensor for s in store.series(store.history(60))] == ["I0"]


def test_a_disabled_channel_is_not_waited_for(db):
    clock = Clock()
    store = lab_store(db, clock, settle_s=10)
    store.record(block(I0=("Cooling", 15.0, 15.0)))
    clock.advance(11)
    store.record(block(I0=("Cooling", 15.1, 15.0)))
    assert [s.sensor for s in store.wait_status()] == ["I0"]
    assert store.targets_reached()
    store.record(block())  # everything off
    assert store.wait_status() == []
    assert store.targets_reached()


def test_warnings_use_the_channel_names(db):
    clock = Clock()
    events: list[dict] = []
    store = lab_store(db, clock, events, printing=True, deviation_c=3, deviation_s=10)
    store.on_event({"type": "printer", "connected": True, "port": "virtual"})
    for _ in range(12):
        store.record(block(I1=("Cooling", 30.0, 10.0)))
        store.check()
        clock.advance(1)
    [warning] = [e["message"] for e in events if e["type"] == "warning"]
    assert warning.startswith("Right syringe (I1) is at 30.0 °C")


def test_csv_keeps_the_name_a_reading_was_taken_under(db):
    clock = Clock()
    store = lab_store(db, clock)
    store.record(block(I0=("Heating", 20.0, 37.0)))
    clock.advance(2)
    # Another print: I0 now heats the bed
    store.set_channel_uses({"I0": ("bed", "Bed")})
    store.record(block(I0=("Heating", 21.0, 37.0)))

    rows = list(csv.DictReader(io.StringIO(store.csv(store.logged(start=0, end=clock.now)))))
    assert [(r["sensor"], r["name"], r["actual"]) for r in rows] == [
        ("I0", "Left syringe (I0)", "20"),
        ("I0", "Bed (I0)", "21"),
    ]


@pytest.mark.parametrize(
    ("actual", "target", "command", "mode"),
    [
        (21.0, 37, "M104 I1 T37 H", "heat"),  # above the reading: heat
        (21.0, 10.5, "M104 I1 T10.5 C", "cool"),  # below: cool
        (37.0, 37, "M104 I1 T37 H", "heat"),  # equal: heat
    ],
)
def test_heat_or_cool_from_the_reading(actual, target, command, mode):
    assert channel_command(1, target, actual=actual) == (command, mode)


def test_explicit_direction_without_a_reading():
    assert channel_command(2, 30, direction="heat") == ("M104 I2 T30 H", "heat")
    assert channel_command(2, 10, direction="cool") == ("M104 I2 T10 C", "cool")
    # It wins over the reading
    assert channel_command(2, 30, actual=40.0, direction="heat") == ("M104 I2 T30 H", "heat")
    with pytest.raises(TargetError, match="choose Heat to or Cool to"):
        channel_command(2, 30)


def test_off_is_d():
    assert channel_command(3, 0) == ("M104 I3 D", None)
    assert TemperatureConfig().off_command == "M104 I{n} D"


def test_targets_from_the_store(db):
    store = lab_store(db, Clock())
    store.record(block(I0=("Cooling", 20.0, 15.0)))

    assert store.check_target("I0", 37) == "M104 I0 T37 H"
    assert store.check_target("I0", 15) == "M104 I0 T15 C"
    with pytest.raises(TargetError, match="I1 has no current reading"):
        store.check_target("I1", 30)  # disabled
    assert store.check_target("I1", 30, "heat") == "M104 I1 T30 H"
    assert store.check_target("I1", 0) == "M104 I1 D"
    # Not used: only off
    with pytest.raises(TargetError, match="I3 isn't used"):
        store.check_target("I3", 30, "heat")
    assert store.check_target("I3", 0) == "M104 I3 D"
    with pytest.raises(TargetError, match="isn't a heater channel"):
        store.check_target("T0", 30, "heat")


@pytest.mark.parametrize("target", [3.9, 50.1, -5, 120])
def test_targets_from_4_to_50(db, target):
    store = lab_store(db, Clock())
    with pytest.raises(TargetError, match=r"Left syringe \(I0\) accepts targets from 4 to 50"):
        store.check_target("I0", target, "heat")


@pytest.mark.parametrize("target", [4, 50])
def test_range_ends_are_allowed(db, target):
    assert lab_store(db, Clock()).check_target("I0", target, "cool") == f"M104 I0 T{target} C"


def test_default_channels():
    config = TemperatureConfig()
    assert [(c.sensor, c.min, c.max) for c in config.channels] == [(f"I{n}", 4, 50) for n in range(4)]
    assert [(c.sensor, c.min, c.max) for c in make_config().temperature.channels] == [
        (f"I{n}", 4, 50) for n in range(4)
    ]


@pytest.mark.parametrize(
    ("config", "error"),
    [
        ({"channels": [{"index": 0}, {"index": 0}]}, "same index"),
        ({"channels": [{"index": 4}]}, "less than 4"),
        ({"channels": [{"index": 0, "min": 50, "max": 4}]}, "must be below"),
        ({"off_command": "M104 I{channel} D"}, "can only use"),
        ({"protocol": "reprap"}, "protocol"),
    ],
)
def test_channel_config_is_checked(config, error):
    with pytest.raises(ValueError, match=error):
        TemperatureConfig(**config)


def test_old_channel_config_keys_are_ignored():
    config = TemperatureConfig(channels=[{"index": 0, "name": "Channel 0", "reply_key": "T0"}])
    assert config.channels[0].sensor == "I0"


# --- the app, on the virtual lab firmware --------------------------------------------------------


async def put_settings(client, **changes):
    return await client.put("/settings", json=changes)


@pytest.fixture
async def lab(client, monkeypatch):
    """The app on the lab firmware, with temperature control on, I0/I1 on
    the Printess 1.0's syringes and I2 on the bed; the virtual printer
    speaks that firmware (dev mode), polled every 0.05 s."""
    monkeypatch.setenv("OCTARIS_VIRTUAL_PRINTER", "1")
    monkeypatch.setattr(virtual_printer, "DEV_OPTIONS", dict(FAST))
    monkeypatch.setattr(serial_module, "TEMPERATURE_POLL_S", 0.05)
    resp = await put_settings(
        client,
        temperature_sensors=True,
        temperature_protocol="printess",
        temperature_channels={"I0": "syringe:left", "I1": "syringe:right", "I2": "bed"},
    )
    assert resp.status_code == 200, resp.text


async def connect(client) -> VirtualPrinter:
    assert (await client.post("/connect", json={"port": "virtual"})).status_code == 200
    await wait_for(lambda: app.state.temperature.status().state == "ok")
    return app.state.serial_manager._serial


async def get_status(client) -> dict:
    return (await client.get("/temperature")).json()


async def target(client, sensor: str, value: float, direction: str | None = None):
    body = {"sensor": sensor, "target": value}
    if direction is not None:
        body["direction"] = direction
    return await client.post("/temperature/target", json=body)


async def test_connect_shows_the_used_channels_disabled(client, lab):
    await connect(client)

    status = await get_status(client)
    assert status["protocol"] == "printess"
    assert [(c["sensor"], c["use"], c["name"]) for c in status["channels"]] == [
        ("I0", "syringe:left", "Left syringe (I0)"),
        ("I1", "syringe:right", "Right syringe (I1)"),
        ("I2", "bed", "Bed (I2)"),
        ("I3", None, "I3 (not used)"),
    ]
    assert [(s["sensor"], s["actual"], s["firmware_status"], s["status"]) for s in status["sensors"]] == [
        ("I0", None, "Disabled", "off"),
        ("I1", None, "Disabled", "off"),
        ("I2", None, "Disabled", "off"),
    ]


async def test_heat_cool_and_off(client, lab):
    printer = await connect(client)
    store = app.state.temperature

    # Disabled, so no reading: the direction has to be given
    resp = await target(client, "I0", 37)
    assert resp.status_code == 400
    assert "choose Heat to or Cool to" in resp.json()["detail"]
    resp = await target(client, "I0", 37, "heat")
    assert resp.json() == {"status": "ok", "sensor": "I0", "target": 37, "command": "M104 I0 T37 H"}
    assert (await target(client, "I2", 10, "cool")).json()["command"] == "M104 I2 T10 C"

    await wait_for(lambda: store.latest["I0"].firmware_status == "Heating")
    readings = {s["sensor"]: s for s in (await get_status(client))["sensors"]}
    assert (readings["I0"]["target"], readings["I0"]["mode"]) == (37, "heat")
    assert (readings["I2"]["firmware_status"], readings["I2"]["target"]) == ("Cooling", 10)

    def reached() -> bool:
        i0, i2 = store.latest["I0"], store.latest["I2"]
        return (
            i0.actual is not None
            and abs(i0.actual - 37) < 0.5
            and i2.actual is not None
            and abs(i2.actual - 10) < 0.5
        )

    await wait_for(reached)
    statuses = {s["sensor"]: s["status"] for s in (await get_status(client))["sensors"]}
    assert statuses == {"I0": "at_target", "I1": "off", "I2": "at_target"}

    # A reading now: H or C chosen from it
    assert (await target(client, "I0", 20)).json()["command"] == "M104 I0 T20 C"
    assert (await target(client, "I0", 45)).json()["command"] == "M104 I0 T45 H"

    # Off: D, and the channel is disabled again
    assert (await target(client, "I0", 0)).json()["command"] == "M104 I0 D"
    await wait_for(lambda: store.latest["I0"].disabled)
    assert store.latest["I0"].actual is None
    assert [c for c in printer.executed if c.startswith("M104")] == [
        "M104 I0 T37 H",
        "M104 I2 T10 C",
        "M104 I0 T20 C",
        "M104 I0 T45 H",
        "M104 I0 D",
    ]


@pytest.mark.parametrize(
    ("body", "command"),
    [
        # What Take Over's quick command sends: Heat, Cool and Off for a channel
        ({"sensor": "I1", "target": 30, "direction": "heat"}, "M104 I1 T30 H"),
        ({"sensor": "I1", "target": 12.5, "direction": "cool"}, "M104 I1 T12.5 C"),
        ({"sensor": "I1", "target": 0}, "M104 I1 D"),
    ],
)
async def test_quick_command_forms(client, lab, body, command):
    printer = await connect(client)
    resp = await client.post("/temperature/target", json=body)
    assert resp.json()["command"] == command
    assert printer.executed[-1] == command


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"sensor": "I0", "target": 51, "direction": "heat"}, "accepts targets from 4 to 50"),
        ({"sensor": "I0", "target": 3, "direction": "cool"}, "accepts targets from 4 to 50"),
        ({"sensor": "I3", "target": 30, "direction": "heat"}, "I3 isn't used"),
        ({"sensor": "I0", "target": 30, "direction": "up"}, None),
    ],
)
async def test_refused_targets(client, lab, body, detail):
    await connect(client)
    resp = await client.post("/temperature/target", json=body)
    assert resp.status_code == (422 if detail is None else 400)
    if detail is not None:
        assert detail in resp.json()["detail"]


async def test_direction_is_for_the_lab_firmware_only(client):
    assert (await put_settings(client, temperature_sensors=True)).is_success
    from tests.test_print_gate import connect_fake_serial

    connect_fake_serial()
    resp = await target(client, "T0", 30, "heat")
    assert resp.status_code == 400
    assert "lab firmware" in resp.json()["detail"]


async def test_sensor_test_shows_every_channel(client, lab):
    printer = await connect(client)
    printer.temperatures["I3"] = (14.94, 15.0)
    printer.modes["I3"] = "C"

    body = (await client.post("/temperature/test")).json()

    assert body["reply"].splitlines()[0] == "Status:       [Disabled, Disabled, Disabled, Cooling, Disabled]"
    assert body["reply"].splitlines()[-1] == "ok"
    assert [(s["sensor"], s["name"], s["actual"], s["firmware_status"]) for s in body["sensors"]] == [
        ("I0", "Left syringe (I0)", None, "Disabled"),
        ("I1", "Right syringe (I1)", None, "Disabled"),
        ("I2", "Bed (I2)", None, "Disabled"),
        ("I3", "I3 (not used)", 14.94, "Cooling"),
    ]


async def test_print_waits_for_the_channels(client, lab, ready_to_print):  # noqa: F811
    worker = app.state.queue_worker
    store = app.state.temperature
    store.config.settle_s = 0.3
    assert (await target(client, "I1", 30, "heat")).is_success
    await wait_for(lambda: store.latest["I1"].target == 30)

    assert (await client.post("/print/start", json={"wait_for_temperature": True})).status_code == 200
    await wait_for(lambda: worker.waiting_for_temperature)
    # The disabled channels aren't waited for
    assert [s.name for s in store.wait_status()] == ["Right syringe (I1)"]
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED, timeout=10)
    actual = store.latest["I1"].actual
    assert actual is not None and abs(actual - 30) <= 1


# --- what each channel heats ------------------------------------------------------------------


async def test_channels_are_saved_and_kept_across_restarts(client, lab, data_dir):
    stored = json.loads((data_dir / "settings.json").read_text())
    assert stored["temperature_channels"] == {"I0": "syringe:left", "I1": "syringe:right", "I2": "bed"}

    app.state.history._traffic.stop()
    app.state.db.close()
    wire(app, make_config(), init_db(Path(":memory:")), SettingsStore(data_dir / "settings.json"))

    names = [c["name"] for c in (await get_status(client))["channels"]]
    assert names == ["Left syringe (I0)", "Right syringe (I1)", "Bed (I2)", "I3 (not used)"]


async def test_channels_apply_at_once(client, lab):
    store = app.state.temperature
    sub_id, events = app.state.event_bus.subscribe()
    store.record(block(I0=("Heating", 20.0, 37.0), I3=("Heating", 25.0, 30.0)))
    assert list(store.latest) == ["I0", "I1", "I2"]

    resp = await put_settings(
        client, temperature_channels={"I0": "bed", "I1": "unused", "I3": "syringe:right"}
    )
    assert resp.json()["temperature_channels"] == {"I0": "bed", "I3": "syringe:right"}
    # I1 and I2 are dropped at once; I3 shows from the next report
    assert [(key, store.sensor(key).name) for key in store.latest] == [("I0", "Bed (I0)")]
    store.record(block(I0=("Heating", 21.0, 37.0), I3=("Heating", 25.0, 30.0)))
    assert [(key, store.sensor(key).name) for key in store.latest] == [
        ("I0", "Bed (I0)"),
        ("I3", "Right syringe (I3)"),
    ]

    published = []
    while not events.empty():
        published.append(events.get_nowait())
    app.state.event_bus.unsubscribe(sub_id)
    assert {"type": "settings", "temperature_channels": {"I0": "bed", "I3": "syringe:right"}}.items() <= next(
        e for e in published if e["type"] == "settings"
    ).items()
    statuses = [e for e in published if e["type"] == "temperature_status"]
    assert statuses[-1]["channels"][0]["name"] == "Bed (I0)"


@pytest.mark.parametrize(
    ("channels", "detail"),
    [
        ({"I7": "bed"}, "Unknown temperature channel 'I7'"),
        ({"I0": "syringe:nope"}, "choose unused, bed or a syringe (syringe:left, syringe:right)"),
        ({"I0": "toaster"}, "I0 can't be used for 'toaster'"),
    ],
)
async def test_invalid_channels_are_refused(client, lab, channels, detail):
    resp = await put_settings(client, temperature_channels=channels)
    assert resp.status_code == 400
    assert detail in resp.json()["detail"]
    assert app.state.settings.current.temperature_channels["I0"] == "syringe:left"


async def test_a_syringe_the_profile_lacks_falls_back_to_not_used(client, lab, data_dir):
    sub_id, events = app.state.event_bus.subscribe()

    await save_four(client)  # the Custom profile: syringes s1..s4, no left or right

    published = []
    while not events.empty():
        published.append(events.get_nowait())
    app.state.event_bus.unsubscribe(sub_id)
    warnings = [e["message"] for e in published if e["type"] == "warning"]
    assert any("Temperature channel I0, I1 was assigned to a syringe" in w for w in warnings)
    assert app.state.settings.current.temperature_channels == {"I2": "bed"}
    assert json.loads((data_dir / "settings.json").read_text())["temperature_channels"] == {"I2": "bed"}
    assert [c["use"] for c in (await get_status(client))["channels"]] == [None, None, "bed", None]

    # The new profile's syringes can be assigned, named as it names them
    assert (await put_settings(client, temperature_channels={"I1": "syringe:s2"})).is_success
    assert (await get_status(client))["channels"][1]["name"] == "Two syringe (I1)"


async def test_names_in_the_csv_export(client, lab):
    store = app.state.temperature
    store.record(block(I1=("Heating", 22.0, 30.0)))
    await put_settings(client, temperature_channels={"I1": "bed"})
    store.record(block(I1=("Heating", 23.0, 30.0)))

    resp = await client.get(
        "/temperature/export.csv", params={"from": "2000-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z"}
    )
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert [(r["sensor"], r["name"], r["actual"]) for r in rows] == [
        ("I1", "Right syringe (I1)", "22"),
        ("I1", "Bed (I1)", "23"),
    ]
