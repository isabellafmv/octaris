// REST and WebSocket types, generated from the backend's Pydantic models.
// Regenerate with `npm run gen:types` after changing backend/backend/schemas.py.
import type { components } from './generated/api'

type Schemas = components['schemas']

export type SyringeMode = 'left' | 'right' | 'both'
export type PrintStatus = Schemas['StatusEvent']['value']

export type PortInfo = Schemas['PortInfo']
export type SerialLogEntry = Schemas['SerialLogEntry']
export type UploadResult = Schemas['UploadResult']
export type Snapshot = Schemas['Snapshot']
export type PrintSession = Schemas['PrintSession']
export type SensorInfo = Pick<SensorReading, 'sensor' | 'name' | 'min' | 'max' | 'settable'>
export type SensorReading = Schemas['SensorReading']
export type TemperatureStatus = Schemas['TemperatureStatus']
export type TemperatureSeries = Schemas['TemperatureSeries']
export type TemperatureHistoryResponse = Schemas['TemperatureHistoryResponse']

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
export type SnapshotEvent = Schemas['SnapshotEvent']
export type CalibrationEvent = Schemas['CalibrationEvent']
export type PrintEndEvent = Schemas['PrintEndEvent']
export type PrintResumedEvent = Schemas['PrintResumedEvent']
export type WsEvent = Schemas['WsEvent']

// null while the backend is still working out where the print stopped
export type StopInfo = { resumable: boolean; reason: string | null } | null
