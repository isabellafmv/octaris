import { create } from 'zustand'
import type { SerialLogEntry } from '../types'

const MAX_LOG_ENTRIES = 200

interface SerialLogState {
  entries: SerialLogEntry[]
  append: (entry: SerialLogEntry) => void
  setEntries: (entries: SerialLogEntry[]) => void
  clear: () => void
}

// Kept outside the websocket hook's state: a line arrives for every command
// sent or received, and only the Logs screen needs to re-render for it.
export const useSerialLog = create<SerialLogState>()((set) => ({
  entries: [],
  append: (entry) =>
    set((s) => {
      const updated = [...s.entries, entry]
      return { entries: updated.length > MAX_LOG_ENTRIES ? updated.slice(-MAX_LOG_ENTRIES) : updated }
    }),
  setEntries: (entries) => set({ entries }),
  clear: () => set({ entries: [] })
}))
