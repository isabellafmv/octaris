import { useCallback, useState } from 'react'
import { ProgressBar } from '../components/ProgressBar'
import { TimeRemaining } from '../components/TimeRemaining'
import { ExtrusionSlider } from '../components/ExtrusionSlider'
import { PrintOverlay } from '../components/PrintOverlay'
import { api } from '../api'
import type { PrintStatus } from '../types'

interface PrintScreenProps {
  status: PrintStatus
  linesSent: number
  linesTotal: number
  timeRemainingS: number | null
  extrusionRate: number
  filename: string | null
  onBack: () => void
}

export function PrintScreen({
  status,
  linesSent,
  linesTotal,
  timeRemainingS,
  extrusionRate,
  filename,
  onBack
}: PrintScreenProps) {
  const [error, setError] = useState<string | null>(null)

  const handleStop = useCallback(async () => {
    try {
      await api.printStop()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Stop failed')
    }
  }, [])

  const handlePause = useCallback(async () => {
    try {
      await api.printPause()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Pause failed')
    }
  }, [])

  const handleResume = useCallback(async () => {
    try {
      await api.printResume()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Resume failed')
    }
  }, [])

  const percentage = linesTotal > 0 ? Math.round((linesSent / linesTotal) * 100) : 0

  return (
    <div className="flex flex-col h-full p-6 max-w-4xl mx-auto">
      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
          {error}
        </div>
      )}

      <div className="text-center mb-2 text-sm text-gray-500">{filename}</div>

      <div className="flex-1 flex flex-col justify-center gap-8">
        <div>
          <h3 className="text-sm font-medium text-gray-500 mb-2">Extrusion Rate</h3>
          <ExtrusionSlider currentRate={extrusionRate} />
        </div>

        <TimeRemaining seconds={timeRemainingS} linesSent={linesSent} linesTotal={linesTotal} />

        <ProgressBar percentage={percentage} />
      </div>

      <div className="flex justify-center gap-4 mt-8">
        {status === 'printing' && (
          <button
            onClick={handlePause}
            className="min-h-[48px] px-8 py-3 text-lg font-semibold rounded-lg bg-amber-500 text-white active:scale-95 transition-transform"
          >
            Pause
          </button>
        )}
        {status === 'paused' && (
          <button
            onClick={handleResume}
            className="min-h-[48px] px-8 py-3 text-lg font-semibold rounded-lg bg-green-600 text-white active:scale-95 transition-transform"
          >
            Resume
          </button>
        )}
        <button
          onClick={handleStop}
          className="min-h-[48px] px-8 py-3 text-lg font-semibold rounded-lg bg-red-600 text-white active:scale-95 transition-transform"
        >
          Stop
        </button>
      </div>

      {(status === 'stopped' || status === 'completed') && (
        <PrintOverlay status={status} onResume={handleResume} onBack={onBack} />
      )}
    </div>
  )
}
