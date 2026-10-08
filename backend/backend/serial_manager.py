"""Serial link to the printer.

One reader thread per connection owns the pyserial port: it reads every line
the printer sends and sorts it (reply, resend request, busy, temperature, log).
Commands are sent one at a time — a line goes out, and the next may only follow
once the reader has seen its "ok" (or its deadline has passed). Print lines
carry line numbers and checksums, so the printer can ask for a corrupted line
to be sent again.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import serial
import serial.tools.list_ports

from backend.checkpoint import parse_m114
from backend.gcode_processor import AXES, MachineState, step
from backend.schemas import Event, PrinterEvent, SerialLogEvent, TemperatureEvent

logger = logging.getLogger(__name__)

RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY_S = 2.0
# pyserial readline() timeout. Short, so the reader can check deadlines and
# notice a stop request while the printer is silent.
SERIAL_TIMEOUT_S = 1.0
# Overall time to wait for "ok"/"error" after sending a command.
REPLY_DEADLINE_S = 60.0
# Commands that can legitimately block for minutes (homing, dwell, drain the
# move buffer, wait for temperature).
SLOW_COMMANDS = frozenset({"G28", "G4", "M400", "M109", "M190"})
SLOW_REPLY_DEADLINE_S = 300.0
SERIAL_LOG_MAX_ENTRIES = 500
# Numbered lines kept for answering resend requests.
RESEND_HISTORY = 100
# Temperature auto-report interval requested with M155, and how long to wait
# for the first report before falling back to polling with M105.
AUTOREPORT_INTERVAL_S = 2
AUTOREPORT_WAIT_S = 5.0
TEMPERATURE_POLL_S = 2.0

_RESEND = re.compile(r"^(?:resend:?|rs)\s*N?:?\s*(\d+)", re.IGNORECASE)
# A temperature report starts with a sensor key, optionally after "ok":
# "T:21.30 /0.00 B:20.10 /0.00 @:0 B@:0". M114's "X:... B:... C:..." does not.
_TEMPERATURE_REPORT = re.compile(r"^(?:ok\s+)?(?:T\d*|B|C|P|R|W):\s*-?\d")
_TEMPERATURE = re.compile(r"\b(T\d*|B|C|P|R|W):\s*(-?\d+(?:\.\d+)?)(?:\s*/\s*(-?\d+(?:\.\d+)?))?")
_M110 = re.compile(r"^M110\b.*?\bN(\d+)", re.IGNORECASE)
# Errors Marlin sends just before a "Resend:" request — not a reply by themselves.
_RESEND_ERRORS = ("checksum", "line number", "last line")


@dataclass
class SerialLogEntry:
    timestamp: str
    direction: str  # "sent" | "received"
    content: str
    line_number: int | None = None  # the N of a numbered line

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SerialError(Exception):
    pass


class SerialTimeout(SerialError):
    """The printer did not answer a command with "ok" or "error" in time."""

    def __init__(self, command: str, timeout_s: float):
        self.command = command
        self.timeout_s = timeout_s
        super().__init__(f"No reply from printer to '{command}' within {timeout_s:g} s")


class ResendUnavailable(SerialError):
    """The printer asked for a line that is no longer in the resend history."""


class _PortLost(SerialError):
    """Reading from or writing to the port failed; worth a reconnect."""


def reply_deadline_s(line: str) -> float:
    """How long to wait for the reply to `line`, based on its command word."""
    command = line.split(";", 1)[0].split(maxsplit=1)
    if command and command[0].upper() in SLOW_COMMANDS:
        return SLOW_REPLY_DEADLINE_S
    return REPLY_DEADLINE_S


def checksum(body: str) -> int:
    """Marlin's line checksum: XOR of every byte before the '*'."""
    result = 0
    for byte in body.encode():
        result ^= byte
    return result


def number_line(number: int, command: str) -> str:
    body = f"N{number} {command}"
    return f"{body}*{checksum(body)}"


def parse_temperatures(line: str) -> dict[str, dict[str, float | None]] | None:
    """{"T": {"actual": 21.3, "target": 0.0}, ...} for a temperature report, else None."""
    if not _TEMPERATURE_REPORT.match(line):
        return None
    return {
        key: {"actual": float(actual), "target": float(target) if target is not None else None}
        for key, actual, target in _TEMPERATURE.findall(line)
    }


def _unknown_position(relative: bool = False) -> MachineState:
    return MachineState(pos={ax: None for ax in AXES}, relative=relative)


