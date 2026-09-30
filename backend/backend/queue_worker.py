from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from enum import Enum
from typing import Callable, Sequence

from backend.checkpoint import (
    Checkpoint,
    build_resume_commands,
    fmt,
    locate_line,
    parse_m114,
)
from backend.gcode_processor import AXES, PRESSURIZE_FEED, MachineState, step
from backend.limits import LOW_TRAVEL_FRACTION, PLUNGER_AXES, plunger_step
from backend.serial_manager import (
    ResendUnavailable,
    SerialError,
    SerialManager,
    SerialTimeout,
)

_BC_RUNTIME = re.compile(r"([BC])(-?\d+\.?\d*)")

# How many sent line indices to remember for locating an e-stop. More than
# the printer's move buffer can hold, so the line it stopped on is always
# among them.
RECENT_LINES = 32

# Why a print can't be resumed after the serial connection dropped. The board
# may have reset (reopening the port can do that), losing its zero.
CONNECTION_LOST = "Connection lost — re-zero before printing"

logger = logging.getLogger(__name__)


class PrintStatus(str, Enum):
    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


class NotResumable(Exception):
    pass


class QueueWorker:
    def __init__(
        self,
        serial_manager: SerialManager,
        on_event: Callable[[dict], None] | None = None,
        on_print_end: Callable[[str, int | None], None] | None = None,
        on_print_resumed: Callable[[], None] | None = None,
        retract_on_estop: bool = True,
        syringe_travel_mm: float | None = None,
    ):
        self._serial = serial_manager
        self._priority_queue: asyncio.Queue[str] = asyncio.Queue()
        self._status = PrintStatus.IDLE
        # The loaded print. Stopping or pausing never discards lines; _next is
        # the index of the next line to send.
        self._lines: list[str] = []
        self._state_before: list[MachineState] = []
        self._state_after: list[MachineState] = []
        self._next = 0
        # (index into _lines, as-sent state before, as-sent state after) for
        # each recently sent print line — reflects what was actually written
        # to the printer (flow scaling included), not the planned path.
        self._recent: deque[tuple[int, MachineState, MachineState]] = deque(maxlen=RECENT_LINES)
        # Tracks the machine state as lines are actually sent (see _track_send).
        # Seeded from a real M114 at the start of each print so even a fully
        # relative (G91) file has a known absolute reference to build from.
        self._sent_state = MachineState(pos={ax: None for ax in AXES})
        # Why _sent_state couldn't be seeded from M114 at print start, if it
        # couldn't — makes any later stop unconditionally not resumable.
        self._seed_error: str | None = None
        self._extrusion_axes: tuple[str, ...] = ()
        self._retract_mm = 0.0
        self._retract_on_estop = retract_on_estop
        self._checkpoint: Checkpoint | None = None
        self._stop_reason: str | None = None  # why the last stop can't be resumed
        self._paused = asyncio.Event()
        self._paused.set()  # not paused initially
        self._task: asyncio.Task | None = None
        self._on_event = on_event
        # Called once per print with the end reason ("completed", "stopped",
        # "estop" or "error") and the line a resumable stop halted on.
        self._on_print_end = on_print_end
        # Called when an e-stopped print is resumed.
        self._on_print_resumed = on_print_resumed
        self._print_active = False
        self._flow_rate: float = 100.0  # percentage, same semantics as M221
        self._time_estimate_s: float | None = None
        # Plunger travel of a full syringe, and how far each plunger has been
        # pushed (as sent) since the print was loaded.
        self._syringe_travel_mm = syringe_travel_mm
        self._plunger_pushed: dict[str, float] = {}
        self._low_travel_warned: set[str] = set()

    @property
    def status(self) -> PrintStatus:
        return self._status

    @property
    def lines_sent(self) -> int:
        return self._next

    @property
    def lines_total(self) -> int:
        return len(self._lines)

    @property
    def flow_rate(self) -> float:
        return self._flow_rate

    @property
    def checkpoint(self) -> Checkpoint | None:
        return self._checkpoint

    @property
    def resumable(self) -> bool:
        return self._status == PrintStatus.STOPPED and self._checkpoint is not None

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    @property
    def print_active(self) -> bool:
        """A print is printing or paused (not yet stopped or finished)."""
        return self._print_active

    @property
    def sending(self) -> bool:
        """The worker task is still running. It can outlive the print by a
        line or two (e.g. a preamble line queued behind a manual command)."""
        return self._task is not None and not self._task.done()

    def set_flow_rate(self, rate: float) -> None:
        """Set runtime flow rate as percentage (100 = normal)."""
        self._flow_rate = max(0, rate)

    def _emit(self, event: dict) -> None:
        if self._on_event:
            self._on_event(event)

    def _finish(self, end_reason: str, resume_line: int | None = None) -> None:
        if not self._print_active:
            return
        self._print_active = False
        self._notify_end(end_reason, resume_line)

    def _notify_end(self, end_reason: str, resume_line: int | None) -> None:
        if self._on_print_end:
            try:
                self._on_print_end(end_reason, resume_line)
            except Exception:
                logger.exception("on_print_end callback failed")

    def load_gcode(
        self,
        lines: list[str],
        time_estimate_s: float | None = None,
        *,
        state_before: Sequence[MachineState] | None = None,
        state_after: Sequence[MachineState] | None = None,
        extrusion_axes: Sequence[str] = (),
        pressurize_mm: float = 0.0,
    ) -> None:
        """Load a print. The optional per-line states (from ProcessedGcode)
        are what make an e-stop resumable."""
        has_states = state_before is not None and state_after is not None
        if has_states and not (len(state_before) == len(state_after) == len(lines)):
            raise ValueError("state_before/state_after must match lines")

        self.flush_priority()
        self._lines, self._state_before, self._state_after = [], [], []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith(";"):
                self._lines.append(stripped)
                if has_states:
                    self._state_before.append(state_before[i])
                    self._state_after.append(state_after[i])
        self._next = 0
        self._recent.clear()
        self._sent_state = MachineState(pos={ax: None for ax in AXES})
        self._seed_error = None
        self._time_estimate_s = time_estimate_s
        self._extrusion_axes = tuple(extrusion_axes)
        self._retract_mm = pressurize_mm
        self._plunger_pushed = {axis: 0.0 for axis in PLUNGER_AXES}
        self._low_travel_warned = set()
        self._clear_checkpoint()

    def enqueue_priority(self, command: str) -> None:
        self._priority_queue.put_nowait(command.strip())

    def flush_priority(self) -> None:
        while not self._priority_queue.empty():
            try:
                self._priority_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def start(self, start_position: dict[str, float] | None = None) -> None:
        """Start the loaded print. `start_position` is the printer's position
        as just read with M114; without it the worker reads it itself."""
        if self._task and not self._task.done():
            return
        self._clear_checkpoint()
        if start_position is not None:
            self._sent_state = MachineState(pos={ax: start_position.get(ax) for ax in AXES})
            self._seed_error = None
        self._status = PrintStatus.PRINTING
        self._print_active = True
        self._paused.set()
        self._emit({"type": "status", "value": self._status.value})
        # Keep idle motors enabled so a stopped print holds its position.
        self._task = asyncio.create_task(
            # M110 N0 starts line numbering: print lines go out from N1.
            self._run(preamble=["M84 S0", "M110 N0"], seed=start_position is None)
        )

    def pause(self) -> None:
        if self._status == PrintStatus.PRINTING:
            self._status = PrintStatus.PAUSED
            self._paused.clear()
            self._emit({"type": "status", "value": self._status.value})

    def resume(self) -> None:
        """Continue a paused print."""
        if self._status == PrintStatus.PAUSED:
            self._status = PrintStatus.PRINTING
            self._paused.set()
            self._emit({"type": "status", "value": self._status.value})

    # --- e-stop / checkpoint ------------------------------------------------

    def _clear_checkpoint(self) -> None:
        self._checkpoint = None
        self._stop_reason = None

    def _not_resumable(self, reason: str) -> None:
        self._checkpoint = None
        self._stop_reason = reason
        self._emit({"type": "stop", "resumable": False, "reason": reason})
        self._emit({"type": "error", "message": f"The print can't be resumed: {reason}"})

    def connection_lost(self) -> None:
        """The serial connection dropped. A running print is stopped for good:
        the board may have reset and lost its zero, so it can't be resumed."""
        if not self._print_active:
            self.invalidate_checkpoint(CONNECTION_LOST)
            return
        logger.error("Connection lost during a print; stopping it")
        self._print_active = False
        self._status = PrintStatus.STOPPED
        self.flush_priority()
        self._paused.set()  # unblock the worker so it can exit
        self._emit({"type": "status", "value": self._status.value})
        self._not_resumable(CONNECTION_LOST)
        self._notify_end("error", None)

    def invalidate_checkpoint(self, reason: str) -> None:
        """Drop the resume checkpoint, e.g. after something moved the plungers
        or changed the coordinate system."""
        if self._checkpoint is None:
            return
        logger.info("Resume checkpoint invalidated: %s", reason)
        self._not_resumable(reason)

    async def estop(self, end_reason: str = "estop") -> None:
        """Emergency stop: halt the queue and send M410 straight to the printer.

        M410 bypasses both queues and the in-flight line (Marlin's
        EMERGENCY_PARSER acts on it on arrival). If a print was running, the
        printer's position is then read back to find the line it stopped on,
        so the print can be resumed from there.
        `end_reason` is reported to on_print_end if a print was running.
        """
        was_active = self._print_active
        self._print_active = False  # the worker's exit must not end the print as "error"
        self._status = PrintStatus.STOPPED  # worker won't send another line
        self.flush_priority()
        self._paused.set()  # unblock worker so it can exit
        self._emit({"type": "status", "value": self._status.value})
        try:
            await self._serial.emergency_write("M410")
        except SerialError:
            logger.error("Failed to send emergency stop (M410)")
            self._emit({"type": "printer", "connected": False, "port": None})
            if was_active:
                self._not_resumable(CONNECTION_LOST)
                self._notify_end(end_reason, None)
            return

        if not was_active:
            if self._checkpoint is None:
                self._emit({"type": "stop", "resumable": False, "reason": None})
            return
        await self._take_checkpoint()
        self._notify_end(
            end_reason, self._checkpoint.line if self._checkpoint else None
        )

    async def _take_checkpoint(self) -> None:
        if self._seed_error:
            self._not_resumable(self._seed_error)
            return
        try:
            # send_lines queues behind the line in flight, so this also waits for the
            # line that was in flight when M410 went out.
            replies = await self._serial.send_lines(["M400", "M114"])
        except SerialError as exc:
            self._not_resumable(f"Couldn't read the printer position: {exc}")
            return

        position = parse_m114(replies[1])
        if position is None:
            self._not_resumable(f"Couldn't parse the printer position from {replies[1]!r}")
            return

        located = locate_line(list(self._recent), self._lines, position)
        if located is None:
            where = " ".join(f"{axis}{fmt(value)}" for axis, value in position.items())
            reason = f"The stop position ({where}) is not on any recently sent line"
            self._not_resumable(reason)
            return
        line, after = located

        retract: dict[str, float] = {}
        axes = [axis for axis in self._extrusion_axes if axis in position]
        if self._retract_on_estop and axes and self._retract_mm > 0:
            # Plungers extrude in the negative direction, so retract is positive.
            move = " ".join(f"{axis}{fmt(self._retract_mm)}" for axis in axes)
            try:
                await self._serial.send_lines(["G91", f"G1 {move} F{PRESSURIZE_FEED}", "G90"])
            except SerialError as exc:
                self._not_resumable(f"Retracting the plungers failed: {exc}")
                return
            retract = {axis: self._retract_mm for axis in axes}

        self._checkpoint = Checkpoint(line=line, position=position, after=after, retract=retract)
        self._stop_reason = None
        logger.info("E-stop checkpoint: line %d (%s) at %s", line, self._lines[line], position)
        self._emit({"type": "stop", "resumable": True, "reason": None, "line": line})

    async def resume_from_stop(self) -> None:
        """Return to the e-stop checkpoint and continue the print from there.

        Raises NotResumable without a valid checkpoint, and SerialError if the
        return moves fail (the checkpoint is kept, so this can be retried).
        """
        checkpoint = self._checkpoint
        if self._status != PrintStatus.STOPPED or checkpoint is None:
            raise NotResumable(self._stop_reason or "No stopped print to resume")
        if self._task and not self._task.done():
            await self._task  # exits right after its in-flight line

        k = checkpoint.line
        commands = build_resume_commands(checkpoint)
        await self._serial.send_lines(commands)

        self._clear_checkpoint()
        # The tracker continues from where the resume commands actually left
        # the machine — the checkpoint's as-sent after-state.
        self._sent_state = checkpoint.after
        self._recent.append((k, checkpoint.after, checkpoint.after))
        self._next = k + 1
        self._status = PrintStatus.PRINTING
        self._print_active = True
        self._paused.set()
        if self._on_print_resumed:
            try:
                self._on_print_resumed()
            except Exception:
                logger.exception("on_print_resumed callback failed")
        self._emit({"type": "status", "value": self._status.value})
        self._emit_progress()
        self._task = asyncio.create_task(self._run(preamble=[], seed=False))

    # --- sending ------------------------------------------------------------

    async def _seed_sent_state(self) -> None:
        """Read the printer's actual position before sending anything, so the
        as-sent tracker has known axes from line one.

        Without this, a fully relative (G91) file would never establish an
        absolute reference, and every position would stay unknown. If the
        read fails or can't be parsed, the print still runs — it just won't
        be resumable, with `_seed_error` explaining why.
        """
        try:
            reply = await self._serial.send("M114")
        except SerialError as exc:
            self._seed_error = f"Couldn't read the printer's starting position: {exc}"
            return

        position = parse_m114(reply)
        if position is None:
            self._seed_error = f"Couldn't parse the printer's starting position from {reply!r}"
            return

        self._seed_error = None
        self._sent_state = MachineState(pos={ax: position.get(ax) for ax in AXES})

    def _track_send(self, line: str, index: int | None) -> None:
        """Advance the as-sent position tracker for a line about to be sent.

        Called before the write completes: a line that times out waiting for
        "ok" may still have reached and been acted on by the printer, so it's
        still a candidate for where an e-stop actually landed.
        """
        before = self._sent_state
        after = step(before, line)
        self._sent_state = after
        if index is not None:
            self._recent.append((index, before, after))
        for axis, delta in plunger_step(before, after, line).items():
            self._plunger_pushed[axis] = self._plunger_pushed.get(axis, 0.0) - delta
            self._check_plunger_travel(axis)

    def _check_plunger_travel(self, axis: str) -> None:
        """Warn once per print when a syringe has under 10% of its travel left."""
        travel = self._syringe_travel_mm
        if not travel or axis in self._low_travel_warned:
            return
        remaining = travel - self._plunger_pushed[axis]
        if remaining >= LOW_TRAVEL_FRACTION * travel:
            return
        self._low_travel_warned.add(axis)
        side = "left" if axis == "B" else "right"
        self._emit({
            "type": "warning",
            "message": (
                f"The {side} syringe ({axis}) is nearly empty: "
                f"{max(remaining, 0):.1f} of {travel:g} mm plunger travel left."
            ),
        })

    async def _run(self, preamble: list[str], seed: bool = False) -> None:
        logger.info("Queue worker started at line %d/%d", self._next, len(self._lines))

        try:
            if seed:
                await self._seed_sent_state()

            for command in preamble:
                self._track_send(command, index=None)
                await self._send(command)

            while True:
                # Always service priority queue first
                try:
                    cmd = self._priority_queue.get_nowait()
                    self._track_send(cmd, index=None)
                    await self._send(cmd)
                    continue
                except asyncio.QueueEmpty:
                    pass

                if self._status == PrintStatus.STOPPED:
                    break

                # Wait if paused
                await self._paused.wait()

                if self._status == PrintStatus.STOPPED:
                    break

                if self._next >= len(self._lines):
                    self._status = PrintStatus.COMPLETED
                    self._emit({"type": "status", "value": self._status.value})
                    self._finish("completed")
                    break

                index = self._next
                sent_line = self._apply_flow_rate(self._lines[index])
                self._track_send(sent_line, index=index)
                # On failure _next stays put: a timed-out line is re-sent on resume.
                if await self._send(sent_line, numbered=True):
                    self._next = index + 1
                    self._emit_progress()
                await asyncio.sleep(0)

        except Exception:
            logger.exception("Queue worker error")
        finally:
            # No-op if the print already ended; otherwise the worker crashed or
            # was cancelled mid-print.
            self._finish("error")
            logger.info(
                "Queue worker finished: status=%s, sent=%d/%d",
                self._status.value,
                self._next,
                len(self._lines),
            )

    def _apply_flow_rate(self, line: str) -> str:
        """Scale B/C values by the current flow rate percentage."""
        if self._flow_rate == 100.0:
            return line
        multiplier = self._flow_rate / 100.0

        def scale(m):
            axis = m.group(1)
            val = float(m.group(2)) * multiplier
            return f"{axis}{val:g}"

        return _BC_RUNTIME.sub(scale, line)

    def _emit_progress(self) -> None:
        total = len(self._lines)
        event: dict = {
            "type": "progress",
            "lines_sent": self._next,
            "lines_total": total,
        }
        if self._time_estimate_s is not None and total:
            event["time_remaining_s"] = self._time_estimate_s * (1 - self._next / total)
        self._emit(event)

    async def _send(self, line: str, numbered: bool = False) -> bool:
        """Send one line. Returns False if it failed and must not be counted."""
        try:
            await self._serial.send(line, numbered=numbered)
            return True
        except (SerialTimeout, ResendUnavailable) as exc:
            logger.error("No usable reply to %s: %s", line, exc)
            if self._status != PrintStatus.STOPPED:
                self._status = PrintStatus.PAUSED
                self._paused.clear()
                self._emit({"type": "status", "value": self._status.value})
            self._emit({"type": "error", "message": f"{exc}. Print paused."})
            return False
        except SerialError:
            logger.error("Failed to send: %s", line)
            if self._status == PrintStatus.STOPPED:
                # An e-stop, or connection_lost() via the disconnect
                # callback, is already handling this print.
                return False
            self._emit({"type": "printer", "connected": False, "port": None})
            self.connection_lost()
            return False
