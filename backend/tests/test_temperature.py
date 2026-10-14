"""Temperature store: latest readings, rolling history, logging to SQLite."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from backend.config import ChannelConfig, SensorConfig, TemperatureConfig
from backend.database import init_db, insert_temperature_readings, temperature_readings
from backend.main import app
from backend.queue_worker import PrintStatus
from backend.temperature import (
    FLUSH_INTERVAL_S,
    HISTORY_S,
    TargetError,
    TemperatureStore,
    channel_command,
    target_command,
)
from tests.serial_fakes import attach
from tests.test_history import FakeSerial, upload_stl, wait_for

DAY_S = 24 * 60 * 60
# What a status says about the heaters with the standard protocol
MARLIN = {"protocol": "marlin", "channels": []}


class Clock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def report(**sensors: tuple[float, float | None]) -> dict[str, dict[str, float | None]]:
    return {key: {"actual": actual, "target": target} for key, (actual, target) in sensors.items()}


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def db() -> sqlite3.Connection:
    return init_db(Path(":memory:"))


def make_store(db, clock, **config) -> TemperatureStore:
    return TemperatureStore(db, TemperatureConfig(**config), clock=clock)


def rows(db) -> list[tuple]:
    return temperature_readings(db)


# --- latest reading and history --------------------------------------------------


def test_keeps_the_latest_reading_per_sensor(db, clock):
    store = make_store(db, clock)
    store.record(report(T=(21.0, 0.0), B=(20.0, 0.0)))
    clock.advance(2)
    store.record(report(T=(22.5, 37.0)))

    latest = store.latest
    assert (latest["T"].actual, latest["T"].target, latest["T"].timestamp) == (22.5, 37.0, clock.now)
    assert latest["B"].actual == 20.0


def test_history_rolls_after_two_hours(db, clock):
    store = make_store(db, clock)
    start = clock.now
    for _ in range(int(HISTORY_S / 60) + 30):  # 2.5 h, one reading a minute
        store.record(report(T=(21.0, 0.0)))
        clock.advance(60)

    clock.advance(-60)  # back to the last reading's time
    readings = store.history(HISTORY_S * 2)["T"]
    assert readings[0].timestamp >= clock.now - HISTORY_S
    assert readings[0].timestamp > start
    assert readings[-1].timestamp == clock.now

    last_ten_minutes = store.history(10 * 60)["T"]
    assert len(last_ten_minutes) == 11


def test_with_several_tools_bare_t_is_dropped(db, clock):
    """Marlin's "T:" then repeats the active tool's T<n>."""
    store = make_store(db, clock)
    store.record(report(T=(30.0, 37.0), T0=(30.0, 37.0), T1=(21.0, 0.0)))
    assert set(store.latest) == {"T0", "T1"}


def test_sensor_names_and_ranges_from_config(db, clock):
    store = make_store(db, clock, sensors={"T0": SensorConfig(name="Left syringe", min=4, max=60)})
    assert store.sensor("T0") == SensorConfig(name="Left syringe", min=4, max=60)
    assert store.sensor("B") == SensorConfig(name="B", min=0, max=120)


def test_sensor_range_must_be_ordered():
    with pytest.raises(ValueError):
        SensorConfig(name="x", min=60, max=4)


# --- logging ---------------------------------------------------------------------


def test_readings_are_written_in_batches(db, clock):
    store = make_store(db, clock)
    store.record(report(T=(21.0, 0.0), B=(20.0, 0.0)))
    clock.advance(FLUSH_INTERVAL_S / 2)
    store.record(report(T=(21.1, 0.0), B=(20.1, 0.0)))
    assert rows(db) == []

    clock.advance(FLUSH_INTERVAL_S / 2)
    store.record(report(T=(21.2, 0.0), B=(20.2, 0.0)))
    assert [(sensor, actual) for _, sensor, actual, _, _ in rows(db)] == [
        ("T", 21.0),
        ("B", 20.0),
        ("T", 21.1),
        ("B", 20.1),
        ("T", 21.2),
        ("B", 20.2),
    ]

    clock.advance(1)
    store.record(report(T=(21.3, 0.0)))
    assert len(rows(db)) == 6
    store.flush()
    assert len(rows(db)) == 7


def test_batch_uses_one_insert(db, clock, monkeypatch):
    from backend import temperature

    calls: list[int] = []
    insert = temperature.insert_temperature_readings
    monkeypatch.setattr(
        temperature, "insert_temperature_readings", lambda conn, r: calls.append(len(r)) or insert(conn, r)
    )
    store = make_store(db, clock)
    for _ in range(5):
        store.record(report(T=(21.0, 0.0), B=(20.0, 0.0)))
        clock.advance(2)
    store.record(report(T=(21.0, 0.0), B=(20.0, 0.0)))

    assert calls == [12]


def test_retention_deletes_old_readings(db, clock):
    insert_temperature_readings(
        db,
        [
            (clock.now - 31 * DAY_S, "T", 20.0, 0.0, None),
            (clock.now - 29 * DAY_S, "T", 21.0, 0.0, None),
            (clock.now - 1, "T", 22.0, 0.0, None),
        ],
    )
    assert make_store(db, clock).purge_expired() == 1
    assert [actual for _, _, actual, _, _ in rows(db)] == [21.0, 22.0]

    assert make_store(db, clock, retention_days=7).purge_expired() == 1
    assert [actual for _, _, actual, _, _ in rows(db)] == [22.0]


def test_old_readings_are_purged_at_startup(db):
    from backend.config import load_config
    from backend.main import wire

    now = time.time()
    insert_temperature_readings(
        db, [(now - 365 * DAY_S, "T", 20.0, 0.0, None), (now - DAY_S, "T", 21.0, 0.0, None)]
    )
    wire(app, load_config(), db)
    try:
        assert [actual for _, _, actual, _, _ in rows(db)] == [21.0]
    finally:
        app.state.history._traffic.stop()


def test_existing_database_gets_the_table(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, "
        "ended_at TEXT, filename TEXT NOT NULL, syringe_config TEXT NOT NULL, "
        "total_lines INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0)"
    )
    old.execute(
        "INSERT INTO sessions (started_at, filename, syringe_config, total_lines) "
        "VALUES ('x', 'a', 'left', 1)"
    )
    old.commit()
    old.close()

    conn = init_db(path)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(temperature_readings)")]
    assert columns == ["id", "timestamp", "sensor", "actual", "target", "session_id"]
    assert conn.execute("SELECT filename FROM sessions").fetchall() == [("a",)]
    conn.close()


# --- print sessions ------------------------------------------------------------------


async def test_readings_during_a_print_carry_its_session(client, tmp_path):
    assert (await client.put("/settings", json={"temperature_sensors": True})).status_code == 200
    attach(app.state.serial_manager, FakeSerial())
    app.state.session.zeroed = {"X", "Y", "Z", "A"}
    app.state.config.nozzle_offset_measured = True
    bus = app.state.event_bus
    store: TemperatureStore = app.state.temperature
    worker = app.state.queue_worker

    bus.publish({"type": "temperature", "temperatures": report(T=(20.0, 0.0))})
    await upload_stl(client, tmp_path, 400)
    assert (await client.post("/print/start")).status_code == 200
    await wait_for(lambda: worker.lines_sent >= 5)
    bus.publish({"type": "temperature", "temperatures": report(T=(21.0, 0.0))})
    await client.post("/print/stop")
    assert worker.status == PrintStatus.STOPPED
    bus.publish({"type": "temperature", "temperatures": report(T=(22.0, 0.0))})
    store.flush()

    [session] = (await client.get("/history")).json()["sessions"]
    assert [(actual, session_id) for _, _, actual, _, session_id in rows(app.state.db)] == [
        (20.0, None),
        (21.0, session["id"]),
        (22.0, None),
    ]


# --- status ------------------------------------------------------------------------


def test_heater_status(db, clock):
    store = TemperatureStore(db, TemperatureConfig(), is_connected=lambda: True, clock=clock)
    store.record(report(T0=(36.5, 37.0), T1=(30.0, 37.0), B=(10.0, 4.0), C=(25.0, 0.0), P=(22.0, None)))
    status = store.status()
    assert status.state == "ok"
    assert {s.sensor: s.status for s in status.sensors} == {
        "T0": "at_target",
        "T1": "heating",
        "B": "cooling",
        "C": "off",
        "P": "off",
    }
    assert {s.sensor for s in status.sensors if s.settable} == {"T0", "T1", "B", "C"}


def test_sensors_in_config_order_then_by_key(db, clock):
    config = TemperatureConfig(
        sensors={
            "T1": SensorConfig(name="Right"),
            "B": SensorConfig(name="Bed"),
            "T0": SensorConfig(name="Left"),
        }
    )
    store = TemperatureStore(db, config, is_connected=lambda: True, clock=clock)
    store.record(report(B=(20.0, 0.0), W=(20.0, None), C=(20.0, 0.0), T0=(20.0, 0.0), T1=(20.0, 0.0)))
    assert [s.sensor for s in store.status().sensors] == ["T1", "B", "T0", "C", "W"]
    assert [s.name for s in store.status().sensors] == ["Right", "Bed", "Left", "C", "W"]


def test_no_sensor_state(db, clock):
    connected = False
    store = TemperatureStore(
        db, TemperatureConfig(report_timeout_s=15), is_connected=lambda: connected, clock=clock
    )
    assert store.status().state == "disconnected"

    connected = True
    store.on_event({"type": "printer", "connected": True, "port": "virtual"})
    assert store.status().state == "waiting"
    clock.advance(15)
    assert store.status().model_dump() == {
        "state": "no_sensors",
        "monitor": "autoreport",
        "sensors": [],
        **MARLIN,
    }

    store.record(report(T=(21.0, 0.0)))
    assert store.status().state == "ok"


def test_reconnecting_forgets_the_old_readings(db, clock):
    store = TemperatureStore(db, TemperatureConfig(), is_connected=lambda: True, clock=clock)
    store.record(report(T=(21.0, 0.0)))
    store.on_event({"type": "printer", "connected": False, "port": None})
    store.on_event({"type": "printer", "connected": True, "port": "virtual"})
    assert store.status().state == "waiting"
    assert len(store.history(60)["T"]) == 1  # the chart keeps them


def test_status_is_published_after_each_report(db, clock):
    events: list[dict] = []
    store = TemperatureStore(
        db, TemperatureConfig(), is_connected=lambda: True, publish=events.append, clock=clock
    )
    store.record(report(T=(21.0, 30.0)))
    assert events[-1]["type"] == "temperature_status"
    assert events[-1]["state"] == "ok"
    assert events[-1]["sensors"][0]["status"] == "heating"


# --- target commands ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sensor", "target", "command"),
    [
        ("T", 37, "M104 S37"),
        ("T0", 37.5, "M104 T0 S37.5"),
        ("T1", 0, "M104 T1 S0"),
        ("B", 25, "M140 S25"),
        ("C", 30, "M141 S30"),
    ],
)
def test_target_commands(sensor, target, command):
    assert target_command(sensor, target) == command


@pytest.mark.parametrize("sensor", ["P", "R", "W", "X"])
def test_sensors_without_heater_have_no_target(sensor):
    with pytest.raises(TargetError):
        target_command(sensor, 30)


def test_target_range(db, clock):
    store = make_store(db, clock, sensors={"T0": SensorConfig(name="Left syringe", min=4, max=60)})
    assert store.check_target("T0", 4) == "M104 T0 S4"
    assert store.check_target("T0", 60) == "M104 T0 S60"
    assert store.check_target("T0", 0) == "M104 T0 S0"  # off
    for bad in (3.9, 60.1, -5):
        with pytest.raises(TargetError, match="from 4 to 60"):
            store.check_target("T0", bad)
    # Unknown sensors get 0-120 °C
    assert store.check_target("B", 120) == "M140 S120"
    with pytest.raises(TargetError):
        store.check_target("B", 121)


# --- CSV -------------------------------------------------------------------------------


def test_csv(db, clock):
    store = make_store(db, clock, sensors={"T0": SensorConfig(name="Left syringe")})
    store.record(report(T0=(21.25, 37.0), P=(20.0, None)))
    clock.advance(2.5)
    store.record(report(T0=(22.0, 37.0)))

    assert store.csv(store.logged(start=0, end=clock.now)).splitlines() == [
        "timestamp,sensor,name,actual,target",
        "2023-11-14T22:13:20.000+00:00,T0,Left syringe,21.25,37",
        "2023-11-14T22:13:20.000+00:00,P,P,20,",
        "2023-11-14T22:13:22.500+00:00,T0,Left syringe,22,37",
    ]


# --- warnings ----------------------------------------------------------------------------


class Watched:
    """A store as the app wires it, with the printing and connection state
    in plain attributes and the published events collected."""

    def __init__(self, db, clock, **config):
        self.connected = True
        self.printing = True
        self.events: list[dict] = []
        self.store = TemperatureStore(
            db,
            TemperatureConfig(**config),
            is_connected=lambda: self.connected,
            is_printing=lambda: self.printing,
            publish=self.events.append,
            clock=clock,
        )
        self.store.on_event({"type": "printer", "connected": True, "port": "virtual"})

    def warnings(self) -> list[str]:
        return [e["message"] for e in self.events if e["type"] == "warning"]


@pytest.fixture
def watched(db, clock) -> Watched:
    return Watched(db, clock)


def tick(watched: Watched, clock: Clock, seconds: float, **sensors) -> None:
    """Let `seconds` pass with a report every 2 s and a check every second."""
    end = clock.now + seconds
    while clock.now < end:
        step = min(1.0, end - clock.now)
        clock.advance(step)
        if sensors and int(clock.now) % 2 == 0:
            watched.store.record(report(**sensors))
        watched.store.check()


def test_deviation_from_target_while_printing(watched, clock):
    watched.store.record(report(T0=(30.0, 37.0)))
    tick(watched, clock, 59, T0=(30.0, 37.0))
    assert watched.warnings() == []

    tick(watched, clock, 2, T0=(30.0, 37.0))
    assert watched.warnings() == ["T0 is at 30.0 °C, more than 3 °C from its 37 °C target for over 60 s."]
    tick(watched, clock, 120, T0=(30.0, 37.0))
    assert len(watched.warnings()) == 1  # once per excursion

    tick(watched, clock, 4, T0=(35.0, 37.0))  # back within 3 °C
    tick(watched, clock, 62, T0=(41.0, 37.0))
    assert len(watched.warnings()) == 2


def test_deviation_within_tolerance_or_shorter_is_fine(watched, clock):
    tick(watched, clock, 120, T0=(34.0, 37.0))  # 3 °C off: within tolerance
    tick(watched, clock, 50, T0=(20.0, 37.0))
    tick(watched, clock, 4, T0=(36.0, 37.0))
    tick(watched, clock, 50, T0=(20.0, 37.0))
    assert watched.warnings() == []


def test_new_target_restarts_the_grace_period(watched, clock):
    tick(watched, clock, 50, T0=(20.0, 37.0))
    tick(watched, clock, 50, T0=(20.0, 40.0))
    assert watched.warnings() == []
    tick(watched, clock, 12, T0=(20.0, 40.0))
    assert len(watched.warnings()) == 1


def test_no_deviation_warning_unless_printing(watched, clock):
    watched.printing = False  # also while a print waits for its temperatures
    tick(watched, clock, 120, T0=(20.0, 37.0), B=(20.0, 0.0))
    assert watched.warnings() == []


def test_deviation_tolerance_is_configurable(db, clock):
    watched = Watched(db, clock, deviation_c=1.0, deviation_s=10)
    tick(watched, clock, 12, T0=(35.5, 37.0))
    assert watched.warnings() == ["T0 is at 35.5 °C, more than 1 °C from its 37 °C target for over 10 s."]


def test_reports_stopping(watched, clock):
    watched.printing = False
    tick(watched, clock, 10, B=(20.0, 0.0))
    tick(watched, clock, 14)
    assert watched.warnings() == []
    tick(watched, clock, 2)
    assert watched.warnings() == ["No temperature report from the printer for 15 s."]
    tick(watched, clock, 60)
    assert len(watched.warnings()) == 1

    tick(watched, clock, 2, B=(20.0, 0.0))  # reports again
    tick(watched, clock, 16)
    assert len(watched.warnings()) == 2


def test_no_report_warning_while_disconnected(watched, clock):
    tick(watched, clock, 4, B=(20.0, 0.0))
    watched.connected = False
    watched.store.on_event({"type": "printer", "connected": False, "port": None})
    tick(watched, clock, 60)
    assert watched.warnings() == []


def test_no_sensors_is_a_state_not_a_warning(watched, clock):
    tick(watched, clock, 14)
    assert watched.events[-1]["state"] == "waiting"
    tick(watched, clock, 2)
    assert watched.events[-1] == {
        "type": "temperature_status",
        "state": "no_sensors",
        "monitor": "autoreport",
        "sensors": [],
        **MARLIN,
    }
    tick(watched, clock, 60)
    assert watched.warnings() == []


@pytest.mark.parametrize("actual", [-20.5, 300.5, -273.0, 999.0])
def test_implausible_reading(watched, clock, actual):
    watched.store.record(report(B=(actual, 0.0)))
    watched.store.record(report(B=(actual, 0.0)))
    assert watched.warnings() == [
        f"B reads {actual:g} °C, which isn't plausible. Check that the sensor is connected."
    ]
    watched.store.record(report(B=(20.0, 0.0)))
    watched.store.record(report(B=(actual, 0.0)))
    assert len(watched.warnings()) == 2


@pytest.mark.parametrize("actual", [-20.0, 4.0, 300.0])
def test_plausible_reading(watched, clock, actual):
    watched.store.record(report(B=(actual, 0.0)))
    assert watched.warnings() == []


async def test_watchdog_runs_while_connected(db, monkeypatch):
    from backend import temperature

    monkeypatch.setattr(temperature, "WATCH_INTERVAL_S", 0.01)
    watched = Watched(db, time.time, report_timeout_s=0.05)
    watched.store.record(report(B=(20.0, 0.0)))
    await asyncio.sleep(0.2)
    assert watched.warnings() == ["No temperature report from the printer for 0.05 s."]

    watched.store.on_event({"type": "printer", "connected": False, "port": None})
    assert watched.store._watchdog is None
    await watched.store.close()


# --- waiting for temperatures -----------------------------------------------------------------


def test_targets_reached_once_held_for_the_settle_time(db, clock):
    store = make_store(db, clock, settle_s=30, target_band_c=1.0)
    store.record(report(T0=(30.0, 37.0), T1=(21.0, 0.0), B=(20.0, 0.0)))
    assert not store.targets_reached()
    assert [(s.sensor, s.stable_s) for s in store.wait_status()] == [("T0", 0.0)]

    clock.advance(2)
    store.record(report(T0=(36.2, 37.0)))  # within ±1 °C from here
    clock.advance(28)
    store.record(report(T0=(37.9, 37.0)))
    assert store.wait_status()[0].stable_s == 28
    assert not store.targets_reached()
    clock.advance(2)
    store.record(report(T0=(37.0, 37.0)))
    assert store.targets_reached()


def test_leaving_the_band_restarts_the_settle_time(db, clock):
    store = make_store(db, clock, settle_s=30)
    store.record(report(B=(30.0, 30.0)))
    clock.advance(20)
    store.record(report(B=(28.5, 30.0)))
    clock.advance(20)
    store.record(report(B=(30.0, 30.0)))
    clock.advance(20)
    store.record(report(B=(30.0, 30.0)))
    assert not store.targets_reached()
    clock.advance(10)
    store.record(report(B=(30.0, 30.0)))
    assert store.targets_reached()


def test_every_target_has_to_hold(db, clock):
    store = make_store(db, clock, settle_s=10)
    store.record(report(T0=(37.0, 37.0), B=(20.0, 30.0)))
    clock.advance(10)
    store.record(report(T0=(37.0, 37.0), B=(25.0, 30.0)))
    assert not store.targets_reached()


def test_nothing_to_wait_for_without_targets(db, clock):
    store = make_store(db, clock)
    assert store.targets_reached()  # no sensors at all
    store.record(report(T0=(21.0, 0.0), P=(22.0, None)))
    assert store.targets_reached()


# --- lab firmware (printess) channels ---------------------------------------------------------


CHANNELS = [
    ChannelConfig(index=n, name=name, reply_key=f"T{n}", min=4, max=60)
    for n, name in enumerate(["Left syringe", "Right syringe", "Bed", "Chamber"])
]


def make_printess(db, clock, **config) -> TemperatureStore:
    """A connected store for the lab firmware's channels."""
    config = {"protocol": "printess", "channels": CHANNELS, **config}
    return TemperatureStore(db, TemperatureConfig(**config), is_connected=lambda: True, clock=clock)


def sent(store: TemperatureStore, sensor: str, target: float) -> str:
    """Set a target as the session does: plan it, (send it,) note it sent."""
    plan = store.plan_target(sensor, target)
    store.target_sent(plan)
    return plan.command


@pytest.mark.parametrize(
    ("actual", "target", "command", "mode"),
    [
        (21.0, 37, "M104 I1 T37 H", "heat"),  # above the reading: heat
        (21.0, 10.5, "M104 I1 T10.5 C", "cool"),  # below: cool
        (37.0, 37, "M104 I1 T37 H", "heat"),  # equal: heat
    ],
)
def test_channel_command_heats_or_cools_from_the_reading(actual, target, command, mode):
    assert channel_command(CHANNELS[1], target, actual, "M104 I{n} T0") == (command, mode)


def test_channel_command_needs_a_reading():
    with pytest.raises(TargetError, match="No reading from Right syringe yet"):
        channel_command(CHANNELS[1], 37, None, "M104 I{n} T0")


def test_channel_off_uses_the_off_command_without_a_reading():
    assert channel_command(CHANNELS[2], 0, None, "M104 I{n} T0") == ("M104 I2 T0", None)
    assert channel_command(CHANNELS[2], 0, 30.0, "M104 I{n} S0 X") == ("M104 I2 S0 X", None)


