import { useEffect } from 'react'
import { SerialLog } from '../components/SerialLog'
import { GcodeInput } from '../components/GcodeInput'
import { api } from '../api'
import { useSerialLog } from '../stores/serialLog'
import type { PrintStatus } from '../types'

interface TakeOverScreenProps {
  printerConnected: boolean
  printStatus: PrintStatus
  onBack: () => void
}

export function TakeOverScreen({
  printerConnected,
  printStatus,
  onBack,
}: TakeOverScreenProps): React.JSX.Element {
  const entries = useSerialLog((s) => s.entries)
  const clear = useSerialLog((s) => s.clear)

  // Backfill lines logged before this screen was opened
  useEffect(() => {
    let cancelled = false
    api.getSerialLog(200).then(({ entries }) => {
      if (cancelled || entries.length === 0) return
      useSerialLog.getState().setEntries(entries)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  return (
    <div className="flex flex-col h-full bg-surface text-text">
      {/* Top bar */}
      <div className="flex items-center justify-between px-5 py-3 shrink-0 border-b border-border">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-sm font-medium transition-opacity active:opacity-60 text-text-muted"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-4 h-4">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
          </svg>
          Back
        </button>
        <h2 className="text-xs font-semibold tracking-widest uppercase text-text-muted">
          Manual Take Over
        </h2>
        <div className="w-16" />
      </div>

      {/* Two-panel layout */}
      <div className="flex-1 flex min-h-0">
        <div className="flex-1 flex flex-col border-r min-w-0 border-border">
          <SerialLog entries={entries} onClear={clear} />
        </div>
        <div className="w-80 shrink-0 flex flex-col">
          <GcodeInput disabled={!printerConnected} printing={printStatus === 'printing'} />
        </div>
      </div>
    </div>
  )
}
