import { create } from 'zustand'
import type { SliceOptions } from '../api'
import type { NordsonTip } from '../data/nordsonTips'

// Numeric fields are kept as the raw input text; empty means "backend default".
// printSpeed is typed as a G-code feed rate (mm/min) and sent in mm/s.
export type PrintParameter = keyof SliceOptions

type PrintParameters = Record<PrintParameter, string>

interface PrintSettingsState extends PrintParameters {
  // Ids of the nozzles to print with (tapped on the syringe drawing); at
  // least one. Kept to the printer profile's nozzles by stores/printer.ts.
  selectedNozzles: string[]
  jogStep: number
  // Gauge of the picked Nordson tip; null once the nozzle diameter is typed by hand
  nordsonGauge: number | null
  selectNordsonTip: (tip: NordsonTip) => void
  setJogStep: (step: number) => void
  setSelectedNozzles: (ids: string[]) => void
  // Select or deselect one; the last selected one can't be deselected
  toggleNozzle: (id: string) => void
  setParameter: (key: PrintParameter, value: string) => void
}

// Module-level, so settings survive screen navigation within a session.
export const usePrintSettings = create<PrintSettingsState>()((set) => ({
  selectedNozzles: ['left'],
  nozzleDiameter: '',
  syringeDiameter: '',
  layerHeight: '',
  pressurizeMm: '',
  flowMultiplier: '',
  travelRetractMultiplier: '3',
  printSpeed: '300', // mm/min (5 mm/s)
  jogStep: 5,
  nordsonGauge: null,
  selectNordsonTip: (tip) =>
    set({ nordsonGauge: tip.gauge, nozzleDiameter: String(tip.innerDiameterMm) }),
  setJogStep: (jogStep) => set({ jogStep }),
  setSelectedNozzles: (selectedNozzles) => set({ selectedNozzles }),
  toggleNozzle: (id) =>
    set(({ selectedNozzles }) => {
      if (!selectedNozzles.includes(id)) return { selectedNozzles: [...selectedNozzles, id] }
      if (selectedNozzles.length === 1) return {}
      return { selectedNozzles: selectedNozzles.filter((n) => n !== id) }
    }),
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
    travelRetractMultiplier: num(settings.travelRetractMultiplier),
    // Typed in mm/min like a G-code F value; the backend takes mm/s
    printSpeed: settings.printSpeed ? parseFloat(settings.printSpeed) / 60 : undefined
  }
}
