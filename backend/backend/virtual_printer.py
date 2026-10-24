"""A virtual Marlin printer behind the pyserial interface.

Used by the tests, and by dev mode (OCTARIS_VIRTUAL_PRINTER=1), where /ports
lists it so the whole app can be used without hardware.

It runs its own firmware thread, like the real board: it checks line numbers
and checksums (asking for a resend like Marlin does), plans moves into a
buffer of `planner_depth` moves that take distance / feedrate to run, sends
"echo:busy: processing" while a command is blocked, reports temperatures
(M105, and automatically after M155), and answers M114 and M115 in Marlin's
format. Its heaters (hotends T or T0, T1..., bed B, chamber C) take targets
from M104/M109, M140/M190 and M141 and move towards them over time.

With protocol="printess" it speaks the lab firmware's heater protocol
instead: channels I0..I4, each Disabled until M104 I<n> T<°C> H (heat) or C
(cool) sets a target. A channel only moves the way it was told: H heats up
to the target and never cools, C cools down to it and never heats; its
status is Heating or Cooling accordingly. M104 I<n> D disables it again (it
drifts back to ambient, unseen). Its temperatures are reported as the lab
firmware's block, once unasked right after connecting and in answer to M105:

    Status:       [Cooling, Disabled, Disabled, Disabled, Disabled]
    Temperatures: [14.94, X, X, X, X]
    Targets:      [15.00, X, X, X, X]
    PWM:          [127, 0, 0, 0, 0]

X: no value, as for every disabled channel.

Its axes are X, Y and those of `axes` (default: the default printer
profile's). A move, G92 or M92 on another nozzle axis letter (see
printer.NOZZLE_AXIS_LETTERS) adds that axis at 0, as if the firmware had
been built with it: the M92 Octaris sends on connect and on a change of
printer profile names every axis the profile has, so the virtual printer
always has the active profile's axes, and M114 reports them all.

M410 is handled like Marlin's EMERGENCY_PARSER does: motion stops the moment
it arrives, the planned moves are dropped, and the position is wherever the
axes stopped. The line is then also queued like any other, so it gets its own
"ok" after the command that was in flight. With `emergency_parser=False` (as
the Printess build is), it acts only when its turn in the queue comes.

For tests, faults can be injected (a line corrupted or lost on the wire, any
unsolicited line), and motion can be held at a chosen point of a chosen move,
so an e-stop lands at a known position.
"""

from __future__ import annotations

import math
import os
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from backend.printer import NOZZLE_AXIS_LETTERS, default_axes
from backend.serial_manager import checksum

# The device name /ports lists for the virtual printer in dev mode
VIRTUAL_PORT = "virtual"
VIRTUAL_PORT_DESCRIPTION = "Virtual printer"

FIRMWARE = (
    "FIRMWARE_NAME:Marlin 2.1.2.5 (virtual) SOURCE_CODE_URL:github.com/MarlinFirmware/Marlin "
    "PROTOCOL_VERSION:1.0 MACHINE_TYPE:Octaris EXTRUDER_COUNT:0 AXIS_COUNT:{axis_count} "
    "UUID:00000000-0000-4000-8000-000000000000"
)
# Heaters move towards their target at this rate (°C per second)
HEATING_RATE = 2.0
# Ambient temperature per heater, which it returns to when off
DEFAULT_SENSORS = {"T": 21.3, "B": 20.1}
# Dev mode: two syringes, the bed and the chamber
DEV_SENSORS = {"T0": 21.3, "T1": 21.1, "B": 20.1, "C": 22.0}
# printess: its five channels (Octaris uses I0..I3)
PRINTESS_SENSORS = {"I0": 21.3, "I1": 21.1, "I2": 20.8, "I3": 21.6, "I4": 21.0}
# printess: a channel's PWM while it moves towards its target
PRINTESS_PWM = 127

_NUMBERED = re.compile(r"^N(\d+)\s+(.*)\*(\d+)$")

# A line to match: text it contains, or its line number N
Match = str | int


def enabled() -> bool:
    """Dev mode: offer the virtual printer as a port."""
    return os.environ.get("OCTARIS_VIRTUAL_PRINTER") == "1"


