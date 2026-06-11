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
}) {
  return (
    <button
      onClick={() => !disabled && api.jog(axis, step * direction)}
      disabled={disabled}
      aria-label={label}
      className="w-16 h-16 rounded-full flex flex-col items-center justify-center gap-0.5 text-lg font-light transition-all active:scale-90 disabled:opacity-30"
      style={{ backgroundColor: '#E8E3D8', color: '#2D3333' }}
    >
      {children}
      <span className="text-[10px] font-semibold tracking-wider" style={{ color: '#8B9090' }}>
        {axisLabel}
      </span>
    </button>
  )
}

export function JogPanel({ syringeMode: _syringeMode, disabled }: JogPanelProps) {
  const [step, setStep] = useState<number>(5)

  return (
    <div className="flex flex-col items-center gap-3">
      {/* Step size selector */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold tracking-widest uppercase" style={{ color: '#8B9090' }}>
          Step
        </span>
        <div className="flex rounded-lg p-0.5" style={{ backgroundColor: '#E8E3D8' }}>
          {STEPS.map(s => (
            <button
              key={s}
              onClick={() => setStep(s)}
              className="px-3 py-1 rounded-md text-xs font-semibold transition-all"
              style={
                step === s
                  ? { backgroundColor: 'white', color: '#1A8B8D', boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }
                  : { color: '#8B9090' }
              }
            >
              {s} mm
            </button>
          ))}
        </div>
      </div>

      <div className="flex gap-4 items-center justify-center">
        {/* XY cross */}
        <div className="flex flex-col items-center gap-2">
          <ArrowButton label="Y+" axisLabel="Y+" axis="Y" direction={1} step={step} disabled={disabled}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
            </svg>
          </ArrowButton>

          <div className="flex items-center gap-2">
            <ArrowButton label="X-" axisLabel="X-" axis="X" direction={-1} step={step} disabled={disabled}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
              </svg>
            </ArrowButton>

            <div
              className="w-16 h-16 rounded-full flex items-center justify-center text-xs font-semibold tracking-widest"
              style={{ color: '#8B9090' }}
            >
              XY
            </div>

            <ArrowButton label="X+" axisLabel="X+" axis="X" direction={1} step={step} disabled={disabled}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
              </svg>
            </ArrowButton>
          </div>

          <ArrowButton label="Y-" axisLabel="Y-" axis="Y" direction={-1} step={step} disabled={disabled}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </ArrowButton>
        </div>

        {/* Z axis */}
        <div className="flex flex-col items-center gap-2">
          <ArrowButton label="Z+" axisLabel="Z+" axis="Z" direction={1} step={step} disabled={disabled}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
            </svg>
          </ArrowButton>

          <div
            className="w-16 h-16 rounded-full flex items-center justify-center text-xs font-semibold tracking-widest"
            style={{ color: '#8B9090' }}
          >
            Z
          </div>

          <ArrowButton label="Z-" axisLabel="Z-" axis="Z" direction={-1} step={step} disabled={disabled}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </ArrowButton>
        </div>
      </div>
    </div>
  )
}
