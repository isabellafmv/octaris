"""Every WebSocket event and REST response, as Pydantic models.

The client's TypeScript types are generated from the OpenAPI schema, which
includes the event models too (see backend/openapi.py and the client's
`gen:types` script).
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.printer import NozzleFields, PrinterProfile

# The worker's print status, plus the upload states the client also shows
PrintStatus = Literal["idle", "slicing", "ready", "printing", "paused", "stopped", "completed"]
# "estop" only appears in history recorded before /print/estop merged into /print/stop
EndReason = Literal["completed", "stopped", "estop", "error"]
# "off": temperature control is off in the settings (the printer has no
# sensors). "waiting": connected, no temperature report yet. "no_sensors":
# connected, and none came in the time one should have.
TemperatureState = Literal["off", "disconnected", "waiting", "no_sensors", "ok"]
# How the backend gets temperature reports. "off": temperature control is off.
# "idle": on, not connected. "starting": M155 sent, waiting for the first
# auto-report. "autoreport": the printer reports by itself. "polling": M105
# every couple of seconds. "paused": M105 got no usable reply twice in a row;
# polling stopped until a retry.
TemperatureMonitor = Literal["off", "idle", "starting", "autoreport", "polling", "paused"]
HeaterStatus = Literal["heating", "cooling", "at_target", "off"]
# How the printer's heaters are addressed (config.json temperature.protocol)
TemperatureProtocol = Literal["marlin", "printess"]
# printess: the direction the last target was sent with (H: heat, C: cool)
HeaterMode = Literal["heat", "cool"]


def _type_required(schema: dict[str, Any]) -> None:
    # "type" has a default, so Pydantic leaves it out of "required"; the
    # client needs it there to tell events apart.
    if "type" in schema.get("properties", {}):
        required = schema.setdefault("required", [])
        if "type" not in required:
            required.insert(0, "type")


class Event(BaseModel):
    """A message on the event bus and the WebSocket."""

    model_config = ConfigDict(json_schema_extra=_type_required)

    # Fields left out of the message while they are None
    omit_if_none: ClassVar[tuple[str, ...]] = ()

    def dump(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        for name in self.omit_if_none:
            if data[name] is None:
                del data[name]
        return data


# --- shared parts -----------------------------------------------------------------


class SerialLogEntry(BaseModel):
    timestamp: str
    direction: Literal["sent", "received"]
    content: str
    line_number: int | None = None  # the N of a numbered line


class Temperature(BaseModel):
    actual: float
    target: float | None


class SensorInfo(BaseModel):
    sensor: str  # the key the printer reports: "T0", "B", ...
    name: str
    # Targets it accepts, besides 0 (off)
    min: float
    max: float
    settable: bool  # has a heater whose target can be set


class SensorReading(SensorInfo):
    actual: float
    target: float | None
    status: HeaterStatus
    # printess: whether the target was sent to heat or to cool (None: off,
    # or a target Octaris didn't send)
    mode: HeaterMode | None = None
    timestamp: float  # Unix seconds


class ChannelInfo(BaseModel):
    """A heater channel of the lab (printess) firmware."""

    sensor: str  # the key it goes by: "I0"
    index: int  # the I of M104 I<n>
    name: str
    reply_key: str  # the M105 reply key it reads from
    min: float
    max: float


class TemperatureStatus(BaseModel):
    """The latest reading of every sensor the printer reports."""

    state: TemperatureState
    monitor: TemperatureMonitor
    sensors: list[SensorReading]
    protocol: TemperatureProtocol = "marlin"
    # printess: every configured channel, reported or not (empty for marlin)
    channels: list[ChannelInfo] = []


class Settings(BaseModel):
    """Settings changed in the app (Setup screen), kept across restarts."""

    # The printer has temperature sensors: poll or auto-report temperatures,
    # show and log them, and warn about them. Off: no M155/M105 is ever sent
    # by itself, and every temperature feature is off.
    temperature_sensors: bool = False


# Which of the profile's nozzles are zeroed (X/Y and the nozzle's own
# height axis), by nozzle id
NozzleCalibration = dict[str, bool]


class Snapshot(BaseModel):
    """Current connection and print state."""

    printer_connected: bool
    port: str | None
    print_status: PrintStatus
    lines_sent: int
    lines_total: int
    # Every selected nozzle is zeroed
    calibrated: bool
    calibrated_nozzles: NozzleCalibration
    # The printer profile, and the ids of the nozzles the loaded print (or,
    # with none loaded, the last calibration) uses, left to right
    printer: PrinterProfile
    nozzles: list[str]
    flow_rate: float
    resumable: bool
    stop_reason: str | None
    time_estimate_s: int | None
    settings: Settings


# --- events -------------------------------------------------------------------------


class SnapshotEvent(Event, Snapshot):
    """Sent once, right after a WebSocket connects."""

    type: Literal["snapshot"] = "snapshot"


class StatusEvent(Event):
    type: Literal["status"] = "status"
    value: PrintStatus


class ProgressEvent(Event):
    type: Literal["progress"] = "progress"
    lines_sent: int
    lines_total: int
    time_remaining_s: float | None = None  # only when the time is known

    omit_if_none = ("time_remaining_s",)


class ExtrusionRateEvent(Event):
    type: Literal["extrusion_rate"] = "extrusion_rate"
    value: int


class ErrorEvent(Event):
    type: Literal["error"] = "error"
    message: str


class WarningEvent(Event):
    """E.g. a syringe running low during a print."""

    type: Literal["warning"] = "warning"
    message: str


class StopEvent(Event):
    """Sent after a stop once the backend knows whether the print can resume."""

    type: Literal["stop"] = "stop"
    resumable: bool
    reason: str | None
    line: int | None = None  # the line a resumable stop halted on

    omit_if_none = ("line",)


class SerialLogEvent(Event):
    type: Literal["serial_log"] = "serial_log"
    entry: SerialLogEntry


class TemperatureEvent(Event):
    type: Literal["temperature"] = "temperature"
    temperatures: dict[str, Temperature]  # by sensor: "T", "T0", "B", ...


class TemperatureStatusEvent(Event, TemperatureStatus):
    """GET /temperature's body, after every report and when the state changes."""

    type: Literal["temperature_status"] = "temperature_status"


