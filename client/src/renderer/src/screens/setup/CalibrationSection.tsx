import { api } from '../../api'
import { JogPanel } from '../../components/JogPanel'
import { usePrintSettings } from '../../stores/printSettings'
import type { PrintStatus } from '../../types'

interface CalibrationSectionProps {
  printerConnected: boolean
  printStatus: PrintStatus
  calibrated: boolean
  // Once a file is ready the jog panel is hidden to make room for its preview
  hasUpload: boolean
  onCalibrated: () => void
  onError: (msg: string) => void
}

export function CalibrationSection({
  printerConnected,
  printStatus,
  calibrated,
  hasUpload,
  onCalibrated,
  onError
}: CalibrationSectionProps): React.JSX.Element {
  const syringeMode = usePrintSettings((s) => s.syringeMode)
  const isPrinting = printStatus === 'printing'
  const isPausedOrPrinting = isPrinting || printStatus === 'paused'

  const handleSetOrigin = async (): Promise<void> => {
    try {
      await api.calibrationZero()
      onCalibrated()
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Calibration failed')
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold tracking-widest uppercase text-text-muted">
            {hasUpload ? 'Calibration' : 'Manual Navigation'}
          </span>
          {calibrated && (
            <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-primary-muted text-primary">
              Origin set
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            className="text-xs font-semibold px-3 py-1 rounded-lg transition-all active:scale-95 disabled:opacity-40 bg-surface-sunken text-text-muted"
            disabled={!printerConnected || isPrinting}
            onClick={() => api.sendGcode('G1 X0 Y0 F300')}
          >
            Go to Origin
          </button>
          <button
            className={`text-xs font-semibold px-3 py-1 rounded-lg transition-all active:scale-95 disabled:opacity-40 ${
              calibrated ? 'bg-surface-sunken text-text-muted' : 'bg-primary text-white'
            }`}
            disabled={!printerConnected || isPausedOrPrinting}
            onClick={handleSetOrigin}
          >
            {calibrated ? 'Re-zero Origin' : 'Set Origin'}
          </button>
        </div>
      </div>
      {(syringeMode === 'right' || syringeMode === 'both') && (
        <p className="text-[9px] mt-1 mb-1 text-warning">
          Always zero at the left nozzle the right nozzle offset is applied automatically.
        </p>
      )}
      {isPrinting && (
        <p className="text-[9px] mt-1 mb-1 text-text-subtle">
          Print running — use Monitor to pause or stop
        </p>
      )}
      {!hasUpload && (
        <JogPanel syringeMode={syringeMode} disabled={!printerConnected || isPrinting} />
      )}
    </div>
  )
}
