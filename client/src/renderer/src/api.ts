import type { PortInfo, SerialLogEntry, SyringeMode, UploadResult } from './types'

const BASE = 'http://127.0.0.1:8000'

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail || res.statusText)
  }
  return res.json()
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
  upload: async (
    file: File,
    syringeMode: SyringeMode,
    opts?: { nozzleDiameter?: number; syringeDiameter?: number; layerHeight?: number; pressurizeMm?: number; flowMultiplier?: number }
  ): Promise<UploadResult> => {
    const form = new FormData()
    form.append('file', file)
    const params = new URLSearchParams({ syringe_mode: syringeMode })
    if (opts?.nozzleDiameter) params.set('nozzle_diameter', String(opts.nozzleDiameter))
    if (opts?.syringeDiameter) params.set('syringe_diameter', String(opts.syringeDiameter))
    if (opts?.layerHeight) params.set('layer_height', String(opts.layerHeight))
    if (opts?.pressurizeMm) params.set('pressurize_mm', String(opts.pressurizeMm))
    if (opts?.flowMultiplier) params.set('flow_multiplier', String(opts.flowMultiplier))
    const res = await fetch(`${BASE}/upload?${params}`, {
      method: 'POST',
      body: form
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(body.detail || res.statusText)
    }
    return res.json()
  },
  uploadGcode: async (file: File, syringeMode: SyringeMode): Promise<UploadResult> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/upload/gcode?syringe_mode=${syringeMode}`, {
      method: 'POST',
      body: form
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(body.detail || res.statusText)
    }
    return res.json()
  },
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
  getSerialLog: (limit = 200) =>
    json<{ entries: SerialLogEntry[] }>(`/gcode/log?limit=${limit}`),
  calibrationStatus: () =>
    json<{ calibrated: boolean }>('/calibration/status'),
  calibrationZero: () =>
    json<{ status: string; command: string }>('/calibration/zero', { method: 'POST' }),
  calibrationReset: () =>
    json<{ status: string }>('/calibration/reset', { method: 'POST' }),
}
