export type SyringeMode = 'left' | 'right' | 'both'
export type PrintStatus = 'idle' | 'slicing' | 'ready' | 'printing' | 'paused' | 'stopped' | 'completed'

export interface PortInfo {
  device: string
  description: string
}

export interface SerialLogEntry {
  timestamp: string
  direction: 'sent' | 'received'
  content: string
}

export interface ProgressEvent {
  type: 'progress'
  lines_sent: number
  lines_total: number
  time_remaining_s?: number
}

export interface StatusEvent {
  type: 'status'
  value: PrintStatus
}

export interface ExtrusionRateEvent {
  type: 'extrusion_rate'
  value: number
}

export interface ErrorEvent {
  type: 'error'
  message: string
}

// E.g. a syringe running low during a print
export interface WarningEvent {
  type: 'warning'
  message: string
}

// Sent after a stop once the backend knows whether the print can resume
export interface StopEvent {
  type: 'stop'
  resumable: boolean
  reason: string | null
  line?: number
}

// null while the backend is still working out where the print stopped
export type StopInfo = { resumable: boolean; reason: string | null } | null

export interface SerialLogWsEvent {
  type: 'serial_log'
  entry: SerialLogEntry
}

export interface PrinterEvent {
  type: 'printer'
  connected: boolean
  port: string | null
}

export interface SnapshotEvent {
  type: 'snapshot'
  printer_connected: boolean
  port: string | null
  print_status: PrintStatus
  lines_sent: number
  lines_total: number
  calibrated: boolean
  flow_rate: number
  resumable: boolean
  stop_reason: string | null
  time_estimate_s: number | null
}

export interface CalibrationEvent {
  type: 'calibration'
  value: 'calibrated' | 'uncalibrated'
}

export type WsEvent =
  | ProgressEvent
  | StatusEvent
  | ExtrusionRateEvent
  | ErrorEvent
  | WarningEvent
  | StopEvent
  | SerialLogWsEvent
  | PrinterEvent
  | SnapshotEvent
  | CalibrationEvent

export interface UploadResult {
  status: string
  filename: string
  lines_total: number
  time_estimate_s: number | null
  feed_log_entries: number
  preview_lines: string[]
}
