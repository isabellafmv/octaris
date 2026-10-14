"""The printer as the app sees it: connection, calibration, the loaded print
and the print itself. Routers translate HTTP to calls on PrinterSession and
its exceptions back to HTTP (see main.py for the status codes)."""

from __future__ import annotations

import asyncio
import re
import secrets
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from backend.checkpoint import moved, parse_m114, travel_moves
from backend.config import Config
from backend.gcode_processor import (
    TRAVEL_FEED,
    Command,
    GcodeValidationError,
    MachineState,
    ProcessedGcode,
    as_uploaded,
    check_as_uploaded,
    is_relative_program,
    mirror_to_nozzles,
    parse,
    parse_words,
    process_gcode,
    render,
    scale_flow,
    simulate_states,
    step,
    validate,
)
from backend.history import PrintHistory
from backend.limits import (
    PLUNGER_AXES,
    LimitError,
    check_jog,
    check_move_to,
    check_path,
    check_plunger_travel,
    plunger_step,
    plunger_travel_needed,
    start_state,
)
from backend.printer import HEIGHT_AXES, NozzleSpec, PrinterProfile
from backend.queue_worker import InvalidTransition, PrintStatus, QueueWorker
from backend.schemas import (
    CalibrationEvent,
    Event,
    ExtrusionRateEvent,
    NozzleCalibration,
    PrinterEvent,
    PrinterProfileEvent,
    SensorTestResponse,
    Settings,
    SettingsEvent,
    Snapshot,
    StatusEvent,
)
from backend.serial_manager import SerialError, SerialManager, parse_temperatures
from backend.settings import SettingsStore
from backend.slicer import PrintSettings, slice_model
from backend.temperature import TargetError, TemperatureStore

# Where uploaded models are written for the slicer
DATA_DIR = Path(tempfile.gettempdir()) / "octaris"

# Steps/mm, sent on every connect: EEPROM is disabled on this board
STEPS_PER_MM = "M92 X800 Y800 Z800 A800 B800 C800"


def zero_axes(nozzle: NozzleSpec) -> frozenset[str]:
    """The axes a nozzle's zero sets: X/Y (the start point, set at the first
    selected nozzle) and the nozzle's own height motor. A nozzle is
    calibrated once all of them are zeroed."""
    return frozenset(("X", "Y", nozzle.height_axis))


def offset_unmeasured(profile: PrinterProfile) -> str:
    offsets = ", ".join(
        f"{n.name.lower()} {n.offset_x:g} mm in X" + (f", {n.offset_y:g} mm in Y" if n.offset_y else "")
        for n in profile.nozzles[1:]
    )
    first, second = profile.nozzles[0].name.lower(), profile.nozzles[-1].name.lower()
    return (
        f"Prints with several nozzles are disabled: the {profile.name}'s nozzle offsets "
        f"({offsets}) are placeholders that haven't been measured. Zero at the {first} "
        f"nozzle, jog until the {second} nozzle is over the same point, and read the X "
        f'distance. Set that nozzle\'s offset_x under "profiles" → "{profile.id}" in '
        f'config.json, set "nozzle_offset_measured": true there, and restart.'
    )


TEMPERATURE_OFF = "Temperature control is off — enable it in Setup"


class NotReady(Exception):
    """The request needs something that isn't there yet (a connection,
    calibration, a loaded file) or has invalid input."""


class Conflict(Exception):
    """The request isn't allowed in the printer's current state."""


class StaleProgram(Exception):
    """An edit was made to a program that is no longer the loaded one."""


class GcodeEditError(Exception):
    """An edited program failed a check. `line` is the 1-based line it
    failed on, if the problem is on one line."""

    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


@dataclass
class LoadedPrint:
    gcode: ProcessedGcode
    filename: str = "unknown"
    # The ids of the nozzles it prints with, left to right
    nozzles: tuple[str, ...] = ()
    source: Literal["stl", "gcode"] | None = None
    # The upload settings it was made with, recorded in the print history
    settings: PrintSettings = field(default_factory=lambda: PrintSettings())
    # Changed in the G-code editor since it was loaded
    edited: bool = False
    # Sent exactly as uploaded ("Needs changes" off), so an edit gets the
    # upload's check_as_uploaded instead of the processed print's validation
    as_uploaded: bool = False


