import { useState } from 'react'
import { api } from '../api'
import type { SyringeMode } from '../types'

interface JogPanelProps {
  syringeMode: SyringeMode
  disabled: boolean
}

const STEPS = [0.1, 1, 5] as const

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

export function JogPanel({ syringeMode, disabled }: JogPanelProps): React.JSX.Element {
  const [step, setStep] = useState<number>(5)

  return (
    <div className="flex flex-col items-center gap-3">
      {/* Step size selector */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">
          Step
        </span>
        <div className="flex rounded-lg p-0.5 bg-surface-sunken">
          {STEPS.map((s) => (
            <button
              key={s}
              onClick={() => setStep(s)}
              className={`px-3 py-1 rounded-md text-xs font-semibold transition-all ${
                step === s
                  ? 'bg-white text-primary shadow-[0_1px_3px_rgba(0,0,0,0.08)]'
                  : 'text-text-muted'
              }`}
            >
              {s} mm
            </button>
          ))}
        </div>
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

          <div className="w-16 h-16 rounded-full flex items-center justify-center text-xs font-semibold tracking-widest text-text-muted">
            Z
          </div>

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

        {/* A axis — only in dual syringe mode */}
        {syringeMode === 'both' && (
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

            <div className="w-16 h-16 rounded-full flex items-center justify-center text-xs font-semibold tracking-widest text-text-muted">
              A
            </div>

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
