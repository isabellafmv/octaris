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

export interface WsEvent {
  type: 'progress' | 'time_remaining_s' | 'status' | 'extrusion_rate' | 'disconnected' | 'serial_log'
  value?: string | number
  lines_sent?: number
  lines_total?: number
  entry?: SerialLogEntry
}

export interface UploadResult {
  status: string
  filename: string
  lines_total: number
  time_estimate_s: number | null
  feed_log_entries: number
}
