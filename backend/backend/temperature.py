"""Temperatures: the latest reading and the last two hours per sensor, and a
log of every reading in the database.

TemperatureStore follows the TemperatureEvents the serial manager publishes
on the event bus. Readings are written to the database in batches, at most
FLUSH_INTERVAL_S apart, each tagged with the print session running at the
time (if any). After every report, and whenever the state changes, the
store publishes a TemperatureStatusEvent with what GET /temperature returns.

While the printer is connected, a watchdog checks once a second for what
deserves a WarningEvent: a sensor far from its target for too long during a
print, temperature reports that stopped coming, and implausible readings.
None of this replaces the firmware's thermal runaway protection, which
must stay enabled.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import logging
import re
import sqlite3
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.config import SensorConfig, TemperatureConfig
from backend.database import (
    TemperatureRow,
    delete_temperature_readings_before,
    insert_temperature_readings,
    temperature_readings,
)
from backend.schemas import (
    HeaterStatus,
    SensorInfo,
    SensorReading,
    TemperatureSeries,
    TemperatureState,
    TemperatureStatus,
    TemperatureStatusEvent,
    TemperatureWaitSensor,
    WarningEvent,
)

logger = logging.getLogger(__name__)

# How far back the in-memory history (the live chart) goes
HISTORY_S = 2 * 60 * 60
# Readings are written to the database at most this far apart
FLUSH_INTERVAL_S = 10.0
# How often the watchdog checks for warnings
WATCH_INTERVAL_S = 1.0
# Readings outside this range usually mean a disconnected or shorted sensor
PLAUSIBLE_MIN_C = -20.0
PLAUSIBLE_MAX_C = 300.0

_TOOL = re.compile(r"^T(\d+)$")
# Sensors with a heater: the hotend(s), the bed and the chamber. The others
# Marlin reports (P probe, R redundant, W cooler...) are read-only.
_HEATER = re.compile(r"^(T\d*|B|C)$")


class TargetError(ValueError):
    """A target the sensor can't be set to."""


@dataclass(frozen=True)
class Reading:
    timestamp: float  # Unix seconds
    actual: float
    target: float | None


def target_command(sensor: str, target: float) -> str:
    """The G-code that sets `sensor`'s target without waiting for it
    (never M109/M190, which would block the command queue)."""
    value = f"S{target:g}"
    if sensor == "T":
        return f"M104 {value}"
    tool = _TOOL.match(sensor)
    if tool:
        return f"M104 T{tool.group(1)} {value}"
    if sensor == "B":
        return f"M140 {value}"
    if sensor == "C":
        return f"M141 {value}"
    raise TargetError(f"Sensor {sensor} has no heater")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="milliseconds")


class TemperatureStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        config: TemperatureConfig,
        *,
        session_id: Callable[[], int | None] = lambda: None,
        is_connected: Callable[[], bool] = lambda: False,
        is_printing: Callable[[], bool] = lambda: False,
        publish: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._conn = conn
        self.config = config
        # The print session readings belong to right now, if any
        self._session_id = session_id
        self._is_connected = is_connected
        # A print is running and not just waiting for its temperatures
        self._is_printing = is_printing
        self._publish = publish
        self._clock = clock
        self._latest: dict[str, Reading] = {}
        self._history: dict[str, deque[Reading]] = {}
        # Readings not yet written to the database
        self._pending: list[TemperatureRow] = []
        self._last_flush = clock()
        # When the printer was last connected; None while it isn't
        self._connected_at: float | None = None
        # The last report since then
        self._last_report: float | None = None
        self._published_state: TemperatureState | None = None
        # Per sensor: (target, since when it has been within target_band_c of it)
        self._settled: dict[str, tuple[float, float]] = {}
        # Per sensor, while printing: (target, since when it has been further
        # than deviation_c from it)
        self._deviating: dict[str, tuple[float, float]] = {}
        # Conditions warned about that haven't cleared yet, e.g. ("deviation", "T0").
        # Each is warned about once until it clears.
        self._warned: set[tuple[str, str]] = set()
        self._watchdog: asyncio.Task | None = None

    # --- sensors ------------------------------------------------------------------

    def sensor(self, key: str) -> SensorConfig:
        """The configured name and range of sensor `key`, or its raw key with
        the default range."""
        return self.config.sensors.get(key) or SensorConfig(name=key)

    def info(self, key: str) -> SensorInfo:
        sensor = self.sensor(key)
        return SensorInfo(
            sensor=key,
            name=sensor.name,
            min=sensor.min,
            max=sensor.max,
            settable=_HEATER.match(key) is not None,
        )

    def _order(self, keys: Iterable[str]) -> list[str]:
        """Configured sensors in config order, then the others by key."""
        configured = list(self.config.sensors)

        def rank(key: str) -> tuple[int, str]:
            return (configured.index(key), "") if key in configured else (len(configured), key)

        return sorted(keys, key=rank)

    def check_target(self, sensor: str, target: float) -> str:
        """The command that sets `sensor` to `target` (0: off). Raises
        TargetError if the sensor has no heater or the target is out of range."""
        command = target_command(sensor, target)
        config = self.sensor(sensor)
        if target != 0 and not config.min <= target <= config.max:
            raise TargetError(
                f"{config.name} accepts targets from {config.min:g} to {config.max:g} °C (or 0 for off)"
            )
        return command

    @property
    def latest(self) -> dict[str, Reading]:
        return dict(self._latest)

    @property
    def last_report(self) -> float | None:
        """When the last report came, if one came since connecting."""
        return self._last_report

    # --- state --------------------------------------------------------------------

    def heater_status(self, reading: Reading) -> HeaterStatus:
        if not reading.target:
            return "off"
        if abs(reading.actual - reading.target) <= self.config.target_band_c:
            return "at_target"
        return "heating" if reading.actual < reading.target else "cooling"

    def state(self) -> TemperatureState:
        if not self._is_connected():
            return "disconnected"
        if self._latest:
            return "ok"
        connected_at = self._connected_at
        if connected_at is not None and self._clock() - connected_at >= self.config.report_timeout_s:
            return "no_sensors"
        return "waiting"

    def status(self) -> TemperatureStatus:
        state = self.state()
        latest = self._latest if state == "ok" else {}
        sensors = []
        for key in self._order(latest):
            reading = latest[key]
            sensors.append(
                SensorReading(
                    **self.info(key).model_dump(),
                    actual=reading.actual,
                    target=reading.target,
                    status=self.heater_status(reading),
                    timestamp=reading.timestamp,
                )
            )
        return TemperatureStatus(state=state, sensors=sensors)

    def _publish_status(self) -> None:
        status = self.status()
        self._published_state = status.state
        if self._publish is not None:
            self._publish(TemperatureStatusEvent(**status.model_dump()).dump())

    # --- waiting for temperatures ---------------------------------------------------

    def wait_status(self) -> list[TemperatureWaitSensor]:
        """Every sensor with a target, and how long it has held it."""
        result = []
        for key in self._order(self._latest):
            reading = self._latest[key]
            if not reading.target:
                continue
            settled = self._settled.get(key)
            stable_s = reading.timestamp - settled[1] if settled and settled[0] == reading.target else 0.0
            result.append(
                TemperatureWaitSensor(
                    sensor=key,
                    name=self.sensor(key).name,
                    actual=reading.actual,
                    target=reading.target,
                    stable_s=stable_s,
                )
            )
        return result

    def targets_reached(self) -> bool:
        """Every sensor with a target has stayed within target_band_c of it
        for settle_s (true when no sensor has a target)."""
        return all(sensor.stable_s >= self.config.settle_s for sensor in self.wait_status())

    # --- readings -----------------------------------------------------------------

    def on_event(self, event: dict[str, Any]) -> None:
        """Follows temperature reports and the connection through the app's event bus."""
        if event["type"] == "temperature":
            self.record(event["temperatures"])
        elif event["type"] == "printer":
            # Readings from before don't describe the printer now
            self._latest.clear()
            self._settled.clear()
            self._deviating.clear()
            self._warned.clear()
            self._last_report = None
            self._connected_at = self._clock() if event["connected"] else None
            self.flush()
            self._publish_status()
            if event["connected"]:
                self._start_watchdog()
            else:
                self._stop_watchdog()

    def record(self, temperatures: Mapping[str, Mapping[str, float | None]]) -> None:
        """Take one temperature report: {"T0": {"actual": 21.3, "target": 0.0}, ...}."""
        now = self._clock()
        session_id = self._session_id()
        tools = any(_TOOL.match(key) for key in temperatures)
        self._last_report = now
        self._warned.discard(("report", ""))
        for key, value in temperatures.items():
            if key == "T" and tools:
                # With several tools, Marlin's "T:" repeats the active one's T<n>
                continue
            actual = value["actual"]
            if actual is None:
                continue
            reading = Reading(now, actual, value.get("target"))
            self._latest[key] = reading
            history = self._history.setdefault(key, deque())
            history.append(reading)
            while history[0].timestamp < now - HISTORY_S:
                history.popleft()
            self._pending.append((now, key, reading.actual, reading.target, session_id))
            self._track(key, reading)
        if now - self._last_flush >= FLUSH_INTERVAL_S:
            self.flush()
        self._publish_status()

    def _track(self, key: str, reading: Reading) -> None:
        if PLAUSIBLE_MIN_C <= reading.actual <= PLAUSIBLE_MAX_C:
            self._warned.discard(("implausible", key))
        elif ("implausible", key) not in self._warned:
            self._warned.add(("implausible", key))
            self._warn(
                f"{self.sensor(key).name} reads {reading.actual:g} °C, which isn't plausible. "
                "Check that the sensor is connected."
            )

        target = reading.target
        if target and abs(reading.actual - target) <= self.config.target_band_c:
            settled = self._settled.get(key)
            if settled is None or settled[0] != target:
                self._settled[key] = (target, reading.timestamp)
        else:
            self._settled.pop(key, None)

    def history(self, seconds: float) -> dict[str, list[Reading]]:
        """Each sensor's readings from the last `seconds` (up to HISTORY_S), oldest first."""
        since = self._clock() - seconds
        result = {}
        for key, readings in self._history.items():
            recent = [r for r in readings if r.timestamp >= since]
            if recent:
                result[key] = recent
        return result

    def series(self, readings: Mapping[str, list[Reading]]) -> list[TemperatureSeries]:
        """Per-sensor readings as chart series."""
        return [
            TemperatureSeries(
                **self.info(key).model_dump(),
                timestamps=[r.timestamp for r in readings[key]],
                actual=[r.actual for r in readings[key]],
                target=[r.target for r in readings[key]],
            )
            for key in self._order(readings)
        ]

    # --- warnings -----------------------------------------------------------------

    def _warn(self, message: str) -> None:
        logger.warning(message)
        if self._publish is not None:
            self._publish(WarningEvent(message=message).dump())

    def check(self) -> None:
        """The watchdog's periodic check: warnings, a state that changed
        without a report (no sensors), and readings due to be written."""
        now = self._clock()
        if self._is_connected():
            self._check_reports(now)
            self._check_deviation(now)
        if self.state() != self._published_state:
            self._publish_status()
        if self._pending and now - self._last_flush >= FLUSH_INTERVAL_S:
            self.flush()

    def _check_reports(self, now: float) -> None:
        """Reports stopped coming. (A printer that never sent one has no
        sensors, which GET /temperature says instead.)"""
        timeout = self.config.report_timeout_s
        last = self._last_report
        if last is None or now - last < timeout or ("report", "") in self._warned:
            return
        self._warned.add(("report", ""))
        self._warn(f"No temperature report from the printer for {timeout:g} s.")

    def _check_deviation(self, now: float) -> None:
        printing = self._is_printing()
        for key, reading in self._latest.items():
            target = reading.target
            if not printing or not target or abs(reading.actual - target) <= self.config.deviation_c:
                self._deviating.pop(key, None)
                self._warned.discard(("deviation", key))
                continue
            deviating = self._deviating.get(key)
            if deviating is None or deviating[0] != target:
                # A new target starts a new grace period
                self._deviating[key] = (target, now)
                self._warned.discard(("deviation", key))
                continue
            if now - deviating[1] >= self.config.deviation_s and ("deviation", key) not in self._warned:
                self._warned.add(("deviation", key))
                self._warn(
                    f"{self.sensor(key).name} is at {reading.actual:.1f} °C, more than "
                    f"{self.config.deviation_c:g} °C from its {target:g} °C target "
                    f"for over {self.config.deviation_s:g} s."
                )

    def _start_watchdog(self) -> None:
        self._stop_watchdog()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop (some tests): check() is called by hand
        self._watchdog = loop.create_task(self._watch())

    def _stop_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(WATCH_INTERVAL_S)
            try:
                self.check()
            except Exception:
                logger.exception("Temperature check failed")

    async def close(self) -> None:
        """Stop the watchdog and write what's pending (at shutdown)."""
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None:
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog
        self.flush()

    # --- database -----------------------------------------------------------------

    def flush(self) -> None:
        """Write the readings taken since the last flush."""
        self._last_flush = self._clock()
        rows, self._pending = self._pending, []
        if not rows:
            return
        try:
            insert_temperature_readings(self._conn, rows)
        except sqlite3.Error:
            logger.exception("Couldn't log %d temperature readings", len(rows))

    def logged(
        self, *, session_id: int | None = None, start: float | None = None, end: float | None = None
    ) -> dict[str, list[Reading]]:
        """Logged readings of a print session or a time range, per sensor."""
        self.flush()
        result: dict[str, list[Reading]] = {}
        for timestamp, sensor, actual, target, _ in temperature_readings(
            self._conn, session_id=session_id, start=start, end=end
        ):
            result.setdefault(sensor, []).append(Reading(timestamp, actual, target))
        return result

    def csv(self, readings: Mapping[str, list[Reading]]) -> str:
        """Readings as CSV, in time order: timestamp (ISO 8601, UTC), sensor,
        name, actual, target (empty when the sensor has none)."""
        rows = sorted(
            ((r, key) for key, sensor_readings in readings.items() for r in sensor_readings),
            key=lambda item: item[0].timestamp,
        )
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["timestamp", "sensor", "name", "actual", "target"])
        for reading, key in rows:
            target = "" if reading.target is None else f"{reading.target:g}"
            name = self.sensor(key).name
            writer.writerow([_iso(reading.timestamp), key, name, f"{reading.actual:g}", target])
        return out.getvalue()

    def purge_expired(self) -> int:
        """Delete readings older than the retention period; returns how many."""
        cutoff = self._clock() - self.config.retention_days * 24 * 60 * 60
        deleted = delete_temperature_readings_before(self._conn, cutoff)
        if deleted:
            logger.info(
                "Deleted %d temperature readings older than %g days", deleted, self.config.retention_days
            )
        return deleted