class SettingsEvent(Event, Settings):
    """The settings changed (PUT /settings)."""

    type: Literal["settings"] = "settings"


class TemperatureWaitSensor(BaseModel):
    sensor: str
    name: str
    actual: float
    target: float
    stable_s: float  # how long it has been within target_band_c of its target


class TemperatureWaitEvent(Event):
    """Sent about once a second while a print waits for its temperatures,
    and once with waiting=False when it stops waiting (reached or stopped)."""

    type: Literal["temperature_wait"] = "temperature_wait"
    waiting: bool
    settle_s: float  # how long each target has to hold
    sensors: list[TemperatureWaitSensor]


class PrinterEvent(Event):
    type: Literal["printer"] = "printer"
    connected: bool
    port: str | None


class CalibrationEvent(Event):
    """value: every selected nozzle is zeroed. nozzles: which of the
    profile's nozzles are zeroed."""

    type: Literal["calibration"] = "calibration"
    value: Literal["calibrated", "uncalibrated"]
    nozzles: NozzleCalibration


class PrinterProfileEvent(Event):
    """Another printer profile was selected (PUT /printer), or the Custom
    profile in use was saved (PUT /printer/custom)."""

    type: Literal["printer_profile"] = "printer_profile"
    printer: PrinterProfile


class PrinterProfilesEvent(Event):
    """The list of printer profiles changed: the Custom profile was saved
    or reset (PUT/DELETE /printer/custom). Same body as GET /printer/profiles."""

    type: Literal["printer_profiles"] = "printer_profiles"
    selected: str
    profiles: list[PrinterProfile]


class PrintEndEvent(Event):
    """A print ended; resume_line is where a resumable stop halted."""

    type: Literal["print_end"] = "print_end"
    reason: EndReason
    resume_line: int | None


class PrintResumedEvent(Event):
    """A stopped print was resumed from its checkpoint."""

    type: Literal["print_resumed"] = "print_resumed"


WsEvent = Annotated[
    SnapshotEvent
    | StatusEvent
    | ProgressEvent
    | ExtrusionRateEvent
    | ErrorEvent
    | WarningEvent
    | StopEvent
    | SerialLogEvent
    | TemperatureEvent
    | TemperatureStatusEvent
    | TemperatureWaitEvent
    | SettingsEvent
    | PrinterEvent
    | CalibrationEvent
    | PrinterProfileEvent
    | PrinterProfilesEvent
    | PrintEndEvent
    | PrintResumedEvent,
    Field(discriminator="type"),
]


# --- REST responses -------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok"]


class PortInfo(BaseModel):
    device: str
    description: str


class PortsResponse(BaseModel):
    ports: list[PortInfo]


class ConnectResponse(BaseModel):
    status: Literal["connected"]
    port: str


class DisconnectResponse(BaseModel):
    status: Literal["disconnected"]


class CalibrationStatusResponse(BaseModel):
    calibrated: bool
    nozzles: NozzleCalibration


class CalibrateResponse(BaseModel):
    # The G92 sent, and the calibration after it: with several nozzles
    # selected, calibrated only once each of them is zeroed.
    command: str
    calibrated: bool
    nozzles: NozzleCalibration


class CalibrationResetResponse(BaseModel):
    status: Literal["uncalibrated"]


