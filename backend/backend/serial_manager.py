from __future__ import annotations

import asyncio
import logging
import platform
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Callable

import serial
import serial.tools.list_ports

from backend.checkpoint import parse_m114
from backend.gcode_processor import AXES, MachineState, step

logger = logging.getLogger(__name__)

RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY_S = 2.0
# pyserial readline() timeout. Short, so the reply loop can check its deadline.
SERIAL_TIMEOUT_S = 1.0
# Overall time to wait for "ok"/"error" after sending a command.
REPLY_DEADLINE_S = 60.0
# Commands that can legitimately block for minutes (homing, dwell, drain the
# move buffer, wait for temperature).
SLOW_COMMANDS = frozenset({"G28", "G4", "M400", "M109", "M190"})
SLOW_REPLY_DEADLINE_S = 300.0
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


class SerialTimeout(SerialError):
    """The printer did not answer a command with "ok" or "error" in time."""

    def __init__(self, command: str, timeout_s: float):
        self.command = command
        self.timeout_s = timeout_s
        super().__init__(f"No reply from printer to '{command}' within {timeout_s:g} s")


def reply_deadline_s(line: str) -> float:
    """How long to wait for the reply to `line`, based on its command word."""
    command = line.split(";", 1)[0].split(maxsplit=1)
    if command and command[0].upper() in SLOW_COMMANDS:
        return SLOW_REPLY_DEADLINE_S
    return REPLY_DEADLINE_S


def _unknown_position(relative: bool = False) -> MachineState:
    return MachineState(pos={ax: None for ax in AXES}, relative=relative)