def test_channel_targets_from_the_store(db, clock):
    store = make_printess(db, clock)
    store.record(report(T0=(21.0, None), T1=(30.0, None)))

    assert store.check_target("I0", 37) == "M104 I0 T37 H"
    assert store.check_target("I1", 20) == "M104 I1 T20 C"
    assert store.check_target("I1", 30) == "M104 I1 T30 H"
    assert store.check_target("I0", 0) == "M104 I0 T0"
    with pytest.raises(TargetError, match="No reading from Bed yet"):
        store.check_target("I2", 30)  # not in the report


@pytest.mark.parametrize("target", [3.9, 60.1, -5])
def test_channel_target_out_of_range(db, clock, target):
    store = make_printess(db, clock)
    store.record(report(T0=(21.0, None)))
    with pytest.raises(TargetError, match="Left syringe accepts targets from 4 to 60"):
        store.check_target("I0", target)


@pytest.mark.parametrize("sensor", ["T0", "B", "I7"])
def test_only_channels_are_settable_with_printess(db, clock, sensor):
    store = make_printess(db, clock)
    with pytest.raises(TargetError, match=f"Sensor {sensor} isn't a heater channel"):
        store.check_target(sensor, 30)


def test_marlin_ignores_the_channels(db, clock):
    store = TemperatureStore(db, TemperatureConfig(channels=CHANNELS), is_connected=lambda: True, clock=clock)
    store.record(report(T0=(21.0, 0.0)))
    assert store.check_target("T0", 37) == "M104 T0 S37"
    with pytest.raises(TargetError):
        store.check_target("I0", 37)
    assert [s.sensor for s in store.status().sensors] == ["T0"]
    assert store.status().channels == []


