import { api } from '../../api'
import { JogPanel } from '../../components/JogPanel'
import { usePrintSettings } from '../../stores/printSettings'
import type { NozzleCalibration, PrintStatus, SyringeMode } from '../../types'
import { isCalibratedFor, MODE_NOZZLES, type Nozzle } from './calibration'

interface CalibrationSectionProps {
  printerConnected: boolean
  printStatus: PrintStatus
  nozzles: NozzleCalibration
  // Once a file is ready the jog panel is hidden to make room for its preview
  hasUpload: boolean
  onCalibrated: (nozzles: NozzleCalibration) => void
  onError: (msg: string) => void
}

// What to do before each zero, per mode. X/Y are always set at the left
// nozzle's position over the start point; each nozzle's height (Z left,
// A right) with that nozzle lowered onto the bed.
const STEPS: Record<SyringeMode, { nozzle: Nozzle; instruction: string; label: string }[]> = {
  left: [
    {
      nozzle: 'left',
      instruction: 'Jog the left nozzle over the start point and lower it onto the bed (Z)',
      label: 'Zero left'
    }
  ],
  right: [
    {
      nozzle: 'right',
      instruction:
        'Jog the left nozzle over the start point, then lower the right nozzle onto the bed (A)',
      label: 'Zero right'
    }
  ],
  both: [
    {
      nozzle: 'left',
      instruction: 'Lower the left nozzle onto the bed (Z) over the start point',
      label: 'Zero left'
    },
    {
      nozzle: 'right',
      instruction: 'Now lower the right nozzle onto the bed (A)',
      label: 'Zero right'
    }
  ]
}

export function CalibrationSection({
  printerConnected,
  printStatus,
  nozzles,
  hasUpload,
  onCalibrated,
  onError
}: CalibrationSectionProps): React.JSX.Element {
  const syringeMode = usePrintSettings((s) => s.syringeMode)
  const isPrinting = printStatus === 'printing'
  const isPausedOrPrinting = isPrinting || printStatus === 'paused'
  const calibrated = isCalibratedFor(syringeMode, nozzles)
  const steps = STEPS[syringeMode]

  const handleZero = async (nozzle: Nozzle): Promise<void> => {
    try {
      const result = await api.calibrationZero(nozzle, syringeMode)
      onCalibrated(result.nozzles)
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Calibration failed')
    }
  }

  // The printer may be left relative (G91) by a print: go to the origin in
  // absolute mode, then back to relative, the mode every print runs in.
  const handleGoToOrigin = async (): Promise<void> => {
    try {
      for (const line of ['G90', 'G1 X0 Y0 F300', 'G91']) await api.sendGcode(line)
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Move failed')
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
        <button
          className="text-xs font-semibold px-3 py-1 rounded-lg transition-all active:scale-95 disabled:opacity-40 bg-surface-sunken text-text-muted"
          disabled={!printerConnected || isPrinting}
          onClick={handleGoToOrigin}
        >
          Go to Origin
        </button>
      </div>

      <ol className="flex flex-col gap-2 mb-3">
        {steps.map((step, i) => {
          const done = nozzles[step.nozzle]
          // In both mode the right nozzle is zeroed after the left one has set the start point
          const waiting = MODE_NOZZLES[syringeMode].slice(0, i).some((n) => !nozzles[n])
          return (
            <li key={step.nozzle} className="flex items-center justify-between gap-3">
              <p className={`text-xs ${waiting ? 'text-text-subtle' : 'text-text'}`}>
                {steps.length > 1 && <span className="font-semibold">{i + 1}. </span>}
                {step.instruction}
                {done && <span className="ml-1 text-primary">✓</span>}
              </p>
              <button
                className={`text-xs font-semibold px-3 py-1 rounded-lg shrink-0 transition-all active:scale-95 disabled:opacity-40 ${
                  done ? 'bg-surface-sunken text-text-muted' : 'bg-primary text-white'
                }`}
                disabled={!printerConnected || isPausedOrPrinting || waiting}
                onClick={() => handleZero(step.nozzle)}
              >
                {done ? `Re-${step.label.toLowerCase()}` : step.label}
              </button>
            </li>
          )
        })}
      </ol>
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
