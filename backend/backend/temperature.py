"""Temperatures: the latest reading and the last two hours per sensor, and a
log of every reading in the database.

TemperatureStore follows the TemperatureEvents the serial manager publishes
on the event bus. Readings are written to the database in batches, at most
FLUSH_INTERVAL_S apart, each tagged with the print session running at the
time (if any).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from backend.config import SensorConfig, TemperatureConfig
from backend.database import (
    TemperatureRow,
    delete_temperature_readings_before,
    insert_temperature_readings,
)

logger = logging.getLogger(__name__)

# How far back the in-memory history (the live chart) goes
HISTORY_S = 2 * 60 * 60
# Readings are written to the database at most this far apart
FLUSH_INTERVAL_S = 10.0

_TOOL = re.compile(r"^T\d+$")


@dataclass(frozen=True)
class Reading:
    timestamp: float  # Unix seconds
    actual: float
    target: float | None


class TemperatureStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        config: TemperatureConfig,
        *,
        session_id: Callable[[], int | None] = lambda: None,
        clock: Callable[[], float] = time.time,
    ):
        self._conn = conn
        self.config = config
        # The print session readings belong to right now, if any
        self._session_id = session_id
        self._clock = clock
        self._latest: dict[str, Reading] = {}
        self._history: dict[str, deque[Reading]] = {}
        # Readings not yet written to the database
        self._pending: list[TemperatureRow] = []
        self._last_flush = clock()

    # --- sensors ------------------------------------------------------------------

    def sensor(self, key: str) -> SensorConfig:
        """The configured name and range of sensor `key`, or its raw key with
        the default range."""
        return self.config.sensors.get(key) or SensorConfig(name=key)

    @property
    def latest(self) -> dict[str, Reading]:
        return dict(self._latest)

    # --- readings -----------------------------------------------------------------

    def on_event(self, event: dict[str, Any]) -> None:
        """Follows temperature reports through the app's event bus."""
        if event["type"] == "temperature":
            self.record(event["temperatures"])

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

    def history(self, seconds: float) -> dict[str, list[Reading]]:
        """Each sensor's readings from the last `seconds` (up to HISTORY_S), oldest first."""
        since = self._clock() - seconds
        result = {}
        for key, readings in self._history.items():
            recent = [r for r in readings if r.timestamp >= since]
            if recent:
                result[key] = recent
        return result

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

    def purge_expired(self) -> int:
        """Delete readings older than the retention period; returns how many."""
        cutoff = self._clock() - self.config.retention_days * 24 * 60 * 60
        deleted = delete_temperature_readings_before(self._conn, cutoff)
        if deleted:
            logger.info(
                "Deleted %d temperature readings older than %g days", deleted, self.config.retention_days
            )
        return deleted