def test_reply_keys_map_to_channels(db, clock):
    store = make_printess(db, clock)
    store.record(report(T=(21.0, None), T0=(21.0, None), T1=(22.0, None), T3=(23.0, None), B=(20.0, None)))
    status = store.status()
    # Channels in config order, then what no channel reads from; Marlin's
    # extra "T:" is dropped
    assert [(s.sensor, s.name, s.settable) for s in status.sensors] == [
        ("I0", "Left syringe", True),
        ("I1", "Right syringe", True),
        ("I3", "Chamber", True),
        ("B", "B", False),
    ]
    assert status.protocol == "printess"
    assert [(c.sensor, c.index, c.reply_key) for c in status.channels] == [
        ("I0", 0, "T0"),
        ("I1", 1, "T1"),
        ("I2", 2, "T2"),
        ("I3", 3, "T3"),
    ]


def test_reply_key_mapping_is_configurable(db, clock):
    channels = [
        ChannelConfig(index=0, name="Syringe", reply_key="B"),
        ChannelConfig(index=1, name="Two", reply_key="T"),
    ]
    store = make_printess(db, clock, channels=channels)
    store.record(report(T=(30.0, None), T0=(25.0, None), B=(20.0, None)))
    assert {s.sensor: s.actual for s in store.status().sensors} == {"I0": 20.0, "I1": 30.0, "T0": 25.0}


