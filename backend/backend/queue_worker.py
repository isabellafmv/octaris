from __future__ import annotations

import asyncio
import logging
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
from backend.gcode_processor import AXES, PRESSURIZE_FEED, MachineState, scale_flow, step
from backend.limits import LOW_TRAVEL_FRACTION, PLUNGER_AXES, plunger_step
from backend.schemas import (
    ErrorEvent,
    Event,
    PrintEndEvent,
    PrinterEvent,
    PrintResumedEvent,
    ProgressEvent,
    StatusEvent,
    StopEvent,
    WarningEvent,
)
from backend.serial_manager import (
    ResendUnavailable,
    SerialError,
    SerialManager,
    SerialTimeout,
)

# How many sent line indices to remember for locating an e-stop. More than
# the printer's move buffer can hold, so the line it stopped on is always
# among them.
RECENT_LINES = 32

# Why a print can't be resumed after the serial connection dropped. The board
# may have reset (reopening the port can do that), losing its zero.
CONNECTION_LOST = "Connection lost — re-zero before printing"

logger = logging.getLogger(__name__)


class PrintStatus(str, Enum):
    """The print status reported to clients."""

    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


class PrintState(str, Enum):
    """The worker's state. Clients see STOPPED_RESUMABLE as STOPPED, plus
    `resumable`."""

    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    STOPPED_RESUMABLE = "stopped_resumable"  # holds a checkpoint to resume from
    STOPPED = "stopped"
    COMPLETED = "completed"


IDLE, PRINTING, PAUSED, STOPPED_RESUMABLE, STOPPED, COMPLETED = PrintState

# The states each state may move to. Anything else raises InvalidTransition.
TRANSITIONS: dict[PrintState, frozenset[PrintState]] = {
    IDLE: frozenset({PRINTING, STOPPED}),
    PRINTING: frozenset({PAUSED, STOPPED, COMPLETED}),
    PAUSED: frozenset({PRINTING, STOPPED}),
    # A stop becomes resumable once its checkpoint is taken, and stops being
    # resumable when something invalidates the checkpoint.
    STOPPED: frozenset({PRINTING, STOPPED_RESUMABLE}),
    STOPPED_RESUMABLE: frozenset({PRINTING, STOPPED}),
    COMPLETED: frozenset({PRINTING, STOPPED}),
}

# A print is running: its lines are being sent, or will be after a pause.
ACTIVE = frozenset({PRINTING, PAUSED})


class InvalidTransition(Exception):
    pass


class NotResumable(Exception):
    pass


class SentTracker:
    """The machine state as print lines are actually sent (flow scaling
    included), and the recently sent lines, for locating an e-stop.

    Seeded from a real M114 at the start of each print, so even a fully
    relative (G91) file has a known absolute reference to build from.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.state = MachineState(pos={ax: None for ax in AXES})
        # (index into the print's lines, as-sent state before, after)
        self.recent: deque[tuple[int, MachineState, MachineState]] = deque(maxlen=RECENT_LINES)
        # Why the starting position is unknown, if it is. Any stop of this
        # print is then not resumable.
        self.error: str | None = None

    def seed(self, position: dict[str, float]) -> None:
        self.state = MachineState(pos={ax: position.get(ax) for ax in AXES})
        self.error = None

    def track(self, line: str, index: int | None) -> tuple[MachineState, MachineState]:
        before = self.state
        self.state = step(before, line)
        if index is not None:
            self.recent.append((index, before, self.state))
        return before, self.state

    def resume_at(self, index: int, after: MachineState) -> None:
        self.state = after
        self.recent.append((index, after, after))


class QueueWorker:
    def __init__(
        self,
        serial_manager: SerialManager,
        on_event: Callable[[dict], None] | None = None,
        retract_on_estop: bool = True,
        syringe_travel_mm: float | None = None,
    ):
        self._serial = serial_manager
        self._priority_queue: asyncio.Queue[str] = asyncio.Queue()
        self._state = IDLE
        # Set in every state but PAUSED; the worker waits on it between lines.
        self._unpaused = asyncio.Event()
        self._unpaused.set()
        # The loaded print. Stopping or pausing never discards lines; _next is
        # the index of the next line to send.
        self._lines: list[str] = []
        self._next = 0
        self._tracker = SentTracker()
        self._extrusion_axes: tuple[str, ...] = ()
        self._retract_mm = 0.0
        self._retract_on_estop = retract_on_estop
        # Set exactly while the state is STOPPED_RESUMABLE
        self._checkpoint: Checkpoint | None = None
        self._stop_reason: str | None = None  # why the last stop can't be resumed
        self._task: asyncio.Task | None = None
        self._on_event = on_event
        self._flow_rate: float = 100.0  # percentage, same semantics as M221
        self._time_estimate_s: float | None = None
        # Plunger travel of a full syringe, and how far each plunger has been
        # pushed (as sent) since the print was loaded.
        self._syringe_travel_mm = syringe_travel_mm
        self._plunger_pushed: dict[str, float] = {}
        self._low_travel_warned: set[str] = set()

    @property
    def state(self) -> PrintState:
        return self._state

    @property
    def status(self) -> PrintStatus:
        if self._state == STOPPED_RESUMABLE:
            return PrintStatus.STOPPED
        return PrintStatus(self._state.value)

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
        return self._state == STOPPED_RESUMABLE

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    @property
    def print_active(self) -> bool:
        """A print is printing or paused (not yet stopped or finished)."""
        return self._state in ACTIVE

    @property
    def sending(self) -> bool:
        """The worker task is still running. It can outlive the print by a
        line or two (e.g. a preamble line queued behind a manual command)."""
        return self._task is not None and not self._task.done()

    def set_flow_rate(self, rate: float) -> None:
        """Set runtime flow rate as percentage (100 = normal)."""
        self._flow_rate = max(0, rate)

    def _emit(self, event: Event) -> None:
        if self._on_event:
            self._on_event(event.dump())

    def _transition(self, new: PrintState) -> None:
        if new not in TRANSITIONS[self._state]:
            raise InvalidTransition(f"Can't go from {self._state.value} to {new.value}")
        old_status = self.status
        self._state = new
        if new == PAUSED:
            self._unpaused.clear()
        else:
            self._unpaused.set()  # also wakes a paused worker so it can exit
        if new != STOPPED_RESUMABLE:
            self._checkpoint = None
        if self.status != old_status:
            self._emit(StatusEvent(value=self.status.value))

    def _end_print(self, end_reason: str, resume_line: int | None = None) -> None:
        """Report the end of a print ("completed", "stopped", "estop" or
        "error") and the line a resumable stop halted on. Called once, right
        after the print leaves ACTIVE."""
        self._emit(PrintEndEvent(reason=end_reason, resume_line=resume_line))

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
        """Load a print, replacing any stopped or finished one.

        state_before/state_after (the planned per-line states from
        ProcessedGcode) are checked for consistency only: resuming works
        from the as-sent states the worker tracks itself.
        """
        if self._state in ACTIVE:
            raise InvalidTransition("Can't load a new print while one is running")
        has_states = state_before is not None and state_after is not None
        if has_states and not (len(state_before) == len(state_after) == len(lines)):
            raise ValueError("state_before/state_after must match lines")

        self.flush_priority()
        self._lines = [
            stripped for line in lines
            if (stripped := line.strip()) and not stripped.startswith(";")
        ]
        self._next = 0
        self._tracker.reset()
        self._time_estimate_s = time_estimate_s
        self._extrusion_axes = tuple(extrusion_axes)
        self._retract_mm = pressurize_mm
        self._plunger_pushed = {axis: 0.0 for axis in PLUNGER_AXES}
        self._low_travel_warned = set()
        if self._state == STOPPED_RESUMABLE:
            self._transition(STOPPED)  # the old checkpoint is for the old lines
        self._stop_reason = None

    def enqueue_priority(self, command: str) -> None:
        self._priority_queue.put_nowait(command.strip())

    def flush_priority(self) -> None:
        while not self._priority_queue.empty():
            try:
                self._priority_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def wait_until_sent(self) -> None:
        """Wait for the worker of a stopped or finished print to send its
        last in-flight line, so a new one can start."""
        if self._state not in ACTIVE and self._task and not self._task.done():
            await self._task

    def start(self, start_position: dict[str, float] | None = None) -> None:
        """Start the loaded print. `start_position` is the printer's position
        as just read with M114; without it the worker reads it itself."""
        if self.sending:
            raise InvalidTransition("The previous print is still stopping")
        self._transition(PRINTING)
        self._stop_reason = None
        if start_position is not None:
            self._tracker.seed(start_position)
        # Keep idle motors enabled so a stopped print holds its position.
        self._task = asyncio.create_task(
            # M110 N0 starts line numbering: print lines go out from N1.
            self._run(preamble=["M84 S0", "M110 N0"], seed=start_position is None)
        )

    def pause(self) -> None:
        self._transition(PAUSED)

    def resume(self) -> None:
        """Continue a paused print."""
        if self._state != PAUSED:
            raise InvalidTransition(f"Can't resume from {self._state.value}")
        self._transition(PRINTING)

    # --- e-stop / checkpoint ------------------------------------------------

    def _not_resumable(self, reason: str) -> None:
        if self._state == STOPPED_RESUMABLE:
            self._transition(STOPPED)
        self._stop_reason = reason
        self._emit(StopEvent(resumable=False, reason=reason))
        self._emit(ErrorEvent(message=f"The print can't be resumed: {reason}"))

    def connection_lost(self) -> None:
        """The serial connection dropped. A running print is stopped for good:
        the board may have reset and lost its zero, so it can't be resumed."""
        if self._state not in ACTIVE:
            self.invalidate_checkpoint(CONNECTION_LOST)
            return
        logger.error("Connection lost during a print; stopping it")
        self._transition(STOPPED)
        self.flush_priority()
        self._not_resumable(CONNECTION_LOST)
        self._end_print("error")

    def invalidate_checkpoint(self, reason: str) -> None:
        """Drop the resume checkpoint, e.g. after something moved the plungers
        or changed the coordinate system."""
        if self._state != STOPPED_RESUMABLE:
            return
        logger.info("Resume checkpoint invalidated: %s", reason)
        self._not_resumable(reason)

    async def estop(self, end_reason: str = "estop") -> None:
        """Emergency stop: halt the queue and send M410 straight to the printer.

        M410 bypasses both queues and the in-flight line (Marlin's
        EMERGENCY_PARSER acts on it on arrival). If a print was running, the
        printer's position is then read back to find the line it stopped on,
        so the print can be resumed from there.
        `end_reason` is reported in the print_end event if a print was running.
        """
        was_active = self._state in ACTIVE
        if self._state not in (STOPPED, STOPPED_RESUMABLE):
            self._transition(STOPPED)  # the worker won't send another line
        self.flush_priority()
        try:
            await self._serial.emergency_write("M410")
        except SerialError:
            logger.error("Failed to send emergency stop (M410)")
            self._emit(PrinterEvent(connected=False, port=None))
            if was_active:
                self._not_resumable(CONNECTION_LOST)
                self._end_print(end_reason)
            return

        if not was_active:
            if not self.resumable:
                self._emit(StopEvent(resumable=False, reason=None))
            return
        await self._take_checkpoint()
        self._end_print(end_reason, self._checkpoint.line if self._checkpoint else None)

    async def _take_checkpoint(self) -> None:
        if self._tracker.error:
            self._not_resumable(self._tracker.error)
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

        located = locate_line(list(self._tracker.recent), self._lines, position)
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

        self._transition(STOPPED_RESUMABLE)
        self._checkpoint = Checkpoint(line=line, position=position, after=after, retract=retract)
        self._stop_reason = None
        logger.info("E-stop checkpoint: line %d (%s) at %s", line, self._lines[line], position)
        self._emit(StopEvent(resumable=True, reason=None, line=line))

    async def resume_from_stop(self) -> None:
        """Return to the e-stop checkpoint and continue the print from there.

        Raises NotResumable without a valid checkpoint, and SerialError if the
        return moves fail (the checkpoint is kept, so this can be retried).
        """
        checkpoint = self._checkpoint
        if self._state != STOPPED_RESUMABLE or checkpoint is None:
            raise NotResumable(self._stop_reason or "No stopped print to resume")
        await self.wait_until_sent()
        await self._serial.send_lines(build_resume_commands(checkpoint))
        if self._checkpoint is not checkpoint:
            # Invalidated while the return moves were being sent
            raise NotResumable(self._stop_reason or "The checkpoint was invalidated")

        # The tracker continues from where the resume commands actually left
        # the machine — the checkpoint's as-sent after-state.
        self._tracker.resume_at(checkpoint.line, checkpoint.after)
        self._next = checkpoint.line + 1
        self._transition(PRINTING)
        self._emit(PrintResumedEvent())
        self._emit_progress()
        self._task = asyncio.create_task(self._run(preamble=[], seed=False))

    # --- sending ------------------------------------------------------------

    async def _seed_sent_state(self) -> None:
        """Read the printer's actual position before sending anything, so the
        as-sent tracker has known axes from line one.

        Without this, a fully relative (G91) file would never establish an
        absolute reference, and every position would stay unknown. If the
        read fails or can't be parsed, the print still runs — it just won't
        be resumable, with the tracker's `error` explaining why.
        """
        try:
            reply = await self._serial.send("M114")
        except SerialError as exc:
            self._tracker.error = f"Couldn't read the printer's starting position: {exc}"
            return

        position = parse_m114(reply)
        if position is None:
            self._tracker.error = f"Couldn't parse the printer's starting position from {reply!r}"
            return
        self._tracker.seed(position)

    def _track_send(self, line: str, index: int | None) -> None:
        """Advance the as-sent position tracker for a line about to be sent.

        Called before the write completes: a line that times out waiting for
        "ok" may still have reached and been acted on by the printer, so it's
        still a candidate for where an e-stop actually landed.
        """
        before, after = self._tracker.track(line, index)
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
        self._emit(WarningEvent(message=(
            f"The {side} syringe ({axis}) is nearly empty: "
            f"{max(remaining, 0):.1f} of {travel:g} mm plunger travel left."
        )))

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

                if self._state not in ACTIVE:
                    break

                # Wait if paused
                await self._unpaused.wait()

                if self._state not in ACTIVE:
                    break

                if self._next >= len(self._lines):
                    self._transition(COMPLETED)
                    self._end_print("completed")
                    break

                index = self._next
                sent_line = scale_flow(self._lines[index], self._flow_rate / 100.0)
                self._track_send(sent_line, index=index)
                # On failure _next stays put: a timed-out line is re-sent on resume.
                if await self._send(sent_line, numbered=True):
                    self._next = index + 1
                    self._emit_progress()
                await asyncio.sleep(0)

        except Exception:
            logger.exception("Queue worker error")
        finally:
            if self._state in ACTIVE:
                # The worker crashed or was cancelled mid-print.
                self._transition(STOPPED)
                self._end_print("error")
            logger.info(
                "Queue worker finished: state=%s, sent=%d/%d",
                self._state.value,
                self._next,
                len(self._lines),
            )

    def _emit_progress(self) -> None:
        total = len(self._lines)
        remaining = None
        if self._time_estimate_s is not None and total:
            remaining = self._time_estimate_s * (1 - self._next / total)
        self._emit(ProgressEvent(lines_sent=self._next, lines_total=total, time_remaining_s=remaining))

    async def _send(self, line: str, numbered: bool = False) -> bool:
        """Send one line. Returns False if it failed and must not be counted."""
        try:
            await self._serial.send(line, numbered=numbered)
            return True
        except (SerialTimeout, ResendUnavailable) as exc:
            logger.error("No usable reply to %s: %s", line, exc)
            if self._state == PRINTING:
                self._transition(PAUSED)
            self._emit(ErrorEvent(message=f"{exc}. Print paused."))
            return False
        except SerialError:
            logger.error("Failed to send: %s", line)
            if self._state not in ACTIVE:
                # An e-stop, or connection_lost() via the disconnect
                # event, is already handling this print.
                return False
            self.connection_lost()
            self._emit(PrinterEvent(connected=False, port=None))
            return False