class PrinterSession:
    def __init__(
        self,
        config: Config,
        serial: SerialManager,
        worker: QueueWorker,
        history: PrintHistory,
        publish: Callable[[dict[str, Any]], None],
        temperature: TemperatureStore,
        settings: SettingsStore,
    ):
        self.config = config
        # The printer: its nozzles, bed limits and build volume
        self.profile = config.profile
        self.settings = settings
        self.serial = serial
        self.worker = worker
        self.history = history
        self.temperature = temperature
        self._bus_publish = publish
        self.loaded: LoadedPrint | None = None
        # The axes zeroed with G92 since the printer was last (re)connected
        self.zeroed: set[str] = set()
        # The nozzles the last zero was set for, while no print is loaded
        self._calibration_nozzles: tuple[str, ...] = (self.profile.nozzles[0].id,)
        # Counts loads and edits, for program_id. The prefix tells this run's
        # ids from those of an earlier run of the backend.
        self._loads = 0
        self._run_id = secrets.token_hex(4)

    def _publish(self, event: Event) -> None:
        self._bus_publish(event.dump())

    @property
    def selected_nozzles(self) -> tuple[str, ...]:
        """The nozzles the loaded print uses or, with none loaded, the last
        calibration was for: ids, left to right."""
        return (self.loaded and self.loaded.nozzles) or self._calibration_nozzles

    def select_nozzles(self, nozzle_ids: Sequence[str] | None) -> tuple[str, ...]:
        """`nozzle_ids` checked against the profile and put left to right
        (None or empty: the profile's first nozzle). Raises NotReady for an
        unknown or repeated id."""
        try:
            return tuple(n.id for n in self.profile.select(nozzle_ids or self.profile.nozzle_ids[:1]))
        except ValueError as exc:
            raise NotReady(str(exc)) from exc

    def nozzles_calibrated(self) -> NozzleCalibration:
        return {n.id: zero_axes(n) <= self.zeroed for n in self.profile.nozzles}

    def calibrated_for(self, nozzle_ids: Sequence[str]) -> bool:
        """Every nozzle in `nozzle_ids` is zeroed (several: one step each)."""
        return all(zero_axes(n) <= self.zeroed for n in self.profile.select(nozzle_ids))

    @property
    def calibrated(self) -> bool:
        return self.calibrated_for(self.selected_nozzles)

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
            settings=self.settings.current,
            printer=self.profile,
            nozzles=list(self.selected_nozzles),
        )

    # --- printer profile --------------------------------------------------------

    def select_profile(self, profile_id: str) -> PrinterProfile:
        """Switch to another printer profile (until the backend restarts;
        config.json's printer_profile is the one it starts with). The
        calibration, any resume checkpoint and the loaded print are dropped:
        they were made for the old profile's nozzles.

        Raises NotReady for an unknown id and Conflict during a print."""
        profile = self.config.profiles.get(profile_id)
        if profile is None:
            raise NotReady(
                f"Unknown printer profile {profile_id!r} (there are {', '.join(self.config.profiles)})"
            )
        self._refuse_during_print()
        if profile is self.profile:
            return profile
        self.profile = profile
        self._calibration_nozzles = (profile.nozzles[0].id,)
        self.worker.invalidate_checkpoint("The printer profile was changed")
        if self.loaded is not None:
            self.loaded = None
            self._publish(StatusEvent(value="idle"))
        self.reset_calibration()
        self._publish(PrinterProfileEvent(printer=profile))
        return profile

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

    async def calibrate(self, nozzle: str | None = None, nozzles: Sequence[str] | None = None) -> str:
        """Zero one of the selected nozzles where it is; returns the G92 sent.

        X/Y are set at the first (leftmost) selected nozzle, over the
        print's start point. Each nozzle's height is zeroed with that nozzle
        lowered onto the bed:
          the first selected nozzle:  G92 X0 Y0 <height>0 <plunger>0
          each further one:           G92 <height>0 <plunger>0, after the
                                      first has set the start point.
        On the Printess 1.0: ["left"] G92 X0 Y0 Z0 B0; ["right"] G92 X0 Y0
        A0 C0; ["left", "right"] G92 X0 Y0 Z0 B0, then G92 A0 C0.
        `nozzles` defaults to the loaded print's (or the last calibration's),
        and `nozzle` to their first.
        """
        if self.worker.status == PrintStatus.PRINTING:
            raise Conflict("Pause the print first")
        if self.worker.status == PrintStatus.PAUSED:
            raise Conflict("Can't re-zero during a print")
        self._require_connection()

        ids = self.select_nozzles(nozzles) if nozzles is not None else self.selected_nozzles
        selected = self.profile.select(ids)
        spec = next((n for n in selected if n.id == (nozzle or ids[0])), None)
        if spec is None:
            raise NotReady(f"Nozzle {nozzle!r} isn't selected (selected: {', '.join(ids)})")
        axes = ["X", "Y"] if spec is selected[0] else []
        axes += [spec.height_axis, spec.plunger_axis]
        command = "G92 " + " ".join(f"{axis}0" for axis in axes)

        self.worker.invalidate_checkpoint("The printer was re-zeroed")
        await self.serial.send(command)
        self.zeroed.update(axes)
        self._calibration_nozzles = ids
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
        plungers = [n.plunger_axis for n in self.profile.nozzles]
        if axis not in ("X", "Y", *self._height_axes(), *plungers):
            raise NotReady(f"Invalid axis: {axis}")
        if self.worker.status == PrintStatus.PRINTING:
            raise Conflict("Pause the print first")
        self._require_connection()
        await self._check_jog_limits(axis, distance)

        if axis in plungers:
            self.worker.invalidate_checkpoint(f"The {axis} plunger was jogged")
        self.worker.manual_command()
        # Leave the printer in the mode it was in, except that it never goes
        # back to G90 while a print is loaded: every print runs in G91, and
        # a G90 slipped in before its next line would make that absolute.
        restore = [] if self.serial.relative or self.loaded is not None else ["G90"]
        await self.serial.send_lines(["G91", f"G1 {axis}{distance} F{feed_rate}", *restore])
        return axis

    def _height_axes(self, nozzle_ids: Sequence[str] | None = None) -> list[str]:
        """The height axes of `nozzle_ids` (default: every nozzle), left to right."""
        nozzles = self.profile.nozzles if nozzle_ids is None else self.profile.select(nozzle_ids)
        return [n.height_axis for n in nozzles]

    async def _check_jog_limits(self, axis: str, distance: float) -> None:
        """Only once that axis is zeroed: before the G92 zero the bed's
        position is unknown, and the nozzle has to be jogged freely to find
        it. Every nozzle's height has the bed's z limits."""
        if axis not in ("X", "Y", *self._height_axes()) or axis not in self.zeroed:
            return
        current = self.serial.position[axis]
        if current is None:
            await self.serial.send("M114")  # the reply updates serial.position
            current = self.serial.position[axis]
        if current is None:
            raise SerialError(f"Couldn't read the printer's {axis} position to check the bed limits")
        check_jog(self.profile.bed, axis, current, distance)

    async def go_to_origin(self, lower: bool = False) -> list[str]:
        """Travel to X0 Y0 at the current height and, with `lower`, then
        lower the height axes of the selected nozzles to 0 — onto the bed.
        Returns the lines sent, as one sequence.

        Raises Conflict while printing, NotReady if `lower` and those
        height axes aren't zeroed (their 0 would be wherever the printer
        powered up), and LimitError if the origin is off the bed.
        """
        if self.worker.status == PrintStatus.PRINTING:
            raise Conflict("Pause the print first")
        self._require_connection()
        heights = self._height_axes(self.selected_nozzles)
        if lower and not set(heights) <= self.zeroed:
            raise NotReady("Zero the nozzle height first: lowering to an unset zero could crash the nozzle")
        # As for jogs, the bed limits only apply once an axis is zeroed
        for axis in ["X", "Y", *(heights if lower else [])]:
            if axis in self.zeroed:
                check_move_to(self.profile.bed, axis, 0.0)

        self.worker.manual_command()
        lines = ["G90", f"G1 X0 Y0 F{TRAVEL_FEED}"]
        if lower:
            lines.append("G1 " + " ".join(f"{axis}0" for axis in heights) + f" F{TRAVEL_FEED}")
        # Back to the mode the printer was in; G91 while a print is loaded,
        # as every print runs relative (see jog)
        if self.serial.relative or self.loaded is not None:
            lines.append("G91")
        await self.serial.send_lines(lines)
        return lines

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
        reply = await self.serial.send(line)
        # printess: a channel target typed by hand counts as one set here
        self.temperature.note_command(line)
        return reply

    async def set_temperature_target(self, sensor: str, target: float) -> str:
        """Set a heater's target (0: off); returns the command sent.

        Allowed while printing, unlike other manual commands: it moves
        nothing, and M104/M140/M141 return at once (M109/M190, which wait
        for the temperature, are never sent). It goes out between two print
        lines. With the lab firmware (printess) it is M104 I<n> T<°C> H|C.
        """
        try:
            plan = self.temperature.plan_target(sensor, target)
        except TargetError as exc:
            raise NotReady(str(exc)) from exc
        self._require_connection()
        await self.serial.send(plan.command)
        self.temperature.target_sent(plan)
        return plan.command

    async def test_sensors(self) -> SensorTestResponse:
        """Send one M105 (whether or not temperature control is on) and
        return its raw reply, None if none came, with the sensors it lists."""
        self._require_connection()
        reply = await self.serial.test_sensors()
        found: dict[str, dict[str, float | None]] = {}
        for line in (reply or "").splitlines():
            found.update(parse_temperatures(line) or {})
        return SensorTestResponse(reply=reply, sensors=self.temperature.test_readings(found))

    def retry_temperature(self) -> None:
        """Start temperature monitoring again after polling paused."""
        if not self.settings.current.temperature_sensors:
            raise NotReady(TEMPERATURE_OFF)
        self._require_connection()
        self.serial.retry_temperature()

    # --- settings -----------------------------------------------------------------

    def update_settings(self, settings: Settings) -> Settings:
        """Save `settings` and apply them at once (no reconnect needed).
        Raises OSError if they can't be saved."""
        old = self.settings.current
        self.settings.save(settings)
        if settings.temperature_sensors != old.temperature_sensors:
            self.serial.set_temperature_enabled(settings.temperature_sensors)
            self.temperature.enabled_changed()
        self._publish(SettingsEvent(**settings.model_dump()))
        return settings

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
        self, filename: str, content: bytes, nozzles: Sequence[str] | None, settings: PrintSettings
    ) -> ProcessedGcode:
        """Slice an STL/3MF model for the `nozzles` (ids) and load the result.

        Raises NotReady for an unknown nozzle, SlicingError or
        GcodeValidationError if slicing fails, and LimitError if the print
        would leave the bed.
        """
        ids = self.select_nozzles(nozzles)
        self._publish(StatusEvent(value="slicing"))
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        model_path = DATA_DIR / filename
        model_path.write_bytes(content)
        try:
            gcode = await slice_model(
                model_path,
                self.profile.select(ids),
                **settings,
                max_feed=self.config.max_feed_mm_min,
                build_volume=self.profile.build_volume,
            )
            check_path(self.profile.bed, gcode.lines, start_state(gcode.start_position), gcode.height_axes)
        except Exception:
            self._publish(StatusEvent(value="idle"))
            raise
        self._load(LoadedPrint(gcode, filename, ids, "stl", settings))
        return gcode

    def load_gcode(
        self, filename: str, raw: str, nozzles: Sequence[str] | None, needs_changes: bool = True
    ) -> ProcessedGcode:
        """Load an uploaded G-code file for the `nozzles` (ids): post-processed
        if it `needs_changes`, otherwise exactly as uploaded, with warnings
        for what may print differently than intended.

        Raises NotReady for an unknown nozzle, and GcodeValidationError or
        LimitError if it can't be printed.
        """
        ids = self.select_nozzles(nozzles)
        selected = self.profile.select(ids)
        max_feed = self.config.max_feed_mm_min
        cmds = parse(raw.splitlines())
        # A file kept as it is (as uploaded, or a relative lab file) and
        # written for one nozzle prints from every selected nozzle
        kept = not needs_changes or is_relative_program(cmds)
        notes: list[str] = []
        if len(selected) > 1 and kept:
            cmds, note = mirror_to_nozzles(cmds, selected)
            if note:
                raw = "\n".join(render(cmds))
                notes.append(note)
        if needs_changes:
            gcode = process_gcode(raw, selected, max_feed=max_feed)
            gcode.warnings[:0] = notes
        else:
            gcode = as_uploaded(raw, warnings=[*notes, *check_as_uploaded(cmds, max_feed)])
        # A converted print is checked from the zero point it starts at; a
        # lab file starts wherever the head is, so only at print start.
        check_path(self.profile.bed, gcode.lines, start_state(gcode.start_position), gcode.height_axes)
        self._load(LoadedPrint(gcode, filename, ids, "gcode", as_uploaded=not needs_changes))
        return gcode

    def _load(self, loaded: LoadedPrint, reason: str = "A new file was loaded") -> None:
        self.worker.invalidate_checkpoint(reason)
        self.loaded = loaded
        self._loads += 1
        self._publish(StatusEvent(value="ready"))

    @property
    def program_id(self) -> str | None:
        """Identifies the loaded program's content: changes on every load
        and edit. None while nothing is loaded."""
        return f"{self._run_id}-{self._loads}" if self.loaded else None

    # --- editing the loaded print -------------------------------------------------

    async def replace_loaded_lines(self, text: str, program_id: str | None = None) -> ProcessedGcode:
        """Replace the loaded print's lines with `text`, an edited copy of
        them (as GET /gcode/loaded sends them, one line per line).

        The edit gets the checks a loaded print gets (validation, or for a
        file sent as uploaded check_as_uploaded with its warnings recomputed;
        the bed limits from the print's start point) and the syringe travel
        check (start_print repeats it with the flow override). The print
        keeps its filename, nozzles and start point, and is marked edited.

        Raises Conflict while a print is printing or paused, NotReady if
        nothing is loaded, StaleProgram if `program_id` is given and isn't
        the loaded program's, and GcodeEditError if the edit fails a check.
        """
        loaded = self._editable(program_id)
        # Half a million lines take a few seconds to check: off the event loop
        gcode = await asyncio.to_thread(self._check_edit, loaded, split_lines(text))
        if self._editable(program_id) is not loaded:
            raise StaleProgram("Another file was loaded while the edit was checked")
        self._load(replace(loaded, gcode=gcode, edited=True), "The loaded program was edited")
        return gcode

    def _editable(self, program_id: str | None) -> LoadedPrint:
        if self.worker.print_active:
            raise Conflict("Stop the print before editing its G-code")
        if self.loaded is None:
            raise NotReady("No print loaded")
        if program_id is not None and program_id != self.program_id:
            raise StaleProgram("The loaded program has changed since the editor opened it")
        return self.loaded

    def _check_edit(self, loaded: LoadedPrint, lines: list[str]) -> ProcessedGcode:
        old = loaded.gcode
        cmds = parse(lines)
        max_feed = self.config.max_feed_mm_min
        warnings: list[str] = []
        if loaded.as_uploaded:
            # Held to the upload's check: only an empty file or one without
            # moves fails, the rest is warned about
            try:
                warnings = check_as_uploaded(cmds, max_feed)
            except GcodeValidationError as exc:
                raise GcodeEditError(str(exc)) from exc
        else:
            try:
                validate(cmds, max_feed)
            except GcodeValidationError as exc:
                raise GcodeEditError(str(exc), _line_in(str(exc)) or _first_command_line(cmds)) from exc

        # From where the print was planned to start: its zero point, or for
        # a file kept as uploaded, unknown
        before, after = simulate_states(lines, old.state_before[0] if old.state_before else None)
        gcode = replace(old, lines=lines, state_before=before, state_after=after, warnings=warnings)
        if old.start_position is None:
            # As process_gcode derives them for a lab file, in one pass
            moved = {w.letter for c in cmds if c.is_move for w in c.words}
            gcode.extrusion_axes = tuple(ax for ax in PLUNGER_AXES if ax in moved)
            gcode.height_axes = tuple(ax for ax in HEIGHT_AXES if ax in moved) or ("Z",)

        try:
            check_path(self.profile.bed, lines, start_state(gcode.start_position), gcode.height_axes)
        except LimitError as exc:
            raise GcodeEditError(str(exc), _line_in(str(exc))) from exc

        # Each plunger counts from where it is, a full syringe
        plungers = start_state({**(gcode.start_position or {}), **dict.fromkeys(PLUNGER_AXES, 0.0)})
        try:
            check_plunger_travel(plunger_travel_needed(lines, plungers), self.config.syringe_travel_mm)
        except LimitError as exc:
            line = _plunger_overrun_line(lines, plungers, self.config.syringe_travel_mm)
            raise GcodeEditError(str(exc), line) from exc
        return gcode

    # --- printing ---------------------------------------------------------------

    async def start_print(self, wait_for_temperature: bool = False, from_origin: bool = False) -> int:
        """Start the loaded print; returns its line count. Raises LimitError
        if it would leave the bed or run a syringe empty. With
        `wait_for_temperature`, its first line waits until every sensor
        with a target has held it (see QueueWorker.start). With
        `from_origin` (Restart), a file sent as uploaded also starts from
        the zero point: the nozzle lifts, travels to X0 Y0 and lowers to
        height 0 first, as a converted print always does."""
        worker = self.worker
        loaded = self.loaded
        if loaded is None:
            raise NotReady("No G-code loaded. Upload an STL first.")
        if worker.print_active:
            raise Conflict("A print is already running")
        if wait_for_temperature and not self.settings.current.temperature_sensors:
            raise NotReady("Can't wait for temperatures: temperature control is off — enable it in Setup")
        await worker.wait_until_sent()
        self._require_connection()
        ids = loaded.nozzles or self._calibration_nozzles
        selected = self.profile.select(ids)
        if not self.calibrated_for(ids):
            missing = [n.name.lower() for n in selected if not zero_axes(n) <= self.zeroed]
            steps = "with /calibration/zero" if len(missing) == 1 else f"{len(missing)} steps"
            raise NotReady(
                f"Printer not calibrated: zero the {' and '.join(missing)} nozzle ({steps}) first. "
                f"Jog it to the start point, lower it onto the bed, and zero it."
            )
        if len(selected) > 1 and not self.profile.nozzle_offset_measured:
            raise NotReady(offset_unmeasured(self.profile))

        # The printer's actual position, so relative moves can be checked too.
        # Also seeds the worker's as-sent tracker (see QueueWorker.start).
        reply = await self.serial.send("M114")
        position = parse_m114(reply)
        if position is None:
            raise SerialError(f"Couldn't read the printer's position (M114 replied {reply!r})")

        # A converted print's moves are relative to its zero point: if the
        # head isn't there, it travels back first.
        gcode = loaded.gcode
        target = gcode.start_position
        if target is None and from_origin:
            origin_axes = ["X", "Y", *gcode.height_axes]
            unzeroed = [axis for axis in origin_axes if axis not in self.zeroed]
            if unzeroed:
                raise NotReady(
                    f"Can't return to the zero point: {', '.join(unzeroed)} not zeroed. "
                    "Zero the nozzle first."
                )
            target = dict.fromkeys(origin_axes, 0.0)
        travel: list[str] = []
        if target is not None:
            if moved(position, target):
                travel = travel_moves(position, target)
                check_path(self.profile.bed, travel, start_state(position), gcode.height_axes)
            position = {**position, **target}

        # Checked before load_gcode, which would drop a resumable checkpoint.
        check_path(self.profile.bed, gcode.lines, start_state(position), gcode.height_axes)
        # As it will be sent: the flow override scales every B/C value.
        lines = [scale_flow(line, worker.flow_rate / 100.0) for line in gcode.lines]
        needed = plunger_travel_needed(lines, start_state(position))
        check_plunger_travel(needed, self.config.syringe_travel_mm)

        # The checks above were of `loaded`: an edit may have replaced it since
        if self.loaded is not loaded:
            raise Conflict("The loaded program changed while the print was starting")
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
            syringe_config=",".join(ids),
            total_lines=worker.lines_total,
            source=loaded.source,
            settings=loaded.settings,
            edited=loaded.edited,
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


