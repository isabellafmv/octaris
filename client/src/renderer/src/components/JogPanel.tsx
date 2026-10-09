import { useState } from 'react'
import { api } from '../api'
import { usePrintSettings } from '../stores/printSettings'
import type { SyringeMode } from '../types'

interface JogPanelProps {
  syringeMode: SyringeMode
  disabled: boolean
}

const STEPS: number[] = [0.1, 1, 5]
const MIN_STEP = 0.01
const MAX_STEP = 20

function ArrowButton({
  label,
  axisLabel,
  axis,
  direction,
  step,
  disabled,
  children
}: {
  label: string
  axisLabel: string
  axis: string
  direction: 1 | -1
  step: number
  disabled: boolean
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <button
      onClick={() => !disabled && api.jog(axis, step * direction)}
      disabled={disabled}
      aria-label={label}
      className="w-16 h-16 rounded-full flex flex-col items-center justify-center gap-0.5 text-lg font-light transition-all active:scale-90 disabled:opacity-30 bg-surface-sunken text-text"
    >
      {children}
      <span className="text-[10px] font-semibold tracking-wider text-text-muted">{axisLabel}</span>
    </button>
  )
}

// Each nozzle has its own height motor: Z for the left one, A for the right
function HeightAxisLabel({ axis, nozzle }: { axis: string; nozzle: string }): React.JSX.Element {
  return (
    <div className="w-16 h-16 rounded-full flex flex-col items-center justify-center text-text-muted">
      <span className="text-xs font-semibold tracking-widest">{axis}</span>
      <span className="text-[9px]">{nozzle}</span>
    </div>
  )
}

export function JogPanel({
  syringeMode,
  disabled: panelDisabled
}: JogPanelProps): React.JSX.Element {
  const step = usePrintSettings((s) => s.jogStep)
  const setStep = usePrintSettings((s) => s.setJogStep)
  // Raw text of the custom field; pre-filled when the stored step is a custom one
  const [customText, setCustomText] = useState(() => (STEPS.includes(step) ? '' : String(step)))

  const customValue = parseFloat(customText)
  const customValid = customValue >= MIN_STEP && customValue <= MAX_STEP
  const customInvalid = customText !== '' && !customValid
  const customActive = customText !== '' && customValid && step === customValue
  // Don't jog by a stale step while the custom field holds a bad value
  const disabled = panelDisabled || customInvalid

  const handleCustomChange = (text: string): void => {
    setCustomText(text)
    const value = parseFloat(text)
    if (value >= MIN_STEP && value <= MAX_STEP) setStep(value)
  }

  return (
    <div className="flex flex-col items-center gap-3">
      {/* Step size selector */}
      <div className="flex flex-col items-center gap-1">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">
            Step
          </span>
          <div className="flex rounded-lg p-0.5 bg-surface-sunken">
            {STEPS.map((s) => (
              <button
                key={s}
                onClick={() => {
                  setStep(s)
                  setCustomText('')
                }}
                className={`px-3 py-1 rounded-md text-xs font-semibold transition-all ${
                  step === s && !customActive
                    ? 'bg-white text-primary shadow-[0_1px_3px_rgba(0,0,0,0.08)]'
                    : 'text-text-muted'
                }`}
              >
                {s} mm
              </button>
            ))}
            <label
              className={`flex items-center gap-1 px-2 rounded-md text-xs font-semibold transition-all ${
                customActive
                  ? 'bg-white text-primary shadow-[0_1px_3px_rgba(0,0,0,0.08)]'
                  : 'text-text-muted'
              }`}
            >
              <input
                type="number"
                min={MIN_STEP}
                max={MAX_STEP}
                step="0.01"
                placeholder="custom"
                value={customText}
                onChange={(e) => handleCustomChange(e.target.value)}
                aria-label="Custom step size in mm"
                aria-invalid={customInvalid}
                className={`w-16 bg-transparent outline-none text-right placeholder:text-text-subtle placeholder:font-normal ${
                  customInvalid ? 'text-danger' : ''
                }`}
              />
              mm
            </label>
          </div>
        </div>
        {customInvalid && (
          <p className="text-[10px] text-danger">
            Step must be between {MIN_STEP} and {MAX_STEP} mm
          </p>
        )}
      </div>

      <div className="flex gap-4 items-center justify-center">
        {/* XY cross */}
        <div className="flex flex-col items-center gap-2">
          <ArrowButton
            label="Y+"
            axisLabel="Y+"
            axis="Y"
            direction={1}
            step={step}
            disabled={disabled}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="w-6 h-6"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
            </svg>
          </ArrowButton>

          <div className="flex items-center gap-2">
            <ArrowButton
              label="X-"
              axisLabel="X-"
              axis="X"
              direction={-1}
              step={step}
              disabled={disabled}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="w-6 h-6"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M15.75 19.5L8.25 12l7.5-7.5"
                />
              </svg>
            </ArrowButton>

            <div className="w-16 h-16 rounded-full flex items-center justify-center text-xs font-semibold tracking-widest text-text-muted">
              XY
            </div>

            <ArrowButton
              label="X+"
              axisLabel="X+"
              axis="X"
              direction={1}
              step={step}
              disabled={disabled}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="w-6 h-6"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
              </svg>
            </ArrowButton>
          </div>

          <ArrowButton
            label="Y-"
            axisLabel="Y-"
            axis="Y"
            direction={-1}
            step={step}
            disabled={disabled}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="w-6 h-6"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </ArrowButton>
        </div>

        {/* Z axis */}
        <div className="flex flex-col items-center gap-2">
          <ArrowButton
            label="Z+"
            axisLabel="Z+"
            axis="Z"
            direction={1}
            step={step}
            disabled={disabled}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="w-6 h-6"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
            </svg>
          </ArrowButton>

          <HeightAxisLabel axis="Z" nozzle="left" />

          <ArrowButton
            label="Z-"
            axisLabel="Z-"
            axis="Z"
            direction={-1}
            step={step}
            disabled={disabled}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="w-6 h-6"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </ArrowButton>
        </div>

        {/* A axis — the right nozzle's height, whenever it prints */}
        {(syringeMode === 'right' || syringeMode === 'both') && (
          <div className="flex flex-col items-center gap-2">
            <ArrowButton
              label="A+"
              axisLabel="A+"
              axis="A"
              direction={1}
              step={step}
              disabled={disabled}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="w-6 h-6"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
              </svg>
            </ArrowButton>

            <HeightAxisLabel axis="A" nozzle="right" />

            <ArrowButton
              label="A-"
              axisLabel="A-"
              axis="A"
              direction={-1}
              step={step}
              disabled={disabled}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="w-6 h-6"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19.5 8.25l-7.5 7.5-7.5-7.5"
                />
              </svg>
            </ArrowButton>
          </div>
        )}
      </div>
    </div>
  )
}
