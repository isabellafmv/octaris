import { useState } from 'react'
import { createPortal } from 'react-dom'
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

// What to do before each zero, per mode. X/Y are set at the printing nozzle's
// position over the start point (the right nozzle in right mode, else the
// left); each nozzle's height (Z left, A right) with that nozzle lowered onto
// the bed.
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
      instruction: 'Jog the right nozzle over the start point and lower it onto the bed (A)',
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

  // The move running, for its spinner
  const [moving, setMoving] = useState<'origin' | 'lower' | null>(null)
  const [confirmLower, setConfirmLower] = useState(false)

  // X0 Y0 at the current height; with lower, then the nozzle(s) down to zero
  // height. One sequence on the backend, which restores the positioning mode.
  const goToOrigin = async (lower: boolean): Promise<void> => {
    setMoving(lower ? 'lower' : 'origin')
    try {
      await api.goToOrigin(lower)
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Move failed')
    } finally {
      setMoving(null)
    }
  }

  const moveDisabled = !printerConnected || isPrinting || moving !== null

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
          <MoveButton
            label="Go to Origin"
            busy={moving === 'origin'}
            disabled={moveDisabled}
            onClick={() => goToOrigin(false)}
          />
          {/* Zero height only means something once the nozzle(s) are zeroed */}
          <MoveButton
            label="Lower to zero height"
            busy={moving === 'lower'}
            disabled={moveDisabled || !calibrated}
            onClick={() => setConfirmLower(true)}
          />
        </div>
      </div>

      {confirmLower &&
        createPortal(
          <div className="fixed inset-0 z-[60] flex items-center justify-center bg-[rgba(40,43,43,0.7)]">
            <div
              role="alertdialog"
              aria-modal="true"
              aria-labelledby="lower-title"
              className="rounded-3xl p-7 mx-4 max-w-sm bg-surface text-center shadow-[0_24px_60px_rgba(0,0,0,0.3)]"
            >
              <h2 id="lower-title" className="text-lg font-bold mb-1">
                Lower to zero height?
              </h2>
              <p className="text-sm mb-6 text-text-muted">
                Make sure nothing is on the bed below the nozzle.
              </p>
              <div className="flex flex-col gap-2.5">
                <button
                  autoFocus
                  onClick={() => setConfirmLower(false)}
                  className="w-full py-3 rounded-2xl font-semibold text-sm bg-surface-sunken text-text"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    setConfirmLower(false)
                    goToOrigin(true)
                  }}
                  className="w-full py-3 rounded-2xl font-semibold text-sm text-white bg-primary"
                >
                  Lower
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

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

function MoveButton({
  label,
  busy,
  disabled,
  onClick
}: {
  label: string
  busy: boolean
  disabled: boolean
  onClick: () => void
}): React.JSX.Element {
  return (
    <button
      className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1 rounded-lg transition-all active:scale-95 disabled:opacity-40 bg-surface-sunken text-text-muted"
      disabled={disabled}
      onClick={onClick}
    >
      {busy && (
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="w-3 h-3 animate-spin"
        >
          <path strokeLinecap="round" d="M12 3a9 9 0 1 0 9 9" />
        </svg>
      )}
      {label}
    </button>
  )
}