def split_lines(text: str) -> list[str]:
    """Split as the editor counts lines (str.splitlines also splits on form
    feeds and other separators, which would shift the line numbers)."""
    return re.split(r"\r\n|\r|\n", text)


# How check_path ("line 12 (...)") and validate ("Line 12: ...") name the line
_LINE_NUMBER = re.compile(r"\b[Ll]ine (\d+)\b")


def _line_in(message: str) -> int | None:
    match = _LINE_NUMBER.search(message)
    return int(match.group(1)) if match else None


def _first_command_line(cmds: list[Command]) -> int | None:
    return next((i for i, cmd in enumerate(cmds, start=1) if cmd.code), None)


def _plunger_overrun_line(lines: list[str], start: MachineState, available_mm: float) -> int | None:
    """The first line that pushes a plunger past `available_mm`."""
    state = start
    displacement = dict.fromkeys(PLUNGER_AXES, 0.0)
    for number, line in enumerate(lines, start=1):
        after = step(state, line)
        for axis, delta in plunger_step(state, after, line).items():
            displacement[axis] += delta
            if -displacement[axis] > available_mm:
                return number
        state = after
    return None


def _invalidates_checkpoint(line: str) -> str | None:
    """Why a manual line would make an e-stop checkpoint unusable, if it does."""
    words = parse_words(line)
    if not words or words[0][0] != "G":
        return None
    if words[0][1] == 92:
        return "G92 changed the coordinate system"
    if words[0][1] in (0, 1) and any(letter in PLUNGER_AXES for letter, _ in words[1:]):
        return "A plunger was moved manually"
    return None
