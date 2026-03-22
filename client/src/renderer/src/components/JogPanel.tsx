import { api } from '../api'
import type { SyringeMode } from '../types'

interface JogPanelProps {
  syringeMode: SyringeMode
  disabled: boolean
}

const STEP = 5

function ArrowButton({
  label,
  axis,
  direction,
  disabled,
  children
}: {
  label: string
  axis: string
  direction: 1 | -1
  disabled: boolean
  children: React.ReactNode
}) {
  return (
    <button
      onClick={() => !disabled && api.jog(axis, STEP * direction)}
      disabled={disabled}
      aria-label={label}
      className="w-16 h-16 rounded-full flex items-center justify-center text-lg font-light transition-all active:scale-90 disabled:opacity-30"
      style={{ backgroundColor: '#E8E3D8', color: '#2D3333' }}
    >
      {children}
    </button>
  )
}

export function JogPanel({ syringeMode: _syringeMode, disabled }: JogPanelProps) {
  return (
    <div className="flex flex-col items-center gap-2">
      {/* Up */}
      <ArrowButton label="Y+" axis="Y" direction={1} disabled={disabled}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-7 h-7">
          <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
        </svg>
      </ArrowButton>

      {/* Middle row */}
      <div className="flex items-center gap-2">
        <ArrowButton label="X-" axis="X" direction={-1} disabled={disabled}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-7 h-7">
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
          </svg>
        </ArrowButton>

        {/* Center label */}
        <div
          className="w-16 h-16 rounded-full flex items-center justify-center text-xs font-semibold tracking-widest"
          style={{ color: '#8B9090' }}
        >
          XYZ
        </div>

        <ArrowButton label="X+" axis="X" direction={1} disabled={disabled}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-7 h-7">
            <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
          </svg>
        </ArrowButton>
      </div>

      {/* Down */}
      <ArrowButton label="Y-" axis="Y" direction={-1} disabled={disabled}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-7 h-7">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </ArrowButton>
    </div>
  )
}
