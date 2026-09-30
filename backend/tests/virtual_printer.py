"""A small Marlin stand-in behind the pyserial interface, for protocol tests.

Runs its own firmware thread: it checks line numbers and checksums (asking
for a resend like Marlin does), keeps a move planner with real durations so
long moves produce "echo:busy: processing", auto-reports temperatures after
M155, and answers M105/M114. Faults can be injected: a line corrupted on the
wire, a line lost on the wire, or any unsolicited line.

Like the other fakes in these tests, M410 (handled by Marlin's emergency
parser) stops motion and gets no "ok" of its own.
"""
from __future__ import annotations

import math
import queue
import re
import threading
import time
from collections import deque

from backend.serial_manager import checksum

AXES = "XYZABC"
_NUMBERED = re.compile(r"^N(\d+)\s+(.*)\*(\d+)$")


class VirtualPrinter:
    def __init__(
        self,
        *,
        autoreport: bool = True,
        planner_depth: int = 4,
        speed_scale: float = 0.0,
        busy_interval_s: float | None = 0.05,
        report_scale: float = 0.01,
    ):
        self.is_open = True
        # M155 is accepted either way; without autoreport nothing is reported.
        self.autoreport = autoreport
        self.planner_depth = planner_depth
        # Real move time is scaled by this (0: moves are instant).
        self.speed_scale = speed_scale
        # How often "echo:busy" is sent while blocked (None: never, like a
        # firmware without HOST_KEEPALIVE_FEATURE).
        self.busy_interval_s = busy_interval_s
        # M155 S<n> reports every n * report_scale seconds.
        self.report_scale = report_scale
        self.received: list[str] = []  # raw lines as they arrived
        self.executed: list[str] = []  # commands accepted and run, in order
        self.position = {axis: 0.0 for axis in AXES}
        self.relative = False
        self.feed = 1000.0  # mm/min
        self.temperatures = {"T": (21.3, 0.0), "B": (20.1, 0.0)}
        self._corrupt: list[str] = []
        self._drop: list[str] = []
        self._last_n = 0
        self._in: queue.Queue[str] = queue.Queue()
        self._out: queue.Queue[str] = queue.Queue()
        self._moves: deque[float] = deque()  # end times of planned moves
        self._report_every: float | None = None
        self._next_report = 0.0
        self._quickstop = threading.Event()
        self._stop = threading.Event()
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

    def readline(self) -> bytes:
        try:
            return (self._out.get(timeout=0.01) + "\n").encode()
        except queue.Empty:
            return b""

    # --- fault injection ------------------------------------------------------

    def corrupt_next(self, text: str) -> None:
        """Flip a bit in the next line containing `text`, as line noise would."""
        self._corrupt.append(text)

    def drop_next(self, text: str) -> None:
        """Lose the next line containing `text` on the wire."""
        self._drop.append(text)

    def inject(self, line: str) -> None:
        """Send an unsolicited line to the host."""
        self._out.put(line)

    def temperature_report(self) -> str:
        parts = [f"{key}:{actual:.2f} /{target:.2f}" for key, (actual, target) in self.temperatures.items()]
        return " ".join(parts) + " @:0 B@:0"

    # --- firmware ---------------------------------------------------------------

    def _receive(self, raw: str) -> None:
        for text in self._drop:
            if text in raw:
                self._drop.remove(text)
                return
        for text in self._corrupt:
            if text in raw:
                self._corrupt.remove(text)
                raw = raw.replace(text, text[:-1] + chr(ord(text[-1]) ^ 1), 1)
                break
        self.received.append(raw)
        if "M410" in raw:
            self._quickstop.set()  # emergency parser: acts on arrival
            return
        self._in.put(raw)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._tick()
            try:
                raw = self._in.get(timeout=0.005)
            except queue.Empty:
                continue
            self._process(raw)

    def _tick(self) -> None:
        now = time.monotonic()
        if self._quickstop.is_set():
            self._quickstop.clear()
            self._moves.clear()
        while self._moves and self._moves[0] <= now:
            self._moves.popleft()
        if self._report_every and now >= self._next_report:
            self._next_report = now + self._report_every
            self._out.put(self.temperature_report())

    def _wait(self, done) -> None:
        """Block the command, like Marlin: busy messages and reports go on."""
        next_busy = time.monotonic() + (self.busy_interval_s or math.inf)
        while not done():
            self._tick()
            if time.monotonic() >= next_busy:
                self._out.put("echo:busy: processing")
                next_busy += self.busy_interval_s
            time.sleep(0.002)

    def _resend(self, error: str) -> None:
        self._out.put(f"Error:{error}, Last Line: {self._last_n}")
        self._out.put(f"Resend: {self._last_n + 1}")
        self._out.put("ok")

    def _process(self, raw: str) -> None:
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
        reply = self._execute(command)
        self._out.put(reply)

    def _execute(self, command: str) -> str:
        code, *words = command.upper().split()
        params: dict[str, float] = {}
        for word in words:
            try:
                params[word[0]] = float(word[1:])
            except ValueError:
                pass

        if code == "M110":
            self._last_n = int(params.get("N", 0))
        elif code == "M105":
            return "ok " + self.temperature_report()
        elif code == "M155":
            if self.autoreport:
                interval = params.get("S", 0) * self.report_scale
                self._report_every = interval or None
                self._next_report = time.monotonic()
        elif code == "M114":
            logical = " ".join(f"{axis}:{self.position[axis]:.2f}" for axis in AXES)
            self._out.put(f"{logical} Count X:0 Y:0 Z:0")
        elif code in ("G90", "G91"):
            self.relative = code == "G91"
        elif code == "G92":
            for axis in AXES:
                if axis in params:
                    self.position[axis] = params[axis]
        elif code in ("G0", "G1"):
            self._move(params)
        elif code == "G4":
            self._wait(lambda: not self._moves)
            seconds = params.get("S", params.get("P", 0) / 1000) * (self.speed_scale or 1)
            end = time.monotonic() + seconds
            self._wait(lambda: time.monotonic() >= end)
        elif code == "M400":
            self._wait(lambda: not self._moves)
        return "ok"

    def _move(self, params: dict[str, float]) -> None:
        if "F" in params:
            self.feed = params["F"]
        target = dict(self.position)
        for axis in AXES:
            if axis in params:
                target[axis] = self.position[axis] + params[axis] if self.relative else params[axis]
        distance = math.dist(
            [self.position[a] for a in AXES], [target[a] for a in AXES]
        )
        duration = distance / (self.feed / 60) * self.speed_scale
        # A full planner blocks the command until a move finishes.
        self._wait(lambda: len(self._moves) < self.planner_depth)
        start = max(time.monotonic(), self._moves[-1] if self._moves else 0.0)
        self._moves.append(start + duration)
        self.position = target