class PrinterProfilesResponse(BaseModel):
    """Every printer profile (built in, from config.json, and the Custom
    profile once one is saved), and the selected one's id."""

    selected: str
    profiles: list[PrinterProfile]


class SelectProfileRequest(BaseModel):
    profile: str


class CustomProfileRequest(BaseModel):
    """The Custom profile's nozzles, left to right, as edited on the Setup
    screen. Its bed, build volume and steps/mm are kept from the Custom
    profile saved before or, the first time, copied from `base` (the
    profile being edited; default: the selected one)."""

    nozzles: list[NozzleFields]
    nozzle_offset_measured: bool = False
    base: str | None = None


class UploadResult(BaseModel):
    status: Literal["ready"]
    filename: str
    lines_total: int
    time_estimate_s: int | None
    feed_log_entries: int
    preview_lines: list[str]
    # Changed in the G-code editor since it was loaded
    edited: bool = False
    # Checks on a file sent as uploaded that don't stop it printing
    warnings: list[str] = []


class GcodeEditResult(UploadResult):
    """PUT /gcode/loaded's body. program_id changes on every load and edit;
    GET /gcode/loaded sends it as its ETag."""

    program_id: str


class GcodeLineError(BaseModel):
    line: int | None  # 1-based; None when the error isn't on one line
    message: str


class GcodeEditErrorResponse(BaseModel):
    """PUT /gcode/loaded's 422 body: the edit was rejected, the loaded
    program is unchanged. The checks stop at the first problem, so
    `errors` holds one entry."""

    detail: str
    errors: list[GcodeLineError]


class PrintStartRequest(BaseModel):
    # Hold the print until every sensor with a target has stayed within
    # temperature.target_band_c of it for temperature.settle_s
    wait_for_temperature: bool = False
    # Return to the zero point first (Restart): lift clear, travel to X0 Y0,
    # lower to height 0. Converted prints always do; this makes a file sent
    # as uploaded, which otherwise starts wherever the nozzle is, do it too.
    from_origin: bool = False


class PrintStartResponse(BaseModel):
    status: Literal["printing"]
    lines_total: int


class StopResponse(BaseModel):
    status: Literal["stopped"]
    resumable: bool
    reason: str | None  # why it can't be resumed


class PauseResponse(BaseModel):
    status: Literal["paused"]


class ResumeResponse(BaseModel):
    status: Literal["printing"]


class ExtrusionResponse(BaseModel):
    status: Literal["ok"]
    rate: int


class JogResponse(BaseModel):
    status: Literal["ok"]
    axis: str  # the first of `axes`
    axes: list[str]  # every axis moved, in one G1 block
    distance: float


class OriginResponse(BaseModel):
    status: Literal["ok"]
    # The G-code sent, in order
    lines: list[str]


class GcodeSendResponse(BaseModel):
    status: Literal["ok"]
    response: str


class SerialLogResponse(BaseModel):
    entries: list[SerialLogEntry]


class ExtrusionChange(BaseModel):
    id: int
    session_id: int
    timestamp: str
    extrusion_rate: int
    lines_sent: int


class PrintSession(BaseModel):
    """One print in the history."""

    id: int
    started_at: str
    ended_at: str | None
    filename: str
    # The nozzle ids it printed with, comma-joined ("left,right"); before
    # printer profiles, the syringe mode ("left", "right", "both")
    syringe_config: str
    total_lines: int
    completed: bool
    nozzle_diameter: float | None
    syringe_diameter: float | None
    layer_height: float | None
    pressurize_mm: float | None
    flow_multiplier: float | None
    travel_retract_multiplier: float | None
    print_speed: float | None = None  # mm/s
    source: Literal["stl", "gcode"] | None
    end_reason: EndReason | None
    resume_line: int | None
    edited: bool = False  # the program was changed in the G-code editor
    serial_log: str | None = None  # path of the file with the print's serial traffic
    extrusion_events: list[ExtrusionChange]


class HistoryResponse(BaseModel):
    sessions: list[PrintSession]


class TemperatureSeries(SensorInfo):
    """One sensor's readings, oldest first, as parallel lists."""

    timestamps: list[float]  # Unix seconds
    actual: list[float]
    target: list[float | None]


class TemperatureHistoryResponse(BaseModel):
    series: list[TemperatureSeries]


class TemperatureTargetResponse(BaseModel):
    status: Literal["ok"]
    sensor: str
    target: float
    command: str


class SensorTestReading(BaseModel):
    sensor: str
    name: str
    actual: float
    target: float | None
    status: HeaterStatus
    mode: HeaterMode | None = None


class SensorTestResponse(BaseModel):
    """One M105's raw reply (None: no reply in time) and the sensors in it."""

    reply: str | None
    sensors: list[SensorTestReading]


class ErrorResponse(BaseModel):
    """The body of every 4xx/5xx response."""

    detail: str
