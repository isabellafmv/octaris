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
  useEffect(() => {
    let cancelled = false
    api.getSerialLog(200).then(({ entries }) => {
      if (cancelled || entries.length === 0) return
      onSetLog(entries)
    }).catch(() => {})
    return () => { cancelled = true }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex flex-col h-full" style={{ backgroundColor: '#F5F1E6', color: '#2D3333' }}>
      {/* Top bar */}
      <div
        className="flex items-center justify-between px-5 py-3 shrink-0 border-b"
        style={{ borderColor: '#D8D3C8' }}
      >
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-sm font-medium transition-opacity active:opacity-60"
          style={{ color: '#8B9090' }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-4 h-4">
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
          </svg>
          Back
        </button>
        <h2
          className="text-xs font-semibold tracking-widest uppercase"
          style={{ color: '#8B9090' }}
        >
          Manual Take Over
        </h2>
        <div className="w-16" />
      </div>

      {/* Two-panel layout */}
      <div className="flex-1 flex min-h-0">
        <div className="flex-1 flex flex-col border-r min-w-0" style={{ borderColor: '#D8D3C8' }}>
          <SerialLog entries={serialLog} onClear={onClearLog} />
        </div>
        <div className="w-80 shrink-0 flex flex-col">
          <GcodeInput disabled={!printerConnected} />
        </div>
      </div>
    </div>
  )
}
