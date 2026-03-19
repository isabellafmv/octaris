import { useEffect } from 'react'
import { SerialLog } from '../components/SerialLog'
import { GcodeInput } from '../components/GcodeInput'
import { api } from '../api'
import type { SerialLogEntry } from '../types'

interface TakeOverScreenProps {
  printerConnected: boolean
  serialLog: SerialLogEntry[]
  onClearLog: () => void
  onSetLog: (entries: SerialLogEntry[]) => void
  onBack: () => void
}

export function TakeOverScreen({
  printerConnected,
  serialLog,
  onClearLog,
  onSetLog,
  onBack,
}: TakeOverScreenProps) {
  // Fetch historical log entries on mount
  useEffect(() => {
    let cancelled = false
    api.getSerialLog(200).then(({ entries }) => {
      if (cancelled || entries.length === 0) return
      // Merge: use historical entries, then let WebSocket append new ones.
      // To avoid duplicates, we replace the log with history only if it's currently empty.
      onSetLog(entries)
    }).catch(() => {
      // Silently ignore — WebSocket will still stream live entries
    })
    return () => { cancelled = true }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex flex-col h-full bg-gray-900 text-white">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-800 border-b border-gray-700">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-sm text-gray-300 hover:text-white active:text-gray-400"
        >
          <span className="text-lg">←</span>
          <span>Back</span>
        </button>
        <h2 className="text-sm font-semibold text-gray-200 uppercase tracking-wide">
          Manual Take Over
        </h2>
        <div className="w-16" /> {/* spacer for centering */}
      </div>

      {/* Two-panel layout */}
      <div className="flex-1 flex min-h-0">
        {/* Left panel: Serial Log */}
        <div className="flex-1 flex flex-col border-r border-gray-700 min-w-0">
          <SerialLog entries={serialLog} onClear={onClearLog} />
        </div>

        {/* Right panel: G-code Input */}
        <div className="w-80 shrink-0 flex flex-col">
          <GcodeInput disabled={!printerConnected} />
        </div>
      </div>
    </div>
  )
}