def test_channel_keeps_the_target_sent_when_the_reply_has_none(db, clock):
    store = make_printess(db, clock)
    store.record(report(T0=(21.0, None), T1=(30.0, None)))
    assert sent(store, "I0", 37) == "M104 I0 T37 H"
    assert sent(store, "I1", 10) == "M104 I1 T10 C"

    clock.advance(1)
    store.record(report(T0=(25.0, None), T1=(28.0, None)))
    readings = {s.sensor: s for s in store.status().sensors}
    assert (readings["I0"].target, readings["I0"].mode, readings["I0"].status) == (37, "heat", "heating")
    assert (readings["I1"].target, readings["I1"].mode, readings["I1"].status) == (10, "cool", "cooling")

    # Heating: shown as heating even while above the target, which it can't cool to
    clock.advance(1)
    store.record(report(T0=(40.0, None)))
    assert store.status().sensors[0].status == "heating"
    store.record(report(T0=(37.5, None)))
    assert store.status().sensors[0].status == "at_target"

    assert sent(store, "I0", 0) == "M104 I0 T0"
    reading = store.status().sensors[0]
    assert (reading.target, reading.mode, reading.status) == (0, None, "off")


def test_target_in_the_reply_wins(db, clock):
    store = make_printess(db, clock)
    store.record(report(T0=(21.0, None)))
    sent(store, "I0", 37)
    store.record(report(T0=(22.0, 37.0)))
    assert (store.status().sensors[0].target, store.status().sensors[0].mode) == (37, "heat")
    # A different target than the one sent: its direction isn't known
    store.record(report(T0=(22.0, 30.0)))
    reading = store.status().sensors[0]
    assert (reading.target, reading.mode, reading.status) == (30, None, "heating")