class SerialManager:
    def __init__(
        self,
        on_disconnect: Callable[[], None] | None = None,
        on_serial_log: Callable[[dict[str, Any]], None] | None = None,
        on_connect: Callable[[str], None] | None = None,
        can_reconnect: Callable[[], bool] | None = None,
    ):
        self._serial: serial.Serial | None = None
        self._port: str | None = None
        self._baud_rate: int = 115200
        self._lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()
        # Guards only serial.write(), so emergency_write() can interleave with a
        # send that is blocked waiting for "ok" while holding _io_lock.
        self._write_lock = threading.Lock()
        self._on_disconnect = on_disconnect
        self._on_serial_log = on_serial_log
        # Fired with the port after a successful *automatic* reconnect. A
        # manual /connect is reported by the caller, not from here.
        self._on_connect = on_connect
        # Whether an automatic reconnect is allowed right now. Reopening the
        # port can reset the board, losing its position and G92 zero, so the
        # app only allows it while no print is active.
        self._can_reconnect = can_reconnect or (lambda: True)
        # Where the printer was last commanded to go, from every line written
        # (G-code semantics via gcode_processor.step) and every M114 reply.
        # Axes are None while unknown.
        self._state = _unknown_position()
        self._log_buffer: deque[SerialLogEntry] = deque(maxlen=SERIAL_LOG_MAX_ENTRIES)
        # Set after a SerialTimeout: the late reply may still arrive, and must
        # not be read as the reply to the next command.
        self._discard_stale_input = False

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

    @property
    def position(self) -> dict[str, float | None]:
        """Tracked logical position per axis; None where unknown."""
        return dict(self._state.pos)

    @staticmethod
    def list_ports() -> list[dict[str, str]]:
        ports = serial.tools.list_ports.comports()
        system = platform.system()

        if system == "Darwin":
            return [
                {"device": p.device, "description": p.description}
                for p in ports
                if "cu.usbmodem" in p.device or "cu.usbserial" in p.device
            ]
        if system == "Linux":
            return [
                {"device": p.device, "description": p.description}
                for p in ports
                if "ttyUSB" in p.device or "ttyACM" in p.device
            ]
        return [{"device": p.device, "description": p.description} for p in ports]

    async def connect(self, port: str, baud_rate: int) -> None:
        async with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()

            try:
                ser = await asyncio.to_thread(_open_port, port, baud_rate)
                self._serial = ser
                self._state = _unknown_position()
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
            self._state = _unknown_position()

    async def send_line(self, line: str) -> str:
        async with self._io_lock:
            return await self._send_unlocked(line)

    async def send_lines(self, lines: list[str]) -> list[str]:
        """Send several lines as one atomic exchange, holding the I/O lock once."""
        async with self._io_lock:
            return [await self._send_unlocked(line) for line in lines]

    async def emergency_write(self, line: str) -> None:
        """Write a line immediately, bypassing _io_lock and not waiting for "ok".

        Meant for commands like M410 that Marlin's EMERGENCY_PARSER handles as
        soon as they arrive, even while another command is in flight.
        """
        if not self.is_connected:
            raise SerialError("Not connected")
        # A quick stop (M410) halts mid-move: the position is unknown until
        # the next M114.
        self._state = _unknown_position(self._state.relative)
        try:
            await asyncio.to_thread(self._write_line, line.strip())
        except (serial.SerialException, OSError) as exc:
            logger.error("Serial error on emergency write: %s", exc)
            await self._handle_disconnect()
            raise SerialError(f"Emergency write failed: {exc}") from exc

    def _write_line(self, stripped: str) -> None:
        ser = self._serial
        assert ser is not None
        with self._write_lock:
            ser.write((stripped + "\n").encode())
        ser.flush()
        self._log("sent", stripped)

    async def _send_unlocked(self, line: str) -> str:
        """Write-then-read-until-ok for a single line. Caller must hold _io_lock.

        A serial error closes the connection and raises. If `can_reconnect`
        allows it (never during a print), the port is reopened first, but the
        line is never re-sent: it may already have reached the printer, and
        the board may have reset and lost its zero.
        """
        if not self.is_connected:
            if not (self._can_reconnect() and await self.reconnect()):
                raise SerialError("Not connected")

        try:
            response = await asyncio.to_thread(self._send_and_receive, line)
            return response
        except (serial.SerialException, OSError) as exc:
            logger.error("Serial error sending line: %s", exc)
            # Decided before on_disconnect, which stops a running print.
            may_reconnect = self._can_reconnect()
            await self._handle_disconnect()
            if may_reconnect:
                logger.info("Attempting reconnect after serial error…")
                if await self.reconnect():
                    raise SerialError(
                        f"Connection lost and re-established ({exc}); "
                        f"'{line.strip()}' was not re-sent"
                    ) from exc
            raise SerialError(f"Connection lost: {exc}") from exc

    def _send_and_receive(self, line: str) -> str:
        assert self._serial is not None
        stripped = line.strip()
        timeout_s = reply_deadline_s(stripped)
        if self._discard_stale_input:
            self._serial.reset_input_buffer()
            self._discard_stale_input = False
        self._write_line(stripped)
        self._state = step(self._state, stripped)

        response_lines: list[str] = []
        deadline = time.monotonic() + timeout_s
        while True:
            raw = self._serial.readline()
            if not raw:
                if time.monotonic() >= deadline:
                    if response_lines:
                        self._log("received", "\n".join(response_lines))
                    self._discard_stale_input = True
                    # Unknown whether (or when) the printer acts on it
                    self._state = _unknown_position(self._state.relative)
                    raise SerialTimeout(stripped, timeout_s)
                continue
            decoded = raw.decode("utf-8", errors="replace").strip()
            lowered = decoded.lower()
            if lowered.startswith("echo:busy"):
                # Marlin is still working on the command; keep waiting.
                self._log("received", decoded)
                deadline = time.monotonic() + timeout_s
                continue
            if not decoded:
                continue
            response_lines.append(decoded)
            if lowered.startswith("ok") or lowered.startswith("error"):
                break

        response = "\n".join(response_lines)
        self._log("received", response)
        if stripped.split(";", 1)[0].split()[:1] == ["M114"]:
            reported = parse_m114(response)
            if reported is not None:
                pos = {ax: reported.get(ax, self._state.pos[ax]) for ax in AXES}
                self._state = MachineState(pos, self._state.relative, self._state.feed)
        return response

    async def _handle_disconnect(self) -> None:
        self._serial = None
        self._state = _unknown_position()
        if self._on_disconnect:
            self._on_disconnect()

    async def reconnect(self) -> bool:
        """Reconnect using the last-known port and baud rate."""
        if not self._port or not self._can_reconnect():
            return False
        return await self.try_reconnect(self._port, self._baud_rate)

    async def try_reconnect(self, port: str, baud_rate: int) -> bool:
        for attempt in range(1, RECONNECT_ATTEMPTS + 1):
            logger.info("Reconnect attempt %d/%d to %s", attempt, RECONNECT_ATTEMPTS, port)
            try:
                await self.connect(port, baud_rate)
                if self._on_connect:
                    self._on_connect(port)
                return True
            except SerialError:
                if attempt < RECONNECT_ATTEMPTS:
                    await asyncio.sleep(RECONNECT_DELAY_S)
        return False


def _open_port(port: str, baud_rate: int) -> serial.Serial:
    """Open `port` without asserting DTR or RTS, where the OS allows it.

    Many printer boards (Arduino-style auto-reset: FTDI/CH340 USB-serial, or
    the ATmega16U2 on a Mega2560) reset when DTR goes active, and pyserial
    asserts DTR and RTS on open by default. A reset loses the position and
    the G92 zero, which on this printer (no homing) means the next print
    starts from the wrong place. Setting dtr/rts on the unopened Serial makes
    pyserial apply them as part of open().

    This is best effort: the OS driver may still pulse DTR while opening
    (Linux does, via HUPCL) before pyserial gets to clear it. Calibration is
    therefore reset after every (re)connect regardless. Boards with native
    USB (STM32, SAMD, LPC) don't reset on DTR at all; if one of those stops
    answering, check whether its firmware waits for DTR before sending.
    """
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud_rate
    ser.timeout = SERIAL_TIMEOUT_S
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser
