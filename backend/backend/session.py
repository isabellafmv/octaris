"""The printer as the app sees it: connection, calibration, the loaded print
and the print itself. Routers translate HTTP to calls on PrinterSession and
its exceptions back to HTTP (see main.py for the status codes)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from backend.checkpoint import moved, parse_m114, travel_moves
from backend.config import Config
from backend.gcode_processor import (
    NOZZLE_OFFSET_X,
    ProcessedGcode,
    SyringeMode,
    parse_words,
    process_gcode,
    scale_flow,
)
from backend.history import PrintHistory
from backend.limits import (
    check_jog,
    check_path,
    check_plunger_travel,
    plunger_travel_needed,
    start_state,
)
from backend.queue_worker import InvalidTransition, PrintStatus, QueueWorker
from backend.schemas import (
    CalibrationEvent,
    Event,
    ExtrusionRateEvent,
    NozzleCalibration,
    PrinterEvent,
    Snapshot,
    StatusEvent,
)
from backend.serial_manager import SerialError, SerialManager
from backend.slicer import PrintSettings, slice_model
from backend.temperature import TargetError, TemperatureStore

# Where uploaded models are written for the slicer
DATA_DIR = Path(tempfile.gettempdir()) / "octaris"

JOG_AXES = ("X", "Y", "Z", "A", "B", "C")

Nozzle = Literal["left", "right"]

# The axes a nozzle's zero sets: X/Y (the start point, always referenced to
# the left nozzle) and the nozzle's own height motor, Z left or A right. A
# nozzle is calibrated once all of them are zeroed.
NOZZLE_ZERO_AXES: dict[Nozzle, frozenset[str]] = {
    "left": frozenset("XYZ"),
    "right": frozenset("XYA"),
}
# The nozzles each syringe mode prints with
MODE_NOZZLES: dict[SyringeMode, tuple[Nozzle, ...]] = {
    "left": ("left",),
    "right": ("right",),
    "both": ("left", "right"),
}

# Steps/mm, sent on every connect: EEPROM is disabled on this board
STEPS_PER_MM = "M92 X800 Y800 Z800 A800 B800 C800"

NOZZLE_OFFSET_UNMEASURED = (
    f"Right-nozzle and dual prints are disabled: the nozzle offset "
    f"(NOZZLE_OFFSET_X = {NOZZLE_OFFSET_X:g} mm) is a placeholder that hasn't been "
    f"measured. Zero at the left nozzle, jog until the right nozzle is over the "
    f"same point, and read the X distance. Set NOZZLE_OFFSET_X in "
    f"backend/backend/gcode_processor.py to it, then set "
    f'"nozzle_offset_measured": true in config.json and restart.'
)


class NotReady(Exception):
    """The request needs something that isn't there yet (a connection,
    calibration, a loaded file) or has invalid input."""


class Conflict(Exception):
    """The request isn't allowed in the printer's current state."""


@dataclass
class LoadedPrint:
    gcode: ProcessedGcode
    filename: str = "unknown"
    mode: SyringeMode = "left"
    source: Literal["stl", "gcode"] | None = None
    # The upload settings it was made with, recorded in the print history
    settings: PrintSettings = field(default_factory=lambda: PrintSettings())


class PrinterSession:
    def __init__(
        self,
        config: Config,
        serial: SerialManager,
        worker: QueueWorker,
        history: PrintHistory,
        publish: Callable[[dict[str, Any]], None],
        temperature: TemperatureStore,
    ):
        self.config = config
        self.serial = serial
        self.worker = worker
        self.history = history
        self.temperature = temperature
        self._bus_publish = publish
        self.loaded: LoadedPrint | None = None
        # The axes zeroed with G92 since the printer was last (re)connected
        self.zeroed: set[str] = set()
        # The syringe mode the last zero was set for, while no print is loaded
        self._calibration_mode: SyringeMode = "left"

    def _publish(self, event: Event) -> None:
        self._bus_publish(event.dump())

    @property
    def syringe_mode(self) -> SyringeMode:
        return self.loaded.mode if self.loaded else self._calibration_mode

    def nozzles_calibrated(self) -> NozzleCalibration:
        return NozzleCalibration(
            left=NOZZLE_ZERO_AXES["left"] <= self.zeroed,
            right=NOZZLE_ZERO_AXES["right"] <= self.zeroed,
        )

    def calibrated_for(self, mode: SyringeMode) -> bool:
        """Every nozzle the mode prints with is zeroed (both: in two steps)."""
        return all(NOZZLE_ZERO_AXES[nozzle] <= self.zeroed for nozzle in MODE_NOZZLES[mode])

    @property
    def calibrated(self) -> bool:
        return self.calibrated_for(self.syringe_mode)

    def snapshot(self) -> Snapshot:
        """Current connection/print state, for GET /status and the ws snapshot."""
        worker = self.worker
        return Snapshot(
            printer_connected=self.serial.is_connected,
            port=self.serial.port,
            print_status=worker.status.value,
            lines_sent=worker.lines_sent,
            lines_total=worker.lines_total,
            calibrated=self.calibrated,
            calibrated_nozzles=self.nozzles_calibrated(),
            flow_rate=worker.flow_rate,
            resumable=worker.resumable,
            stop_reason=worker.stop_reason,
            time_estimate_s=self.loaded.gcode.time_estimate_s if self.loaded else None,
        )

    # --- connection -----------------------------------------------------------

    def _refuse_during_print(self) -> None:
        # (Re)opening or closing the port can reset the board mid-print.
        if self.worker.print_active:
            raise Conflict("Stop the print first")

    async def connect(self, port: str) -> None:
        """Open `port`. Raises SerialError if it can't be opened."""
        self._refuse_during_print()
        await self.serial.connect(port, self.config.baud_rate)
        self.worker.invalidate_checkpoint("The printer was reconnected")
        try:
            await self.serial.send(STEPS_PER_MM)
        except SerialError:
            pass  # non-fatal — printer still usable
        # on_event() resets the calibration
        self._publish(PrinterEvent(connected=True, port=port))

    async def disconnect(self) -> None:
        self._refuse_during_print()
        await self.serial.disconnect()
        self.worker.invalidate_checkpoint("The printer disconnected")
        self._publish(PrinterEvent(connected=False, port=None))

    def on_event(self, event: dict[str, Any]) -> None:
        """Reacts to the printer connecting or disconnecting, however it
        happened: by connect()/disconnect(), or by the serial manager
        losing or reopening the port by itself."""
        if event["type"] != "printer":
            return
        if not event["connected"]:
            # Stops a running print for good (the board may reset on reconnect).
            self.worker.connection_lost()
        # Opening or losing the port may reset the board, losing the G92 zero.
        self.reset_calibration()

    def can_reconnect(self) -> bool:
        # Never reconnect automatically during a print; see on_event().
        # A line the worker queued before the connection dropped must not
        # reopen the port either, hence `sending`.
        return not (self.worker.print_active or self.worker.sending)

    def list_ports(self) -> list[dict[str, str]]:
        return self.serial.list_ports()

    def _require_connection(self) -> None:
        if not self.serial.is_connected:
            raise NotReady("Printer not connected")

    # --- calibration ------------------------------------------------------------

    async def calibrate(self, nozzle: Nozzle | None = None, mode: SyringeMode | None = None) -> str:
        """Zero one nozzle where it is; returns the G92 sent.

        X/Y are always referenced to the LEFT nozzle (its position at the
        print's start point): the right nozzle's offset (NOZZLE_OFFSET_X) is
        applied in post-processing. Each nozzle's height is zeroed with that
        nozzle lowered onto the bed:
          left:          G92 X0 Y0 Z0 B0
          right, right mode: G92 X0 Y0 A0 C0
          right, both mode:  G92 A0 C0 — the second step, after the left
                             nozzle has set the start point.
        `mode` defaults to the loaded print's (or the last calibration's),
        and `nozzle` to the mode's first.
        """
        if self.worker.status == PrintStatus.PRINTING:
            raise Conflict("Pause the print first")
        if self.worker.status == PrintStatus.PAUSED:
            raise Conflict("Can't re-zero during a print")
        self._require_connection()

        mode = mode or self.syringe_mode
        nozzle = nozzle or MODE_NOZZLES[mode][0]
        if nozzle == "left":
            axes = ["X", "Y", "Z", "B"]
        elif mode == "both":
            axes = ["A", "C"]
        else:
            axes = ["X", "Y", "A", "C"]
        command = "G92 " + " ".join(f"{axis}0" for axis in axes)

        self.worker.invalidate_checkpoint("The printer was re-zeroed")
        await self.serial.send(command)
        self.zeroed.update(axes)
        self._calibration_mode = mode
        self._publish_calibration()
        return command

    def reset_calibration(self) -> None:
        """Mark calibration as invalid (e.g. after a disconnect or power cycle)."""
        self.zeroed.clear()
        self._publish_calibration()

    def _publish_calibration(self) -> None:
        value: Literal["calibrated", "uncalibrated"] = "calibrated" if self.calibrated else "uncalibrated"
        self._publish(CalibrationEvent(value=value, nozzles=self.nozzles_calibrated()))

    # --- manual control -----------------------------------------------------------

    async def jog(self, axis: str, distance: float, feed_rate: float) -> str:
        """Move one axis by `distance`; returns the axis, upper-cased.
        Raises LimitError if the move would leave the bed."""
        axis = axis.upper()
        if axis not in JOG_AXES:
            raise NotReady(f"Invalid axis: {axis}")
        if self.worker.status == PrintStatus.PRINTING:
            raise Conflict("Pause the print first")
        self._require_connection()
        await self._check_jog_limits(axis, distance)

        if axis in ("B", "C"):
            self.worker.invalidate_checkpoint(f"The {axis} plunger was jogged")
        self.worker.manual_command()
        # Leave the printer in the mode it was in, except that it never goes
        # back to G90 while a print is loaded: every print runs in G91, and
        # a G90 slipped in before its next line would make that absolute.
        restore = [] if self.serial.relative or self.loaded is not None else ["G90"]
        await self.serial.send_lines(["G91", f"G1 {axis}{distance} F{feed_rate}", *restore])
        return axis

    async def _check_jog_limits(self, axis: str, distance: float) -> None:
        """Only once that axis is zeroed: before the G92 zero the bed's
        position is unknown, and the nozzle has to be jogged freely to find
        it. A (the right nozzle's height) has the same limits as Z."""
        if axis not in ("X", "Y", "Z", "A") or axis not in self.zeroed:
            return
        current = self.serial.position[axis]
        if current is None:
            await self.serial.send("M114")  # the reply updates serial.position
            current = self.serial.position[axis]
        if current is None:
            raise SerialError(f"Couldn't read the printer's {axis} position to check the bed limits")
        check_jog(self.config.bed, axis, current, distance)

    async def send_gcode(self, line: str) -> str:
        """Send one raw line and return the printer's reply."""
        if self.worker.status == PrintStatus.PRINTING:
            raise Conflict("Pause the print first")
        self._require_connection()
        line = line.strip()
        if not line:
            raise NotReady("Empty G-code line")
        if self.worker.status == PrintStatus.PAUSED and line.upper().startswith("G92"):
            raise Conflict("Can't re-zero during a print")

        reason = _invalidates_checkpoint(line)
        if reason:
            self.worker.invalidate_checkpoint(reason)
        self.worker.manual_command()
        return await self.serial.send(line)

    async def set_temperature_target(self, sensor: str, target: float) -> str:
        """Set a heater's target (0: off); returns the command sent.

        Allowed while printing, unlike other manual commands: it moves
        nothing, and M104/M140/M141 return at once (M109/M190, which wait
        for the temperature, are never sent). It goes out between two print
        lines.
        """
        try:
            command = self.temperature.check_target(sensor, target)
        except TargetError as exc:
            raise NotReady(str(exc)) from exc
        self._require_connection()
        await self.serial.send(command)
        return command

    def serial_log(self, limit: int) -> list[dict[str, Any]]:
        entries = self.serial.log_buffer
        return entries[-limit:] if limit > 0 else entries

    def print_history(self, limit: int) -> list[dict[str, Any]]:
        return self.history.list(limit)

    def set_flow_rate(self, rate: int) -> None:
        if rate < 50 or rate > 150:
            raise NotReady("Rate must be between 50 and 150")
        self.worker.set_flow_rate(rate)
        # No-op unless a print session is active
        self.history.log_extrusion(rate, self.worker.lines_sent)
        self._publish(ExtrusionRateEvent(value=rate))

    # --- loading a print --------------------------------------------------------

    async def load_model(
        self, filename: str, content: bytes, mode: SyringeMode, settings: PrintSettings
    ) -> ProcessedGcode:
        """Slice an STL/3MF model and load the result.

        Raises SlicingError or GcodeValidationError if slicing fails, and
        LimitError if the print would leave the bed.
        """
        self._publish(StatusEvent(value="slicing"))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        model_path = DATA_DIR / filename
        model_path.write_bytes(content)
        try:
            gcode = await slice_model(model_path, mode, **settings)
            check_path(self.config.bed, gcode.lines, start_state(gcode.start_position), gcode.height_axes)
        except Exception:
            self._publish(StatusEvent(value="idle"))
            raise
        self._load(LoadedPrint(gcode, filename, mode, "stl", settings))
        return gcode

    def load_gcode(self, filename: str, raw: str, mode: SyringeMode) -> ProcessedGcode:
        """Post-process and load an uploaded G-code file.

        Raises GcodeValidationError or LimitError if it can't be printed.
        """
        gcode = process_gcode(raw, mode)
        # A converted print is checked from the zero point it starts at; a
        # lab file starts wherever the head is, so only at print start.
        check_path(self.config.bed, gcode.lines, start_state(gcode.start_position), gcode.height_axes)
        self._load(LoadedPrint(gcode, filename, mode, "gcode"))
        return gcode

    def _load(self, loaded: LoadedPrint) -> None:
        self.worker.invalidate_checkpoint("A new file was loaded")
        self.loaded = loaded
        self._publish(StatusEvent(value="ready"))

    # --- printing ---------------------------------------------------------------

    async def start_print(self, wait_for_temperature: bool = False) -> int:
        """Start the loaded print; returns its line count. Raises LimitError
        if it would leave the bed or run a syringe empty. With
        `wait_for_temperature`, its first line waits until every sensor
        with a target has held it (see QueueWorker.start)."""
        worker = self.worker
        loaded = self.loaded
        if loaded is None:
            raise NotReady("No G-code loaded. Upload an STL first.")
        if worker.print_active:
            raise Conflict("A print is already running")
        await worker.wait_until_sent()
        self._require_connection()
        if not self.calibrated_for(loaded.mode):
            missing = [n for n in MODE_NOZZLES[loaded.mode] if not NOZZLE_ZERO_AXES[n] <= self.zeroed]
            raise NotReady(
                f"Printer not calibrated: zero the {' and '.join(missing)} nozzle "
                f"({'both steps' if len(missing) == 2 else 'with /calibration/zero'}) first. "
                f"Jog it to the start point, lower it onto the bed, and zero it."
            )
        if loaded.mode in ("right", "both") and not self.config.nozzle_offset_measured:
            raise NotReady(NOZZLE_OFFSET_UNMEASURED)

        # The printer's actual position, so relative moves can be checked too.
        # Also seeds the worker's as-sent tracker (see QueueWorker.start).
        reply = await self.serial.send("M114")
        position = parse_m114(reply)
        if position is None:
            raise SerialError(f"Couldn't read the printer's position (M114 replied {reply!r})")

        # A converted print's moves are relative to its zero point: if the
        # head isn't there, it travels back first.
        gcode = loaded.gcode
        travel: list[str] = []
        if gcode.start_position is not None:
            if moved(position, gcode.start_position):
                travel = travel_moves(position, gcode.start_position)
                check_path(self.config.bed, travel, start_state(position), gcode.height_axes)
            position = {**position, **gcode.start_position}

        # Checked before load_gcode, which would drop a resumable checkpoint.
        check_path(self.config.bed, gcode.lines, start_state(position), gcode.height_axes)
        # As it will be sent: the flow override scales every B/C value.
        lines = [scale_flow(line, worker.flow_rate / 100.0) for line in gcode.lines]
        needed = plunger_travel_needed(lines, start_state(position))
        check_plunger_travel(needed, self.config.syringe_travel_mm)

        # Raises InvalidTransition if another start got in while M114 was read.
        worker.load_gcode(
            gcode.lines,
            time_estimate_s=gcode.time_estimate_s,
            state_before=gcode.state_before or None,
            state_after=gcode.state_after or None,
            extrusion_axes=gcode.extrusion_axes,
            height_axes=gcode.height_axes,
            pressurize_mm=gcode.pressurize_mm,
        )
        if travel:
            await self.serial.send_lines(travel)
        self.history.start(
            filename=loaded.filename,
            syringe_config=loaded.mode,
            total_lines=worker.lines_total,
            source=loaded.source,
            settings=loaded.settings,
        )
        worker.start(start_position=position, wait_for_temperature=wait_for_temperature)
        return worker.lines_total

    async def stop(self) -> tuple[bool, str | None]:
        """Stop any print; returns whether it can be resumed, and if not, why."""
        await self.worker.estop()
        return self.worker.resumable, self.worker.stop_reason

    def pause(self) -> None:
        try:
            self.worker.pause()
        except InvalidTransition as exc:
            raise Conflict("No running print to pause") from exc

    async def resume(self) -> None:
        """Continue a paused print, or a stopped one from its checkpoint.
        Raises NotResumable if the stop can't be resumed."""
        if self.worker.status == PrintStatus.PAUSED:
            self.worker.resume()
            return
        if self.worker.status != PrintStatus.STOPPED:
            raise Conflict("No paused or stopped print to resume")
        self.history.resuming()
        try:
            await self.worker.resume_from_stop()
        except BaseException:
            self.history.resume_failed()
            raise


def _invalidates_checkpoint(line: str) -> str | None:
    """Why a manual line would make an e-stop checkpoint unusable, if it does."""
    words = parse_words(line)
    if not words or words[0][0] != "G":
        return None
    if words[0][1] == 92:
        return "G92 changed the coordinate system"
    if words[0][1] in (0, 1) and any(letter in ("B", "C") for letter, _ in words[1:]):
        return "A plunger was moved manually"
    return None