def test_targets_sent_are_forgotten_on_reconnect(db, clock):
    store = make_printess(db, clock)
    store.record(report(T0=(21.0, None)))
    sent(store, "I0", 37)
    store.on_event({"type": "printer", "connected": True})
    store.record(report(T0=(21.0, None)))
    assert store.status().sensors[0].target is None


@pytest.mark.parametrize(
    ("line", "target", "mode"),
    [
        ("M104 I1 T30 C", 30, "cool"),
        ("m104  i1 t42.5 h", 42.5, "heat"),
        ("M104 I1 T0", 0, None),  # the off command
    ],
)
def test_channel_target_typed_by_hand_counts(db, clock, line, target, mode):
    store = make_printess(db, clock)
    store.record(report(T1=(35.0, None)))
    store.note_command(line)
    reading = store.status().sensors[0]
    assert (reading.target, reading.mode) == (target, mode)


@pytest.mark.parametrize("line", ["M104 I1 T30", "M104 I9 T30 H", "M104 T1 S30", "G1 X1"])
def test_other_lines_typed_by_hand_set_no_target(db, clock, line):
    store = make_printess(db, clock)
    store.record(report(T1=(35.0, None)))
    store.note_command(line)
    assert store.status().sensors[0].target is None


def test_waiting_for_channels(db, clock):
    store = make_printess(db, clock, settle_s=30)
    store.record(report(T0=(21.0, None), T1=(21.0, None)))
    sent(store, "I0", 37)
    assert not store.targets_reached()
    for _ in range(4):
        clock.advance(10)
        store.record(report(T0=(37.2, None), T1=(21.0, None)))
    assert [(s.sensor, s.name, s.target) for s in store.wait_status()] == [("I0", "Left syringe", 37)]
    assert store.targets_reached()


@pytest.mark.parametrize(
    ("config", "error"),
    [
        ({"channels": [{"index": 0, "name": "A", "reply_key": "X0"}]}, "isn't a sensor key"),
        (
            {
                "channels": [
                    {"index": 0, "name": "A", "reply_key": "T0"},
                    {"index": 0, "name": "B", "reply_key": "T1"},
                ]
            },
            "same index",
        ),
        (
            {
                "channels": [
                    {"index": 0, "name": "A", "reply_key": "T0"},
                    {"index": 1, "name": "B", "reply_key": "T0"},
                ]
            },
            "same reply_key",
        ),
        ({"off_command": "M104 I{channel} T0"}, "can only use"),
        ({"protocol": "reprap"}, "protocol"),
    ],
)
def test_channel_config_is_checked(config, error):
    with pytest.raises(ValueError, match=error):
        TemperatureConfig(**config)


def test_default_channels():
    config = TemperatureConfig()
    assert config.protocol == "marlin"
    assert [(c.sensor, c.reply_key) for c in config.channels] == [
        ("I0", "T0"),
        ("I1", "T1"),
        ("I2", "T2"),
        ("I3", "T3"),
    ]
    assert config.off_command == "M104 I{n} T0"