def open_virtual() -> VirtualPrinter:
    """A freshly powered-on virtual printer, for a connect in dev mode.

    OCTARIS_VIRTUAL_PRINTER_SPEED runs its moves that many times faster than
    real time (default 1). It speaks the heater protocol chosen in the app
    (or config.json's temperature.protocol) at the time it connects, unless
    DEV_OPTIONS says otherwise.
    """
    from backend.config import load_config
    from backend.settings import SettingsStore

    speed = float(os.environ.get("OCTARIS_VIRTUAL_PRINTER_SPEED", "1"))
    options: dict[str, Any] = {"speed": speed, "ok_delay_s": 0.002, **DEV_OPTIONS}
    chosen = SettingsStore().current.temperature_protocol
    protocol = options.setdefault("protocol", chosen or load_config().temperature.protocol)
    options.setdefault("sensors", PRINTESS_SENSORS if protocol == "printess" else DEV_SENSORS)
    return VirtualPrinter(**options)


# Overrides for the printer open_virtual() makes (the tests use this)
DEV_OPTIONS: dict[str, Any] = {}


def _matches(match: Match, raw: str) -> bool:
    if isinstance(match, int):
        numbered = _NUMBERED.match(raw)
        return numbered is not None and int(numbered.group(1)) == match
    return match in raw


@dataclass
class _Move:
    command: str
    start: dict[str, float]  # machine coordinates (before G92 offsets)
    end: dict[str, float]
    t0: float  # motion clock
    t1: float

    def at(self, t: float) -> dict[str, float]:
        f = 1.0 if self.t1 <= self.t0 else min(max((t - self.t0) / (self.t1 - self.t0), 0.0), 1.0)
        return {axis: self.start[axis] + (self.end[axis] - self.start[axis]) * f for axis in self.start}


