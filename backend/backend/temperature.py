"""Temperatures: the latest reading and the last two hours per sensor, and a
log of every reading in the database.

TemperatureStore follows the TemperatureEvents the serial manager publishes
on the event bus. Readings are written to the database in batches, at most
FLUSH_INTERVAL_S apart, each tagged with the print session running at the
time (if any). After every report, and whenever the state changes, the
store publishes a TemperatureStatusEvent with what GET /temperature returns.
"""

from __future__ import annotations

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
)

logger = logging.getLogger(__name__)

# How far back the in-memory history (the live chart) goes
HISTORY_S = 2 * 60 * 60
# Readings are written to the database at most this far apart
FLUSH_INTERVAL_S = 10.0

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
        publish: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._conn = conn
        self.config = config
        # The print session readings belong to right now, if any
        self._session_id = session_id
        self._is_connected = is_connected
        self._publish = publish
        self._clock = clock
        self._latest: dict[str, Reading] = {}
        self._history: dict[str, deque[Reading]] = {}
        # Readings not yet written to the database
        self._pending: list[TemperatureRow] = []
        self._last_flush = clock()
        # When the printer was last connected; None while it isn't
        self._connected_at: float | None = None

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
        if self._publish is not None:
            self._publish(TemperatureStatusEvent(**self.status().model_dump()).dump())

    # --- readings -----------------------------------------------------------------

    def on_event(self, event: dict[str, Any]) -> None:
        """Follows temperature reports and the connection through the app's event bus."""
        if event["type"] == "temperature":
            self.record(event["temperatures"])
        elif event["type"] == "printer":
            # Readings from before don't describe the printer now
            self._latest.clear()
            self._connected_at = self._clock() if event["connected"] else None
            self.flush()
            self._publish_status()

    def record(self, temperatures: Mapping[str, Mapping[str, float | None]]) -> None:
        """Take one temperature report: {"T0": {"actual": 21.3, "target": 0.0}, ...}."""
        now = self._clock()
        session_id = self._session_id()
        tools = any(_TOOL.match(key) for key in temperatures)
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
        if now - self._last_flush >= FLUSH_INTERVAL_S:
            self.flush()
        self._publish_status()

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
