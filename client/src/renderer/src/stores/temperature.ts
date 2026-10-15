import { create } from 'zustand'
import type { SensorInfo, TemperatureSeries, TemperatureStatus } from '../types'

// The chart's longest range; live readings older than this are dropped
export const RANGES_MIN = [10, 30, 60] as const
export type RangeMin = (typeof RANGES_MIN)[number]
const KEEP_S = 60 * 60

export interface SensorHistory {
  timestamps: number[] // Unix seconds
  actual: number[]
  target: (number | null)[]
}

interface TemperatureState {
  // null until the first GET /temperature or temperature_status event
  status: TemperatureStatus | null
  // Every sensor seen, live or in the history
  sensors: Record<string, SensorInfo>
  history: Record<string, SensorHistory>
  rangeMin: RangeMin
  applyStatus: (status: TemperatureStatus) => void
  backfill: (series: TemperatureSeries[]) => void
  setRange: (rangeMin: RangeMin) => void
}

function info({ sensor, name, min, max, settable }: SensorInfo): SensorInfo {
  return { sensor, name, min, max, settable }
}

function trimmed(h: SensorHistory, now: number): SensorHistory {
  const first = h.timestamps.findIndex((t) => t >= now - KEEP_S)
  if (first <= 0) return first === -1 ? { timestamps: [], actual: [], target: [] } : h
  return {
    timestamps: h.timestamps.slice(first),
    actual: h.actual.slice(first),
    target: h.target.slice(first)
  }
}

// Kept outside the websocket hook's state, like the serial log: a status
// arrives every couple of seconds and only the temperature views need it.
export const useTemperature = create<TemperatureState>()((set) => ({
  status: null,
  sensors: {},
  history: {},
  rangeMin: 30,

  applyStatus: (status) =>
    set((s) => {
      const sensors = { ...s.sensors }
      const history = { ...s.history }
      const now = Date.now() / 1000
      for (const reading of status.sensors) {
        sensors[reading.sensor] = info(reading)
        const h = history[reading.sensor] ?? { timestamps: [], actual: [], target: [] }
        // A status also goes out when only the state changed: same reading
        if (h.timestamps[h.timestamps.length - 1] >= reading.timestamp) continue
        history[reading.sensor] = trimmed(
          {
            timestamps: [...h.timestamps, reading.timestamp],
            actual: [...h.actual, reading.actual],
            target: [...h.target, reading.target]
          },
          now
        )
      }
      return { status, sensors, history }
    }),

  // The backend's recent history, plus whatever arrived live since it was read
  backfill: (series) =>
    set((s) => {
      const sensors = { ...s.sensors }
      const history = { ...s.history }
      for (const item of series) {
        sensors[item.sensor] = info(item)
        const last = item.timestamps[item.timestamps.length - 1] ?? 0
        const live = s.history[item.sensor]
        const newer = live ? live.timestamps.findIndex((t) => t > last) : -1
        history[item.sensor] = {
          timestamps: [...item.timestamps, ...(newer >= 0 ? live!.timestamps.slice(newer) : [])],
          actual: [...item.actual, ...(newer >= 0 ? live!.actual.slice(newer) : [])],
          target: [...item.target, ...(newer >= 0 ? live!.target.slice(newer) : [])]
        }
      }
      return { sensors, history }
    }),

  setRange: (rangeMin) => set({ rangeMin })
}))
