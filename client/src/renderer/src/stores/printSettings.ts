import { create } from 'zustand'
import type { SliceOptions } from '../api'
import type { SyringeMode } from '../types'
import type { NordsonTip } from '../data/nordsonTips'

// Numeric fields are kept as the raw input text; empty means "backend default".
export type PrintParameter = keyof SliceOptions

type PrintParameters = Record<PrintParameter, string>

interface PrintSettingsState extends PrintParameters {
  syringeMode: SyringeMode
  jogStep: number
  // Gauge of the picked Nordson tip; null once the nozzle diameter is typed by hand
  nordsonGauge: number | null
  selectNordsonTip: (tip: NordsonTip) => void
  setJogStep: (step: number) => void
  setSyringeMode: (mode: SyringeMode) => void
  setParameter: (key: PrintParameter, value: string) => void
}

// Module-level, so settings survive screen navigation within a session.
export const usePrintSettings = create<PrintSettingsState>()((set) => ({
  syringeMode: 'left',
  nozzleDiameter: '',
  syringeDiameter: '',
  layerHeight: '',
  pressurizeMm: '',
  flowMultiplier: '',
  travelRetractMultiplier: '3',
  jogStep: 5,
  nordsonGauge: null,
  selectNordsonTip: (tip) =>
    set({ nordsonGauge: tip.gauge, nozzleDiameter: String(tip.innerDiameterMm) }),
  setJogStep: (jogStep) => set({ jogStep }),
  setSyringeMode: (syringeMode) => set({ syringeMode }),
  setParameter: (key, value) =>
    set({
      [key]: value,
      ...(key === 'nozzleDiameter' && { nordsonGauge: null })
    } as Partial<PrintSettingsState>)
}))

export function sliceOptions(settings: PrintParameters): SliceOptions {
  const num = (v: string): number | undefined => (v ? parseFloat(v) : undefined)
  return {
    nozzleDiameter: num(settings.nozzleDiameter),
    syringeDiameter: num(settings.syringeDiameter),
    layerHeight: num(settings.layerHeight),
    pressurizeMm: num(settings.pressurizeMm),
    flowMultiplier: num(settings.flowMultiplier),
    travelRetractMultiplier: num(settings.travelRetractMultiplier)
  }
}