class _MotionClock:
    """The time moves run on: real seconds, except that it can be held at a
    moment, which freezes motion (and only motion) until released."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._offset = 0.0
        self._hold: float | None = None

    def now(self) -> float:
        with self._lock:
            t = time.monotonic() - self._offset
            return t if self._hold is None else min(t, self._hold)

    @property
    def held(self) -> bool:
        with self._lock:
            return self._hold is not None and time.monotonic() - self._offset >= self._hold

    def hold_at(self, t: float) -> None:
        with self._lock:
            self._hold = t

    def release(self) -> None:
        """Carry on from where motion was held."""
        with self._lock:
            if self._hold is None:
                return
            behind = time.monotonic() - self._offset - self._hold
            if behind > 0:
                self._offset += behind
            self._hold = None


class VirtualPrinter:
    def __init__(
        self,
        *,
        planner_depth: int = 16,
        speed: float = 1.0,
        ok_delay_s: float = 0.0,
        busy_interval_s: float | None = 2.0,
        autoreport: bool = True,
        report_scale: float = 1.0,
        sensors: dict[str, float] | None = None,
        answers_m105: bool = True,
        m105_delay_s: float = 0.0,
        emergency_parser: bool = True,
        protocol: str = "marlin",
        axes: Iterable[str] | None = None,
    ):
        self.is_open = True
        # X, Y and the nozzles' axes; more are added as they are used
        self.axes = list(dict.fromkeys(["X", "Y", *(default_axes().all if axes is None else axes)]))
        # How many moves the planner buffers; a G0/G1 arriving when it's full
        # blocks (no "ok") until the oldest move finishes.
        self.planner_depth = planner_depth
        # How many times faster than real time moves run (math.inf: instantly)
        self.speed = speed
        # Real seconds between finishing a command and sending its "ok"
        self.ok_delay_s = ok_delay_s
        # How often "echo:busy: processing" is sent while a command is blocked
        # (None: never, like a firmware without HOST_KEEPALIVE_FEATURE).
        self.busy_interval_s = busy_interval_s
        # M155 is accepted either way; without autoreport nothing is reported.
        self.autoreport = autoreport
        # M155 S<n> reports every n * report_scale seconds.
        self.report_scale = report_scale
        # False: M105 gets no reply at all, not even "ok"
        self.answers_m105 = answers_m105
        # Real seconds M105 takes to be answered, holding up what follows it
        # (a printer slow to answer it)
        self.m105_delay_s = m105_delay_s
        # M410 stops motion on arrival; False: once the commands ahead have run
        self.emergency_parser = emergency_parser
        # "marlin", or "printess": the lab firmware's channel heaters
        self.protocol = protocol

        self.received: list[str] = []  # raw lines as they arrived
        self.executed: list[str] = []  # commands accepted and run, in order
        # Logical position as commanded (Marlin's current_position): where the
        # last planned move ends, or where the axes stopped after M410.
        self.position = {axis: 0.0 for axis in self.axes}
        self.relative = False
        self.feed = 1000.0  # mm/min
        self.steps_per_mm = {axis: 80.0 for axis in self.axes}
        # (actual, target) per heater, starting at ambient; a target of 0 is
        # off. No sensors: nothing is reported at all.
        self._ambient = dict(DEFAULT_SENSORS if sensors is None else sensors)
        self.temperatures = {key: (actual, 0.0) for key, actual in self._ambient.items()}
        # printess: per heater, "H" (only heats) or "C" (only cools) while it has a target
        self.modes: dict[str, str | None] = {key: None for key in self._ambient}

        # Logical minus machine position per axis, set by G92
        self._offset = {axis: 0.0 for axis in self.axes}
        # Machine position while no move is running
        self._idle = {axis: 0.0 for axis in self.axes}
        self._moves: deque[_Move] = deque()
        self._clock = _MotionClock()
        # Guards the planner and position, shared with the writer thread (M410)
        self._lock = threading.RLock()
        self._quickstops = 0
        self._hold: tuple[Match, float] | None = None
        self.held = threading.Event()  # motion reached the hold point

        self._corrupt: list[Match] = []
        self._drop: list[Match] = []
        self._last_n = 0
        self._raw = ""  # the line being run, as received
        self._halted = False
        self._in: queue.Queue[str] = queue.Queue()
        self._out: queue.Queue[str] = queue.Queue()
        self._report_every: float | None = None
        self._next_report = 0.0
        self._last_tick = time.monotonic()
        self._stop = threading.Event()
        if protocol == "printess" and self.temperatures:
            # The lab firmware reports its channels unasked once it is connected
            for line in self.temperature_lines():
                self._out.put(line)
        threading.Thread(target=self._run, name="virtual-printer", daemon=True).start()

    # --- pyserial interface -------------------------------------------------

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False
        self._stop.set()

    def write(self, data: bytes) -> int:
        for raw in data.decode().splitlines():
            self._receive(raw.strip())
        return len(data)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def readline(self) -> bytes:
        try:
            return (self._out.get(timeout=0.01) + "\n").encode()
        except queue.Empty:
            return b""

    # --- fault injection and test control ----------------------------------------

    def corrupt_next(self, match: Match) -> None:
        """Flip a bit in the next line containing `match` (or numbered
        `match`), as line noise would: the printer asks for it again."""
        self._corrupt.append(match)

    def drop_next(self, match: Match) -> None:
        """Lose the next line containing `match` (or numbered `match`) on the wire."""
        self._drop.append(match)

    def inject(self, line: str) -> None:
        """Send an unsolicited line to the host."""
        self._out.put(line)

    def hold_at(self, match: Match, fraction: float = 0.0) -> None:
        """Freeze motion `fraction` of the way through the next planned move
        whose line contains `match` (or is numbered `match`). `held` is set
        once it gets there; `release()` lets it carry on, and so does M410."""
        self._hold = (match, fraction)
        self.held.clear()

    def release(self) -> None:
        """Let held motion carry on. A hold armed with hold_at() since stays armed."""
        self.held.clear()
        self._clock.release()

    def move_externally(self, **deltas: float) -> None:
        """Move axes behind the host's back, as the printer's own display
        could: the position (and every planned move) shifts by `deltas`."""
        with self._lock:
            for axis, delta in deltas.items():
                self._idle[axis] += delta
                self.position[axis] += delta
                for move in self._moves:
                    move.start[axis] += delta
                    move.end[axis] += delta

    def machine_position(self) -> dict[str, float]:
        """Where the axes physically are right now, in logical coordinates."""
        with self._lock:
            machine = self._machine_at(self._clock.now())
            return {axis: machine[axis] + self._offset[axis] for axis in self.axes}

    def _add_axes(self, letters: Iterable[str]) -> None:
        """Nozzle axes not seen before start at 0, wherever the planned moves go."""
        new = [axis for axis in letters if axis in NOZZLE_AXIS_LETTERS and axis not in self.axes]
        if not new:
            return
        with self._lock:
            for axis in new:
                self.axes.append(axis)
                self.position[axis] = self._offset[axis] = self._idle[axis] = 0.0
                self.steps_per_mm[axis] = 80.0
                for move in self._moves:
                    move.start[axis] = move.end[axis] = 0.0

    def machine_coordinates(self) -> dict[str, float]:
        """Where the axes physically are right now, in machine coordinates:
        unlike the logical ones, G92 doesn't change them."""
        with self._lock:
            return self._machine_at(self._clock.now())

    def run_direct(self, command: str) -> list[str]:
        """Run one command on the firmware straight away, bypassing the
        serial line (no line numbers, no "ok" sent), and return its replies.
        For tests that only need the motion model, e.g. a whole file run at
        speed=math.inf."""
        return self._execute(command)

    @property
    def moves_planned(self) -> int:
        with self._lock:
            self._machine_at(self._clock.now())
            return len(self._moves)

    def temperature_lines(self) -> list[str]:
        """A temperature report, as lines: Marlin's one, or the lab
        firmware's block (printess)."""
        if self.protocol != "printess":
            report = self.temperature_report()
            return [report] if report else []
        channels = list(self.temperatures.items())

        def entry(key: str, value: str) -> str:
            return value if self.modes.get(key) else "X"

        def pwm(key: str, actual: float, target: float) -> str:
            mode = self.modes.get(key)
            moving = (mode == "H" and actual < target) or (mode == "C" and actual > target)
            return str(PRINTESS_PWM if moving else 0)

        status = [
            {"H": "Heating", "C": "Cooling"}.get(self.modes.get(key) or "", "Disabled") for key, _ in channels
        ]
        return [
            f"Status:       [{', '.join(status)}]",
            f"Temperatures: [{', '.join(entry(key, f'{actual:.2f}') for key, (actual, _) in channels)}]",
            f"Targets:      [{', '.join(entry(key, f'{target:.2f}') for key, (_, target) in channels)}]",
            f"PWM:          [{', '.join(pwm(key, a, t) for key, (a, t) in channels)}]",
        ]

    def temperature_report(self) -> str:
        """Marlin's format; with several tools, "T:" first repeats the active one (T0)."""
        temperatures = dict(self.temperatures)
        if "T" not in temperatures and "T0" in temperatures:
            temperatures = {"T": temperatures["T0"], **temperatures}
        parts = [f"{key}:{actual:.2f} /{target:.2f}" for key, (actual, target) in temperatures.items()]
        return " ".join(parts) + " @:0 B@:0" if parts else ""

    def _heater(self, code: str, params: dict[str, float]) -> str | None:
        """The heater an M104/M109/M140/M190/M141 is for, if the printer has it."""
        if code in ("M140", "M190"):
            key = "B"
        elif code == "M141":
            key = "C"
        elif "T" in params:
            key = f"T{int(params['T'])}"
        else:
            key = "T" if "T" in self.temperatures else "T0"  # the active tool
        return key if key in self.temperatures else None

    # --- receiving ------------------------------------------------------------------

    def _receive(self, raw: str) -> None:
        for match in self._drop:
            if _matches(match, raw):
                self._drop.remove(match)
                return
        for match in self._corrupt:
            if _matches(match, raw):
                self._corrupt.remove(match)
                raw = raw[:-1] + chr(ord(raw[-1]) ^ 1)  # garbles the checksum
                break
        self.received.append(raw)
        if self.emergency_parser and raw.split("*", 1)[0].split()[-1:] == ["M410"]:
            self._quickstop()  # the emergency parser acts on arrival...
        self._in.put(raw)  # ...and the line is still queued, and acked, in order

    # --- firmware ---------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            self._tick()
            try:
                raw = self._in.get(timeout=0.005)
            except queue.Empty:
                continue
            if not self._halted:
                self._process(raw)

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last_tick = now - self._last_tick, now
        if self._clock.held and self._hold is None:
            self.held.set()
        step = HEATING_RATE * dt * (1 if math.isinf(self.speed) else self.speed)
        for key, (actual, target) in self.temperatures.items():
            goal = target or self._ambient[key]
            mode = self.modes.get(key) if target else None
            if mode == "H":
                goal = max(actual, goal)  # only heats
            elif mode == "C":
                goal = min(actual, goal)  # only cools
            if actual != goal:
                actual += max(-step, min(step, goal - actual))
                self.temperatures[key] = (actual, target)
        if self._report_every and now >= self._next_report:
            self._next_report = now + self._report_every
            for line in self.temperature_lines():
                self._out.put(line)

    def _wait(self, done: Callable[[], bool]) -> None:
        """Block the command, like Marlin: busy messages and reports go on."""
        interval = self.busy_interval_s
        next_busy = time.monotonic() + (interval or math.inf)
        while not done() and not self._stop.is_set():
            self._tick()
            if interval and time.monotonic() >= next_busy:
                self._out.put("echo:busy: processing")
                next_busy += interval
            time.sleep(0.002)

    def _resend(self, error: str) -> None:
        self._out.put(f"Error:{error}, Last Line: {self._last_n}")
        self._out.put(f"Resend: {self._last_n + 1}")
        self._out.put("ok")

    def _process(self, raw: str) -> None:
        self._raw = raw
        if raw.startswith("N"):
            match = _NUMBERED.match(raw)
            if not match:
                return self._resend("No Checksum with line number")
            if checksum(raw.rsplit("*", 1)[0]) != int(match.group(3)):
                return self._resend("checksum mismatch")
            number, command = int(match.group(1)), match.group(2)
            if number != self._last_n + 1 and not command.startswith("M110"):
                return self._resend("Line Number is not Last Line Number+1")
            self._last_n = number
        else:
            command = raw
        command = command.split(";", 1)[0].strip()
        if not command:
            return
        self.executed.append(command)
        replies = self._execute(command)
        if self._halted:
            return
        if self.ok_delay_s:
            end = time.monotonic() + self.ok_delay_s
            self._wait(lambda: time.monotonic() >= end)
        for line in replies:
            self._out.put(line)

    def _execute(self, command: str) -> list[str]:
        """Run one command; returns the lines to send, ending with "ok"."""
        code, *words = command.upper().split()
        params: dict[str, float] = {}
        for word in words:
            try:
                params[word[0]] = float(word[1:])
            except ValueError:
                pass

        if code in ("G0", "G1", "G92", "M92"):
            self._add_axes(params)
        if code in ("G0", "G1"):
            self._move(params, command)
        elif code == "G4":
            self._drain()
            seconds = params.get("S", params.get("P", 0) / 1000) / self.speed
            end = time.monotonic() + seconds
            self._wait(lambda: time.monotonic() >= end)
        elif code == "G28":
            self._home([axis for axis in self.axes if axis in params] or self.axes)
        elif code in ("G90", "G91"):
            self.relative = code == "G91"
        elif code == "G92":
            with self._lock:
                for axis in self.axes:
                    if axis in params:
                        machine = self.position[axis] - self._offset[axis]
                        self._offset[axis] = params[axis] - machine
                        self.position[axis] = params[axis]
        elif code == "M92":
            for axis in self.axes:
                if axis in params:
                    self.steps_per_mm[axis] = params[axis]
        elif code == "M104" and self.protocol == "printess":
            return self._channel_target(words)
        elif code in ("M104", "M109", "M140", "M190", "M141"):
            key = self._heater(code, params)
            if key is not None:
                actual, _ = self.temperatures[key]
                target = params.get("S", 0.0)
                self.temperatures[key] = (actual, target)
                if code in ("M109", "M190") and target:
                    self._wait(lambda: abs(self.temperatures[key][0] - target) < 1)
        elif code == "M105":
            end = time.monotonic() + self.m105_delay_s
            while time.monotonic() < end and not self._stop.is_set():
                self._tick()
                time.sleep(0.002)
            if not self.answers_m105:
                return []
            if self.protocol == "printess":
                return [*self.temperature_lines(), "ok"]
            return [f"ok {self.temperature_report()}".rstrip()]
        elif code == "M110":
            self._last_n = int(params.get("N", 0))
        elif code == "M112":
            self._halted = True
            self._quickstop()
            self._out.put("Error:Printer halted. kill() called!")
            return []
        elif code == "M114":
            return [self._m114(), "ok"]
        elif code == "M115":
            return [
                FIRMWARE.format(axis_count=len(self.axes)),
                "Cap:SERIAL_XON_XOFF:0",
                "Cap:EEPROM:0",
                f"Cap:AUTOREPORT_TEMP:{int(self.autoreport)}",
                "Cap:AUTOREPORT_POS:0",
                "Cap:HOST_ACTION_COMMANDS:1",
                f"Cap:EMERGENCY_PARSER:{int(self.emergency_parser)}",
                "Cap:EXTENDED_M20:0",
                "ok",
            ]
        elif code == "M155":
            if self.autoreport:
                interval = params.get("S", 0) * self.report_scale
                self._report_every = interval or None
                self._next_report = time.monotonic()
        elif code == "M400":
            self._drain()
        elif code == "M410":
            self._quickstop()  # with the emergency parser, already stopped on arrival
        return ["ok"]

    def _channel_target(self, words: list[str]) -> list[str]:
        """printess: M104 I<n> T<°C> H|C, or M104 I<n> D (off: Disabled)."""
        channel = target = None
        mode: str | None = None
        disable = False
        for word in words:
            if word in ("H", "C"):
                mode = word
            elif word == "D":
                disable = True
            elif word[:1] in ("I", "T"):
                try:
                    value = float(word[1:])
                except ValueError:
                    continue
                if word[0] == "I":
                    channel = int(value)
                else:
                    target = value
        key = f"I{channel}"
        if channel is None or key not in self.temperatures:
            return [f"echo:Unknown heater channel I{channel}", "ok"]
        actual, _ = self.temperatures[key]
        if disable:
            self.temperatures[key] = (actual, 0.0)
            self.modes[key] = None
            return ["ok"]
        if target is None or mode is None:
            return ["echo:M104 needs T<temperature> and H or C, or D", "ok"]
        self.temperatures[key] = (actual, target)
        self.modes[key] = mode
        return ["ok"]

    def _m114(self) -> str:
        with self._lock:
            logical = " ".join(f"{axis}:{self.position[axis]:.2f}" for axis in self.axes)
            machine = self._machine_at(self._clock.now())
        counts = " ".join(f"{axis}:{round(machine[axis] * self.steps_per_mm[axis])}" for axis in self.axes)
        return f"{logical} Count {counts}"

    # --- motion -----------------------------------------------------------------------

    def _machine_at(self, t: float) -> dict[str, float]:
        """Machine position at motion time `t`; drops finished moves. Caller
        holds _lock."""
        while self._moves and self._moves[0].t1 <= t:
            self._idle = self._moves.popleft().end
        if self._moves and self._moves[0].t0 <= t:
            return self._moves[0].at(t)
        return dict(self._idle)

    def _planned(self) -> int:
        with self._lock:
            self._machine_at(self._clock.now())
            return len(self._moves)

    def _drain(self) -> None:
        """Wait until every planned move has finished (M400)."""
        self._wait(lambda: self._planned() == 0)

    def _move(self, params: dict[str, float], command: str) -> None:
        if "F" in params:
            self.feed = params["F"]
        quickstops = self._quickstops
        # A full planner blocks the command until a move finishes.
        self._wait(lambda: self._planned() < self.planner_depth or self._quickstops != quickstops)
        with self._lock:
            if self._quickstops != quickstops:
                return  # M410 came in while it waited: the move is dropped
            target = dict(self.position)
            for axis in self.axes:
                if axis in params:
                    target[axis] = target[axis] + params[axis] if self.relative else params[axis]
            start = {axis: self.position[axis] - self._offset[axis] for axis in self.axes}
            end = {axis: target[axis] - self._offset[axis] for axis in self.axes}
            distance = math.dist([start[a] for a in self.axes], [end[a] for a in self.axes])
            duration = distance / (self.feed / 60) / self.speed if self.feed > 0 else 0.0
            now = self._clock.now()
            t0 = max(now, self._moves[-1].t1 if self._moves else now)
            self._moves.append(_Move(command, start, end, t0, t0 + duration))
            self.position = target
            hold = self._hold
            if hold is not None and _matches(hold[0], self._raw):
                self._hold = None
                self._clock.hold_at(t0 + hold[1] * duration)

    def _home(self, axes: list[str]) -> None:
        """No endstops to find: the axes are simply taken to be at 0, and
        their G92 offsets are cleared, as homing does."""
        self._drain()
        with self._lock:
            for axis in axes:
                self._idle[axis] = 0.0
                self._offset[axis] = 0.0
                self.position[axis] = 0.0

    def _quickstop(self) -> None:
        """M410: stop where the axes are, drop every planned move."""
        with self._lock:
            machine = self._machine_at(self._clock.now())
            self._moves.clear()
            self._idle = machine
            self.position = {axis: machine[axis] + self._offset[axis] for axis in self.axes}
            self._quickstops += 1
        self._hold = None
        self.held.clear()
        self._clock.release()
