import type {
  CalibrateResponse,
  NozzleCalibration,
  PortInfo,
  SerialLogEntry,
  SyringeMode,
  TemperatureHistoryResponse,
  TemperatureStatus,
  UploadResult
} from './types'

const BASE = 'http://127.0.0.1:8000'

// Present in Electron (set by preload from --octaris-token=...); absent when
// the renderer is opened outside Electron, e.g. a plain browser during dev.
function authHeaders(): Record<string, string> {
  const token = window.octaris?.token
  return token ? { 'X-Octaris-Token': token } : {}
}

async function request(url: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(`${BASE}${url}`, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers || {}) }
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail || res.statusText)
  }
  return res
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  return (await request(url, init)).json()
}

// Slicer options for /upload, keyed by their query parameter names
const SLICE_PARAMS = {
  nozzleDiameter: 'nozzle_diameter',
  syringeDiameter: 'syringe_diameter',
  layerHeight: 'layer_height',
  pressurizeMm: 'pressurize_mm',
  flowMultiplier: 'flow_multiplier',
  travelRetractMultiplier: 'travel_retract_multiplier',
  printSpeed: 'print_speed'
} as const

export type SliceOptions = Partial<Record<keyof typeof SLICE_PARAMS, number>>

// Saves a file the backend sends as an attachment. Fetched rather than linked
// to, so the request carries the auth header.
async function download(url: string, fallbackName: string): Promise<void> {
  const res = await fetch(`${BASE}${url}`, { headers: authHeaders() })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail || res.statusText)
  }
  const disposition = res.headers.get('Content-Disposition') ?? ''
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName
  const href = URL.createObjectURL(await res.blob())
  const link = document.createElement('a')
  link.href = href
  link.download = name
  link.click()
  URL.revokeObjectURL(href)
}

// A time range, or one print session's readings
export type TemperatureRange = { from: Date; to: Date } | { sessionId: number }

function uploadFile(path: string, file: File, params: URLSearchParams): Promise<UploadResult> {
  const form = new FormData()
  form.append('file', file)
  return json<UploadResult>(`${path}?${params}`, { method: 'POST', body: form })
}

export const api = {
  getPorts: () => json<{ ports: PortInfo[] }>('/ports'),
  connect: (port: string) =>
    json<{ status: string }>('/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ port })
    }),
  disconnect: () => json<{ status: string }>('/disconnect', { method: 'POST' }),
  upload: (file: File, syringeMode: SyringeMode, opts: SliceOptions = {}) => {
    const params = new URLSearchParams({ syringe_mode: syringeMode })
    for (const [key, name] of Object.entries(SLICE_PARAMS)) {
      const value = opts[key as keyof SliceOptions]
      if (value) params.set(name, String(value))
    }
    return uploadFile('/upload', file, params)
  },
  // needsChanges: post-process it; otherwise it is sent as uploaded, with warnings
  uploadGcode: (file: File, syringeMode: SyringeMode, needsChanges: boolean) =>
    uploadFile(
      '/upload/gcode',
      file,
      new URLSearchParams({ syringe_mode: syringeMode, needs_changes: String(needsChanges) })
    ),
  printStart: () => json<{ status: string }>('/print/start', { method: 'POST' }),
  printStop: () => json<{ status: string }>('/print/stop', { method: 'POST' }),
  printPause: () => json<{ status: string }>('/print/pause', { method: 'POST' }),
  printResume: () => json<{ status: string }>('/print/resume', { method: 'POST' }),
  setExtrusion: (rate: number) =>
    json<{ status: string }>('/extrusion', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rate })
    }),
  jog: (axis: string, distance: number, feedRate = 300) =>
    json<{ status: string }>('/jog', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ axis, distance, feed_rate: feedRate })
    }),
  sendGcode: (line: string) =>
    json<{ status: string; response: string }>('/gcode/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ line })
    }),
  // The loaded print's full processed G-code, for the 3D preview
  getLoadedGcode: async (signal?: AbortSignal) =>
    (await request('/gcode/loaded', { signal })).text(),
  getSerialLog: (limit = 200) => json<{ entries: SerialLogEntry[] }>(`/gcode/log?limit=${limit}`),
  calibrationStatus: () =>
    json<{ calibrated: boolean; nozzles: NozzleCalibration }>('/calibration/status'),
  // Zero one nozzle where it is, for the selected mode (both mode: left, then right)
  calibrationZero: (nozzle: 'left' | 'right', syringeMode: SyringeMode) =>
    json<CalibrateResponse>('/calibration/zero', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nozzle, syringe_mode: syringeMode })
    }),
  calibrationReset: () => json<{ status: string }>('/calibration/reset', { method: 'POST' }),
  getTemperature: () => json<TemperatureStatus>('/temperature'),
  getTemperatureHistory: (minutes: number) =>
    json<TemperatureHistoryResponse>(`/temperature/history?minutes=${minutes}`),
  setTemperatureTarget: (sensor: string, target: number) =>
    json<{ status: string; command: string }>('/temperature/target', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sensor, target })
    }),
  exportTemperatureCsv: (range: TemperatureRange) => {
    const params =
      'sessionId' in range
        ? new URLSearchParams({ session_id: String(range.sessionId) })
        : new URLSearchParams({ from: range.from.toISOString(), to: range.to.toISOString() })
    return download(`/temperature/export.csv?${params}`, 'temperature.csv')
  }
}
