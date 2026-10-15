import { useState } from 'react'
import { api } from '../api'
import { useTemperature } from '../stores/temperature'

export const TEMPERATURE_OFF_HINT = 'Temperature control is off — enable it in Setup'

// Shown while temperature polling is paused because the printer didn't
// answer M105 with temperatures. Other commands are never held up by it.
export function SensorsNotReporting({
  onError
}: {
  onError: (msg: string) => void
}): React.JSX.Element | null {
  const paused = useTemperature((s) => s.status?.monitor === 'paused')
  const [retrying, setRetrying] = useState(false)
  if (!paused) return null

  const handleRetry = async (): Promise<void> => {
    setRetrying(true)
    try {
      useTemperature.getState().applyStatus(await api.retryTemperature())
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Retry failed')
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div
      role="alert"
      className="px-4 py-2 rounded-lg text-sm flex items-center justify-between gap-3 bg-danger-muted text-danger"
    >
      <span>The printer doesn&apos;t report temperatures — check the sensor setup</span>
      <button
        onClick={handleRetry}
        disabled={retrying}
        className="text-xs font-semibold px-3 py-1 rounded-lg shrink-0 transition-all active:scale-95 disabled:opacity-40 bg-danger text-white"
      >
        {retrying ? 'Retrying…' : 'Retry'}
      </button>
    </div>
  )
}
