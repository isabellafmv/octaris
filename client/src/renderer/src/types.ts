// REST and WebSocket types, generated from the backend's Pydantic models.
// Regenerate with `npm run gen:types` after changing backend/backend/schemas.py.
import type { components } from './generated/api'

type Schemas = components['schemas']

export type PrintStatus = Schemas['StatusEvent']['value']

export type PortInfo = Schemas['PortInfo']
export type SerialLogEntry = Schemas['SerialLogEntry']
export type UploadResult = Schemas['UploadResult']
// PUT /gcode/loaded: the edited program, with its new id (the GET's ETag)
export type GcodeEditResult = Schemas['GcodeEditResult']
export type GcodeLineError = Schemas['GcodeLineError']
export type Snapshot = Schemas['Snapshot']
// Which of the profile's nozzles are zeroed (X/Y plus the nozzle's own height), by id
export type NozzleCalibration = Record<string, boolean>
// The printer: its nozzles left to right (each with its height and plunger
// axis and its offset from the first), bed limits and build volume
export type PrinterProfile = Schemas['PrinterProfile']
export type NozzleSpec = Schemas['NozzleSpec']
export type PrinterProfilesResponse = Schemas['PrinterProfilesResponse']
// PUT /printer/custom: the Custom profile's syringes, as edited on Setup
export type CustomProfileRequest = Schemas['CustomProfileRequest']
export type CalibrateResponse = Schemas['CalibrateResponse']
export type PrintSession = Schemas['PrintSession']
export type SensorInfo = Pick<SensorReading, 'sensor' | 'name' | 'min' | 'max' | 'settable'>
export type SensorReading = Schemas['SensorReading']
export type TemperatureStatus = Schemas['TemperatureStatus']
// The lab firmware's heater channels (protocol "printess"), I0..I3
export type ChannelInfo = Schemas['ChannelInfo']
export type TemperatureProtocol = TemperatureStatus['protocol']
export type TemperatureSeries = Schemas['TemperatureSeries']
export type TemperatureHistoryResponse = Schemas['TemperatureHistoryResponse']
// How the backend gets temperature reports; "paused": the printer doesn't answer M105
export type TemperatureMonitor = TemperatureStatus['monitor']
// POST /temperature/test: one M105's raw reply (null: none came) and the sensors in it
export type SensorTestResponse = Schemas['SensorTestResponse']
// Settings changed on the Setup screen, kept by the backend across restarts
export type Settings = Schemas['Settings']

export type ProgressEvent = Schemas['ProgressEvent']
export type StatusEvent = Schemas['StatusEvent']
export type ExtrusionRateEvent = Schemas['ExtrusionRateEvent']
export type ErrorEvent = Schemas['ErrorEvent']
// E.g. a syringe running low during a print
export type WarningEvent = Schemas['WarningEvent']
// Sent after a stop once the backend knows whether the print can resume
export type StopEvent = Schemas['StopEvent']
export type SerialLogWsEvent = Schemas['SerialLogEvent']
export type TemperatureEvent = Schemas['TemperatureEvent']
// GET /temperature's body, after every report and when the state changes
export type TemperatureStatusEvent = Schemas['TemperatureStatusEvent']
export type PrinterEvent = Schemas['PrinterEvent']
export type SettingsEvent = Schemas['SettingsEvent']
export type SnapshotEvent = Schemas['SnapshotEvent']
export type CalibrationEvent = Schemas['CalibrationEvent']
// Another printer profile was selected
export type PrinterProfileEvent = Schemas['PrinterProfileEvent']
// The list of profiles changed (the Custom profile was saved or reset)
export type PrinterProfilesEvent = Schemas['PrinterProfilesEvent']
export type PrintEndEvent = Schemas['PrintEndEvent']
export type PrintResumedEvent = Schemas['PrintResumedEvent']
export type WsEvent = Schemas['WsEvent']

// null while the backend is still working out where the print stopped
export type StopInfo = { resumable: boolean; reason: string | null } | null
