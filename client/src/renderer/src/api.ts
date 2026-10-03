import type { PortInfo, SerialLogEntry, SyringeMode, UploadResult } from './types'

const BASE = 'http://127.0.0.1:8000'

// Present in Electron (set by preload from --octaris-token=...); absent when
// the renderer is opened outside Electron, e.g. a plain browser during dev.
function authHeaders(): Record<string, string> {
  const token = window.octaris?.token
  return token ? { 'X-Octaris-Token': token } : {}
}

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers || {}) }
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail || res.statusText)
  }
  return res.json()
}

// Slicer options for /upload, keyed by their query parameter names
const SLICE_PARAMS = {
  nozzleDiameter: 'nozzle_diameter',
  syringeDiameter: 'syringe_diameter',
  layerHeight: 'layer_height',
  pressurizeMm: 'pressurize_mm',
  flowMultiplier: 'flow_multiplier',
  travelRetractMultiplier: 'travel_retract_multiplier'
} as const

export type SliceOptions = Partial<Record<keyof typeof SLICE_PARAMS, number>>

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
  uploadGcode: (file: File, syringeMode: SyringeMode) =>
    uploadFile('/upload/gcode', file, new URLSearchParams({ syringe_mode: syringeMode })),
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
  getSerialLog: (limit = 200) => json<{ entries: SerialLogEntry[] }>(`/gcode/log?limit=${limit}`),
  calibrationStatus: () => json<{ calibrated: boolean }>('/calibration/status'),
  calibrationZero: () =>
    json<{ status: string; command: string }>('/calibration/zero', { method: 'POST' }),
  calibrationReset: () => json<{ status: string }>('/calibration/reset', { method: 'POST' })
}