@dataclass
class _Pending:
    """The one command in flight. While set, nothing else may be sent."""

    command: str
    number: int | None
    timeout_s: float
    deadline: float
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future
    lines: list[str] = field(default_factory=list)
    wants_temperatures: bool = False
    resend_from: int | None = None
    # Numbered lines still to re-send before the command's own "ok" counts.
    resend_queue: deque[tuple[int, str]] = field(default_factory=deque)


def _settle(future: asyncio.Future, result: str | None, exc: BaseException | None) -> None:
    if future.done():
        return
    if exc is not None:
        future.set_exception(exc)
    else:
        future.set_result(result)


class SerialManager:
    def __init__(
        self,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        can_reconnect: Callable[[], bool] | None = None,
        on_traffic: Callable[[SerialLogEntry], None] | None = None,
    ):
        self._serial: serial.Serial | None = None
        self._port: str | None = None
        self._baud_rate: int = 115200
        # Serializes connect/disconnect, so two can't open the port at once.
        self._connect_lock = asyncio.Lock()
        # Queues senders; whoever holds it may put one command in flight at a
        # time. Holding it across several lines makes them one atomic exchange.
        self._send_lock = asyncio.Lock()
        # Guards only port.write(), so emergency_write() and resends from the
        # reader thread can't interleave bytes with a normal send.
        self._write_lock = threading.Lock()
        # Guards _pending and _history, shared with the reader thread.
        self._state_lock = threading.Lock()
        self._pending: _Pending | None = None
        self._history: deque[tuple[int, str]] = deque(maxlen=RESEND_HISTORY)
        # One entry per emergency line written whose own "ok" is still to
        # come: the command that was in flight when it went out (or None).
        # Marlin acks an M410 once its turn in the command queue comes, so
        # that "ok" follows the in-flight command's and belongs to neither.
        self._emergency_oks: deque[_Pending | None] = deque()
        self._next_line_number = 1
        # Set when the printer's idea of the last line number may differ from
        # ours (fresh port, or a numbered line that failed); the next numbered
        # line is preceded by an M110 to agree on it.
        self._resync = True
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader_stop: threading.Event | None = None
        self._monitor: asyncio.Task | None = None
        self._autoreport_seen: asyncio.Event | None = None
        # Receives, always on the event loop: "serial_log" and "temperature"
        # events, and "printer" events when the port is lost or reopened
        # *automatically*. A manual connect()/disconnect() is reported by the
        # caller, not from here.
        self._on_event = on_event
        # Whether an automatic reconnect is allowed right now. Reopening the
        # port can reset the board, losing its position and G92 zero, so the
        # app only allows it while no print is active.
        self._can_reconnect = can_reconnect or (lambda: True)
        # Where the printer was last commanded to go, from every line written
        # (G-code semantics via gcode_processor.step) and every M114 reply.
        # Axes are None while unknown.
        self._state = _unknown_position()
        self._log_buffer: deque[SerialLogEntry] = deque(maxlen=SERIAL_LOG_MAX_ENTRIES)
        # Receives every serial log entry as it is made, from whichever thread
        # made it, in order (unlike on_event, which runs later on the loop).
        self._on_traffic = on_traffic

    @property
    def log_buffer(self) -> list[dict[str, Any]]:
        """Return the serial log buffer as a list of dicts (oldest first)."""
        return [entry.to_dict() for entry in self._log_buffer]

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

    @property
    def relative(self) -> bool:
        """The printer is in relative positioning (G91), as last sent.
        False on a fresh connection: Marlin starts in G90."""
        return self._state.relative

    @staticmethod
    def list_ports() -> list[dict[str, str]]:
        return _virtual_ports() + _hardware_ports()

    # --- events ---------------------------------------------------------------

    def _emit(self, event: Event) -> None:
        """Hand an event to on_event on the event loop, from any thread."""
        if self._on_event is None or self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._on_event, event.dump())
        except RuntimeError:
            pass  # the loop is closed; nobody is listening any more

    def _log(self, direction: str, content: str, line_number: int | None = None) -> None:
        entry = SerialLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            direction=direction,
            content=content,
            line_number=line_number,
        )
        self._log_buffer.append(entry)
        if self._on_traffic is not None:
            self._on_traffic(entry)
        self._emit(SerialLogEvent.model_validate({"entry": entry.to_dict()}))

    # --- connection -------------------------------------------------------------

    async def connect(self, port: str, baud_rate: int) -> None:
        async with self._connect_lock:
            self._close(SerialError("Reconnected"))
            try:
                ser = await asyncio.to_thread(_open_port, port, baud_rate)
            except Exception as exc:
                raise SerialError(f"Could not connect to {port}: {exc}") from exc
            self._attach(ser, port, baud_rate)
            self._monitor = asyncio.create_task(self._monitor_temperature())
            logger.info("Connected to %s at %d baud", port, baud_rate)

    def _attach(self, ser: Any, port: str, baud_rate: int | None = None) -> None:
        """Take over an open port and start its reader thread."""
        self._stop_reader()
        self._loop = asyncio.get_running_loop()
        self._serial = ser
        self._port = port
        if baud_rate is not None:
            self._baud_rate = baud_rate
        self._resync = True
        self._state = _unknown_position()
        with self._state_lock:
            self._emergency_oks.clear()
        stop = threading.Event()
        self._reader_stop = stop
        threading.Thread(
            target=self._read_loop, args=(ser, stop), name=f"serial-reader {port}", daemon=True
        ).start()

    async def disconnect(self) -> None:
        async with self._connect_lock:
            was_open = self._serial is not None
            self._close(SerialError("Disconnected"))
            self._port = None
            if was_open:
                logger.info("Disconnected")

    def _stop_reader(self) -> None:
        if self._reader_stop is not None:
            self._reader_stop.set()
            self._reader_stop = None

    def _close(self, reason: SerialError) -> None:
        """Stop the reader and monitor, close the port, fail the command in flight."""
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        self._stop_reader()
        ser, self._serial = self._serial, None
        self._state = _unknown_position()
        if ser is not None:
            try:
                ser.close()
            except Exception:
                logger.debug("Closing the port failed", exc_info=True)
        with self._state_lock:
            pending, self._pending = self._pending, None
        if pending is not None:
            pending.loop.call_soon_threadsafe(_settle, pending.future, None, reason)

    def _handle_disconnect(self, ser: Any) -> None:
        """The port `ser` failed. No-op if it was already replaced or closed."""
        if ser is None or self._serial is not ser:
            return
        self._close(SerialError("The printer disconnected"))
        if self._on_event:
            self._on_event(PrinterEvent(connected=False, port=None).dump())

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
                if self._on_event:
                    self._on_event(PrinterEvent(connected=True, port=port).dump())
                return True
            except SerialError:
                if attempt < RECONNECT_ATTEMPTS:
                    await asyncio.sleep(RECONNECT_DELAY_S)
        return False

    # --- sending ------------------------------------------------------------------

    async def send(self, line: str, numbered: bool = False, *, log: bool = True) -> str:
        """Send one line and return the printer's reply once it's "ok"/"error".

        `numbered` lines go out as "N<n> <line>*<checksum>" (comment removed),
        so a corrupted one is re-sent when the printer asks for it. Send
        "M110 N0" first to start numbering from 1.
        """
        async with self._send_lock:
            return await self._exchange(line.strip(), numbered, log)

    async def send_lines(self, lines: list[str]) -> list[str]:
        """Send several unnumbered lines as one atomic exchange."""
        async with self._send_lock:
            return [await self._exchange(line.strip(), False, True) for line in lines]

    async def emergency_write(self, line: str) -> None:
        """Write a line immediately, without waiting for "ok" or for a command in flight.

        Meant for commands like M410 that Marlin's EMERGENCY_PARSER handles as
        soon as they arrive, even while another command is in flight.
        """
        ser = self._serial
        if not self.is_connected:
            raise SerialError("Not connected")
        stripped = line.strip()
        # A quick stop (M410) halts mid-move: the position is unknown until
        # the next M114.
        self._state = _unknown_position(self._state.relative)
        with self._state_lock:
            self._emergency_oks.append(self._pending)
        try:
            self._log("sent", stripped)
            await asyncio.to_thread(self._write, ser, stripped)
        except (serial.SerialException, OSError) as exc:
            logger.error("Serial error on emergency write: %s", exc)
            self._handle_disconnect(ser)
            raise SerialError(f"Emergency write failed: {exc}") from exc

    def _write(self, ser: Any, wire: str) -> None:
        with self._write_lock:
            ser.write((wire + "\n").encode())
            ser.flush()

    async def _exchange(self, line: str, numbered: bool, log: bool) -> str:
        """Send a line and wait for its reply. Caller must hold _send_lock.

        A serial error closes the connection and raises. If `can_reconnect`
        allows it (never during a print), the port is reopened first, but the
        line is never re-sent: it may already have reached the printer, and
        the board may have reset and lost its zero.
        """
        if not self.is_connected and not await self.reconnect():
            raise SerialError("Not connected")
        try:
            return await self._transact(line, numbered, log)
        except _PortLost as exc:
            logger.error("Serial error sending line: %s", exc)
            # Decided before the disconnect event, whose listeners stop a
            # running print.
            may_reconnect = self._can_reconnect()
            self._handle_disconnect(self._serial)
            if may_reconnect:
                logger.info("Attempting reconnect after serial error…")
                if await self.reconnect():
                    raise SerialError(
                        f"Connection lost and re-established ({exc}); '{line}' was not re-sent"
                    ) from exc
            raise SerialError(f"Connection lost: {exc}") from exc

    async def _transact(self, line: str, numbered: bool, log: bool) -> str:
        if numbered:
            if self._resync:
                await self._transact(f"M110 N{self._next_line_number - 1}", False, log)
            command = line.split(";", 1)[0].strip()
            if not command:
                raise ValueError(f"Nothing to send in {line!r}")
            number: int | None = self._next_line_number
            wire = number_line(self._next_line_number, command)
        else:
            command, number, wire = line, None, line
            m110 = _M110.match(command)
            if m110:
                self._next_line_number = int(m110.group(1)) + 1
                self._resync = False
                with self._state_lock:
                    self._history.clear()

        ser = self._serial
        loop = asyncio.get_running_loop()
        timeout_s = reply_deadline_s(command)
        words = command.split(maxsplit=1)
        pending = _Pending(
            command=command,
            number=number,
            timeout_s=timeout_s,
            deadline=time.monotonic() + timeout_s,
            loop=loop,
            future=loop.create_future(),
            wants_temperatures=bool(words) and words[0].upper() == "M105",
        )
        with self._state_lock:
            if number is not None:
                self._next_line_number = number + 1
                self._history.append((number, wire))
            self._pending = pending
        try:
            if log:
                self._log("sent", command, number)
            try:
                await asyncio.to_thread(self._write, ser, wire)
            except (serial.SerialException, OSError) as exc:
                raise _PortLost(str(exc)) from exc
            self._state = step(self._state, command)
            reply = await pending.future
        except BaseException as exc:
            if isinstance(exc, SerialTimeout):
                # Unknown whether (or when) the printer acts on it
                self._state = _unknown_position(self._state.relative)
            if number is not None:
                # The printer may or may not have taken this line. Re-use its
                # number for the retry, and agree on it with M110 first.
                with self._state_lock:
                    self._next_line_number = number
                    if self._history and self._history[-1][0] == number:
                        self._history.pop()
                self._resync = True
            raise
        finally:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
        if words and words[0].upper() == "M114":
            reported = parse_m114(reply)
            if reported is not None:
                pos = {ax: reported.get(ax, self._state.pos[ax]) for ax in AXES}
                self._state = MachineState(pos, self._state.relative, self._state.feed)
        return reply

    # --- reader thread -----------------------------------------------------------

    def _read_loop(self, ser: Any, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                raw = ser.readline()
            except Exception as exc:
                if stop.is_set():
                    break
                logger.error("Serial read failed: %s", exc)
                self._port_lost(ser, exc)
                break
            if raw:
                try:
                    self._handle_line(ser, raw.decode("utf-8", errors="replace").strip())
                except Exception:
                    logger.exception("Failed to handle serial line %r", raw)
            self._check_deadline()

    def _port_lost(self, ser: Any, exc: Exception) -> None:
        """Reader thread: the port failed. A sender in flight handles the
        reconnect; otherwise report the disconnect from the event loop."""
        with self._state_lock:
            pending, self._pending = self._pending, None
        if pending is not None:
            pending.loop.call_soon_threadsafe(_settle, pending.future, None, _PortLost(str(exc)))
        elif self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._handle_disconnect, ser)
            except RuntimeError:
                pass

    def _finish(self, pending: _Pending, result: str | None = None, exc: BaseException | None = None) -> None:
        with self._state_lock:
            if self._pending is not pending:
                return
            self._pending = None
        pending.loop.call_soon_threadsafe(_settle, pending.future, result, exc)

    def _check_deadline(self) -> None:
        pending = self._pending
        if pending is not None and time.monotonic() >= pending.deadline:
            self._finish(pending, exc=SerialTimeout(pending.command, pending.timeout_s))

    def _handle_line(self, ser: Any, line: str) -> None:
        if not line:
            return
        pending = self._pending
        lowered = line.lower()

        if lowered.startswith("echo:busy"):
            # The printer is still working on the command; keep waiting.
            self._log("received", line)
            if pending is not None:
                pending.deadline = time.monotonic() + pending.timeout_s
            return

        temperatures = parse_temperatures(line)
        if temperatures is not None:
            self._emit(TemperatureEvent.model_validate({"temperatures": temperatures}))
            autoreport_seen, loop = self._autoreport_seen, self._loop
            if (pending is None or not pending.wants_temperatures) and autoreport_seen and loop:
                loop.call_soon_threadsafe(autoreport_seen.set)

        resend = _RESEND.match(line)
        if resend:
            self._log("received", line)
            if pending is not None and pending.number is not None:
                pending.resend_from = int(resend.group(1))
            return

        if lowered.startswith("ok"):
            if temperatures is None:
                self._log("received", line)
            if self._is_emergency_ok(pending):
                return
            if pending is not None:
                self._on_ok(ser, pending, line)
            return

        if temperatures is not None:
            if pending is not None and pending.wants_temperatures:
                pending.lines.append(line)
            return

        self._log("received", line)
        if pending is None or line.startswith("//"):
            return  # unsolicited, or a host action message
        if lowered.startswith("error"):
            if any(marker in lowered for marker in _RESEND_ERRORS):
                return  # a "Resend:" follows
            pending.lines.append(line)
            self._finish(pending, "\n".join(pending.lines))
            return
        pending.lines.append(line)

    def _is_emergency_ok(self, pending: _Pending | None) -> bool:
        """Whether an "ok" is the ack of an emergency line, not a reply to
        `pending`: the command that was in flight when it went out has had
        its own "ok" already."""
        with self._state_lock:
            if self._emergency_oks and self._emergency_oks[0] is not pending:
                self._emergency_oks.popleft()
                return True
        return False

    def _on_ok(self, ser: Any, pending: _Pending, line: str) -> None:
        if pending.resend_from is not None:
            wanted, pending.resend_from = pending.resend_from, None
            if pending.number is not None and wanted == pending.number + 1:
                # The printer already has this line (e.g. it was sent twice).
                self._finish(pending, "\n".join([*pending.lines, line]))
                return
            with self._state_lock:
                lines = [(n, wire) for n, wire in self._history if n >= wanted]
            if not lines or lines[0][0] != wanted:
                self._finish(
                    pending,
                    exc=ResendUnavailable(
                        f"The printer asked to resend line {wanted}, which is no longer available"
                    ),
                )
                return
            pending.resend_queue = deque(lines)

        if pending.resend_queue:
            number, wire = pending.resend_queue.popleft()
            self._log("sent", wire.split(" ", 1)[1].rsplit("*", 1)[0], number)
            pending.deadline = time.monotonic() + pending.timeout_s
            try:
                self._write(ser, wire)
            except (serial.SerialException, OSError) as exc:
                self._finish(pending, exc=_PortLost(str(exc)))
            return

        self._finish(pending, "\n".join([*pending.lines, line]))

    # --- temperature ---------------------------------------------------------------

    async def _monitor_temperature(self) -> None:
        """Ask for temperature auto-reports; poll with M105 if they don't come."""
        self._autoreport_seen = asyncio.Event()
        try:
            await self.send(f"M155 S{AUTOREPORT_INTERVAL_S}")
            try:
                await asyncio.wait_for(self._autoreport_seen.wait(), AUTOREPORT_WAIT_S)
                return
            except asyncio.TimeoutError:
                logger.info("No temperature auto-report; polling with M105")
            while True:
                await asyncio.sleep(TEMPERATURE_POLL_S)
                # Waits its turn behind the command in flight, so it goes out
                # between commands.
                try:
                    await self.send("M105", log=False)
                except SerialTimeout:
                    logger.warning("M105 temperature poll timed out")
        except SerialError as exc:
            logger.info("Temperature monitoring stopped: %s", exc)


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
    from backend import virtual_printer

    if port == virtual_printer.VIRTUAL_PORT and virtual_printer.enabled():
        return virtual_printer.open_virtual()  # type: ignore[return-value]
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud_rate
    ser.timeout = SERIAL_TIMEOUT_S
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


def _virtual_ports() -> list[dict[str, str]]:
    """The virtual printer, in dev mode (OCTARIS_VIRTUAL_PRINTER=1)."""
    from backend import virtual_printer

    if not virtual_printer.enabled():
        return []
    return [
        {
            "device": virtual_printer.VIRTUAL_PORT,
            "description": virtual_printer.VIRTUAL_PORT_DESCRIPTION,
        }
    ]


def _hardware_ports() -> list[dict[str, str]]:
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
