import { useState } from 'react'
import { api } from '../api'
import type { SyringeMode } from '../types'

interface JogPanelProps {
  syringeMode: SyringeMode
  disabled: boolean
}

const STEP_SIZES = [0.1, 1, 10]

const AXES_BY_MODE: Record<SyringeMode, Set<string>> = {
  left: new Set(['X', 'Y', 'Z', 'B']),
  right: new Set(['X', 'Y', 'A', 'C']),
  both: new Set(['X', 'Y', 'Z', 'A', 'B', 'C'])
}

function JogButton({
  label,
  axis,
  direction,
  step,
  disabled
}: {
  label: string
  axis: string
  direction: 1 | -1
  step: number
  disabled: boolean
}) {
  const handleClick = () => {
    if (!disabled) api.jog(axis, step * direction)
  }

  return (
    <button
      onClick={handleClick}
      disabled={disabled}
      className="min-h-[48px] min-w-[48px] px-3 py-2 rounded-lg border text-base font-medium active:scale-95 transition-transform disabled:opacity-30"
    >
      {label}
    </button>
  )
}

export function JogPanel({ syringeMode, disabled }: JogPanelProps) {
  const [step, setStep] = useState(1)
  const allowed = AXES_BY_MODE[syringeMode]

  return (
    <div className="space-y-4">
      {/* Step size selector */}
      <div className="flex gap-2">
        {STEP_SIZES.map((s) => (
          <button
            key={s}
            onClick={() => setStep(s)}
            className={`min-h-[48px] px-4 py-2 rounded-lg text-base font-medium transition-all ${
              step === s ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 border'
            }`}
          >
            {s} mm
          </button>
        ))}
      </div>

      <div className="flex gap-8 flex-wrap">
        {/* XY D-pad */}
        <div className="flex flex-col items-center gap-1">
          <div className="text-sm font-medium text-gray-500 mb-1">Build Plate (XY)</div>
          <JogButton label="Y+" axis="Y" direction={1} step={step} disabled={disabled} />
          <div className="flex gap-1">
            <JogButton label="X-" axis="X" direction={-1} step={step} disabled={disabled} />
            <div className="w-12 h-12" />
            <JogButton label="X+" axis="X" direction={1} step={step} disabled={disabled} />
          </div>
          <JogButton label="Y-" axis="Y" direction={-1} step={step} disabled={disabled} />
        </div>

        {/* Z axis (left extruder) */}
        {allowed.has('Z') && (
          <div className="flex flex-col items-center gap-1">
            <div className="text-sm font-medium text-gray-500 mb-1">Left Head (Z)</div>
            <JogButton label="Z+" axis="Z" direction={1} step={step} disabled={disabled} />
            <JogButton label="Z-" axis="Z" direction={-1} step={step} disabled={disabled} />
          </div>
        )}

        {/* A axis (right extruder) */}
        {allowed.has('A') && (
          <div className="flex flex-col items-center gap-1">
            <div className="text-sm font-medium text-gray-500 mb-1">Right Head (A)</div>
            <JogButton label="A+" axis="A" direction={1} step={step} disabled={disabled} />
            <JogButton label="A-" axis="A" direction={-1} step={step} disabled={disabled} />
          </div>
        )}

        {/* B axis (left extrusion) */}
        {allowed.has('B') && (
          <div className="flex flex-col items-center gap-1">
            <div className="text-sm font-medium text-gray-500 mb-1">Left Extrude (B)</div>
            <JogButton label="B+" axis="B" direction={1} step={step} disabled={disabled} />
            <JogButton label="B-" axis="B" direction={-1} step={step} disabled={disabled} />
          </div>
        )}

        {/* C axis (right extrusion) */}
        {allowed.has('C') && (
          <div className="flex flex-col items-center gap-1">
            <div className="text-sm font-medium text-gray-500 mb-1">Right Extrude (C)</div>
            <JogButton label="C+" axis="C" direction={1} step={step} disabled={disabled} />
            <JogButton label="C-" axis="C" direction={-1} step={step} disabled={disabled} />
          </div>
        )}
      </div>
    </div>
  )
}
