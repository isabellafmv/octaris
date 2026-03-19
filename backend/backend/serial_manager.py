from __future__ import annotations

import asyncio
import logging
import platform
import sys
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Callable

import serial
import serial.tools.list_ports

logger = logging.getLogger(__name__)

RECONNECT_ATTEMPTS = 3
RECONNECT_DELAY_S = 1.0
SERIAL_TIMEOUT_S = 5.0
SERIAL_LOG_MAX_ENTRIES = 500


@dataclass
class SerialLogEntry:
    timestamp: str
    direction: str  # "sent" | "received"
    content: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class SerialError(Exception):
    pass


class SerialManager:
    def __init__(
        self,
        on_disconnect: Callable[[], None] | None = None,
        on_serial_log: Callable[[dict[str, Any]], None] | None = None,
    ):
        self._serial: serial.Serial | None = None
        self._port: str | None = None
        self._baud_rate: int = 250000
        self._lock = asyncio.Lock()
        self._on_disconnect = on_disconnect
        self._on_serial_log = on_serial_log
        self._log_buffer: deque[SerialLogEntry] = deque(maxlen=SERIAL_LOG_MAX_ENTRIES)

    @property
    def log_buffer(self) -> list[dict[str, str]]:
        """Return the serial log buffer as a list of dicts (oldest first)."""
        return [entry.to_dict() for entry in self._log_buffer]

    def _log(self, direction: str, content: str) -> None:
        """Record a serial log entry and emit it via the event callback."""
        entry = SerialLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            direction=direction,
            content=content,
        )
        self._log_buffer.append(entry)
        if self._on_serial_log:
            self._on_serial_log({"type": "serial_log", "entry": entry.to_dict()})

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def port(self) -> str | None:
        return self._port

    @staticmethod
    def list_ports() -> list[dict[str, str]]:
        ports = serial.tools.list_ports.comports()
        system = platform.system()

        result = []
        for p in ports:
            result.append({"device": p.device, "description": p.description})

        if system == "Darwin":
            preferred = [r for r in result if "tty.usbmodem" in r["device"] or "tty.usbserial" in r["device"]]
            other = [r for r in result if r not in preferred]
            result = preferred + other
        elif system == "Linux":
            preferred = [r for r in result if "ttyUSB" in r["device"] or "ttyACM" in r["device"]]
            other = [r for r in result if r not in preferred]
            result = preferred + other

        return result

    async def connect(self, port: str, baud_rate: int) -> None:
        async with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()

            try:
                ser = await asyncio.to_thread(
                    serial.Serial,
                    port=port,
                    baudrate=baud_rate,
                    timeout=2,
                )
                self._serial = ser
                self._port = port
                self._baud_rate = baud_rate
                logger.info("Connected to %s at %d baud", port, baud_rate)
            except (serial.SerialException, OSError, Exception) as exc:
                raise SerialError(f"Could not connect to {port}: {exc}") from exc

    async def disconnect(self) -> None:
        async with self._lock:
            if self._serial and self._serial.is_open:
                await asyncio.to_thread(self._serial.close)
                logger.info("Disconnected from %s", self._port)
            self._serial = None
            self._port = None

    async def send_line(self, line: str) -> str:
        if not self.is_connected:
            raise SerialError("Not connected")

        try:
            response = await asyncio.to_thread(self._send_and_receive, line)
            return response
        except (serial.SerialException, OSError) as exc:
            logger.error("Serial error sending line: %s", exc)
            await self._handle_disconnect()
            raise SerialError(f"Send failed: {exc}") from exc

    def _send_and_receive(self, line: str) -> str:
        assert self._serial is not None
        stripped = line.strip()
        self._serial.write((stripped + "\n").encode())
        self._serial.flush()
        self._log("sent", stripped)

        response_lines: list[str] = []
        while True:
            raw = self._serial.readline()
            if not raw:
                break
            decoded = raw.decode("utf-8", errors="replace").strip()
            response_lines.append(decoded)
            if decoded.lower().startswith("ok") or decoded.lower().startswith("error"):
                break

        response = "\n".join(response_lines) if response_lines else "ok"
        self._log("received", response)
        return response

    async def _handle_disconnect(self) -> None:
        self._serial = None
        self._port_at_disconnect = self._port

        if self._on_disconnect:
            self._on_disconnect()

    async def try_reconnect(self, port: str, baud_rate: int) -> bool:
        for attempt in range(1, RECONNECT_ATTEMPTS + 1):
            logger.info("Reconnect attempt %d/%d to %s", attempt, RECONNECT_ATTEMPTS, port)
            try:
                await self.connect(port, baud_rate)
                return True
            except SerialError:
                if attempt < RECONNECT_ATTEMPTS:
                    await asyncio.sleep(RECONNECT_DELAY_S)
        return False
