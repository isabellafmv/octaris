import { create } from 'zustand'
import type { NozzleSpec, PrinterProfile } from '../types'
import { usePrintSettings } from './printSettings'

// The printer profile, from the backend's snapshot (on connect) and
// printer_profile events. null until the first snapshot.
interface PrinterState {
  profile: PrinterProfile | null
  setProfile: (profile: PrinterProfile) => void
}

export const usePrinter = create<PrinterState>()((set) => ({
  profile: null,
  setProfile: (profile) => {
    set({ profile })
    // Keep the selection to the profile's nozzles
    const { selectedNozzles, setSelectedNozzles } = usePrintSettings.getState()
    const ids = profile.nozzles.map((n) => n.id)
    const kept = selectedNozzles.filter((id) => ids.includes(id))
    if (kept.length !== selectedNozzles.length || kept.length === 0) {
      setSelectedNozzles(kept.length > 0 ? kept : ids.slice(0, 1))
    }
  }
}))

// The selected nozzles, left to right as on the head (the first sets X/Y)
export function orderedSelection(profile: PrinterProfile | null, ids: string[]): NozzleSpec[] {
  return (profile?.nozzles ?? []).filter((n) => ids.includes(n.id))
}

// The same, from the stores
export function useSelectedNozzles(): NozzleSpec[] {
  const profile = usePrinter((s) => s.profile)
  const ids = usePrintSettings((s) => s.selectedNozzles)
  return orderedSelection(profile, ids)
}
