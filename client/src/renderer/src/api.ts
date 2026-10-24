import type {
  CalibrateResponse,
  CustomProfileRequest,
  GcodeEditResult,
  GcodeLineError,
  NozzleCalibration,
  PortInfo,
  PrinterProfile,
  PrinterProfilesResponse,
  SensorTestResponse,
  SerialLogEntry,
  Settings,
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

// A rejected G-code edit. `errors` say which line, when the backend knows.
export class GcodeSaveError extends Error {
  constructor(
    message: string,
    readonly errors: GcodeLineError[] = []
  ) {
    super(message)
  }
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

// The nozzles to print with, by id: one `nozzles` parameter each
function nozzleParams(nozzles: string[]): URLSearchParams {
  const params = new URLSearchParams()
  for (const id of nozzles) params.append('nozzles', id)
  return params
}

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
  upload: (file: File, nozzles: string[], opts: SliceOptions = {}) => {
    const params = nozzleParams(nozzles)
    for (const [key, name] of Object.entries(SLICE_PARAMS)) {
      const value = opts[key as keyof SliceOptions]
      if (value) params.set(name, String(value))
    }
    return uploadFile('/upload', file, params)
  },
  // needsChanges: post-process it; otherwise it is sent as uploaded, with warnings
  uploadGcode: (file: File, nozzles: string[], needsChanges: boolean) => {
    const params = nozzleParams(nozzles)
    params.set('needs_changes', String(needsChanges))
    return uploadFile('/upload/gcode', file, params)
  },
  // fromOrigin (Restart): return to the zero point before starting, also for a
  // file sent as uploaded
  printStart: (fromOrigin = false) =>
    json<{ status: string }>('/print/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_origin: fromOrigin })
    }),
  printStop: () => json<{ status: string }>('/print/stop', { method: 'POST' }),
  printPause: () => json<{ status: string }>('/print/pause', { method: 'POST' }),
  printResume: () => json<{ status: string }>('/print/resume', { method: 'POST' }),
  setExtrusion: (rate: number) =>
    json<{ status: string }>('/extrusion', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rate })
    }),
  // Moves all of `axes` by `distance` together, in one G1 block
  jog: (axes: string[], distance: number, feedRate = 300) =>
    json<{ status: string }>('/jog', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ axes, distance, feed_rate: feedRate })
    }),
  // X0 Y0 at the current height; with lower, then the selected nozzles down to zero height
  goToOrigin: (lower = false) =>
    json<{ status: string; lines: string[] }>('/jog/origin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lower })
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
  // The same, with its program id (ETag), for the editor to save it against
  getLoadedGcodeForEdit: async (signal?: AbortSignal) => {
    const res = await request('/gcode/loaded', { signal, cache: 'no-store' })
    return { text: await res.text(), etag: res.headers.get('ETag') }
  },
  // Replace the loaded program's lines. With `etag`, only if it is still the
  // program the editor opened. Throws GcodeSaveError if a check fails.
  saveLoadedGcode: async (text: string, etag: string | null): Promise<GcodeEditResult> => {
    const res = await fetch(`${BASE}/gcode/loaded`, {
      method: 'PUT',
      headers: {
        ...authHeaders(),
        'Content-Type': 'text/plain; charset=utf-8',
        ...(etag ? { 'If-Match': etag } : {})
      },
      body: text
    })
    if (res.ok) return res.json()
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new GcodeSaveError(body.detail || res.statusText, body.errors ?? [])
  },
  getSerialLog: (limit = 200) => json<{ entries: SerialLogEntry[] }>(`/gcode/log?limit=${limit}`),
  calibrationStatus: () =>
    json<{ calibrated: boolean; nozzles: NozzleCalibration }>('/calibration/status'),
  // Zero one of the selected nozzles where it is (the first sets X/Y too)
  calibrationZero: (nozzle: string, nozzles: string[]) =>
    json<CalibrateResponse>('/calibration/zero', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nozzle, nozzles })
    }),
  getPrinterProfiles: () => json<PrinterProfilesResponse>('/printer/profiles'),
  // Kept across restarts; resets the calibration and unloads the print
  selectPrinterProfile: (profile: string) =>
    json<PrinterProfile>('/printer', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile })
    }),
  // Save the Custom profile and select it (resets the calibration and
  // unloads the print, as switching does)
  saveCustomProfile: (request: CustomProfileRequest) =>
    json<PrinterProfile>('/printer/custom', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request)
    }),
  // Delete the Custom profile and go back to the Printess 1.0
  resetCustomProfile: () => json<PrinterProfile>('/printer/custom', { method: 'DELETE' }),
  calibrationReset: () => json<{ status: string }>('/calibration/reset', { method: 'POST' }),
  getSettings: () => json<Settings>('/settings'),
  saveSettings: (settings: Settings) =>
    json<Settings>('/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings)
    }),
  getTemperature: () => json<TemperatureStatus>('/temperature'),
  // One M105, with or without temperature control on: the raw reply and the sensors in it
  testSensors: () => json<SensorTestResponse>('/temperature/test', { method: 'POST' }),
  // Start polling again after the printer didn't answer M105
  retryTemperature: () => json<TemperatureStatus>('/temperature/retry', { method: 'POST' }),
  getTemperatureHistory: (minutes: number) =>
    json<TemperatureHistoryResponse>(`/temperature/history?minutes=${minutes}`),
  // Lab firmware: `direction` heats (H) or cools (C) to the target; without
  // it the backend chooses from the channel's reading. 0 is off.
  setTemperatureTarget: (sensor: string, target: number, direction?: 'heat' | 'cool') =>
    json<{ status: string; command: string }>('/temperature/target', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sensor, target, direction: direction ?? null })
    }),
  exportTemperatureCsv: (range: TemperatureRange) => {
    const params =
      'sessionId' in range
        ? new URLSearchParams({ session_id: String(range.sessionId) })
        : new URLSearchParams({ from: range.from.toISOString(), to: range.to.toISOString() })
    return download(`/temperature/export.csv?${params}`, 'temperature.csv')
  }
}
