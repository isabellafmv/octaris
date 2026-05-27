import { api } from '../api'
import type { SyringeMode } from '../types'

interface JogPanelProps {
  syringeMode: SyringeMode
  disabled: boolean
}

const STEP = 5

function ArrowButton({
  label,
  axisLabel,
  axis,
  direction,
  disabled,
  children
}: {
  label: string
  axisLabel: string
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
  return (
    <div className="flex gap-4 items-center justify-center">
      {/* XY cross */}
      <div className="flex flex-col items-center gap-2">
        <ArrowButton label="Y+" axisLabel="Y+" axis="Y" direction={1} disabled={disabled}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
          </svg>
        </ArrowButton>

        <div className="flex items-center gap-2">
          <ArrowButton label="X-" axisLabel="X-" axis="X" direction={-1} disabled={disabled}>
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

          <ArrowButton label="X+" axisLabel="X+" axis="X" direction={1} disabled={disabled}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
              <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
            </svg>
          </ArrowButton>
        </div>

        <ArrowButton label="Y-" axisLabel="Y-" axis="Y" direction={-1} disabled={disabled}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
          </svg>
        </ArrowButton>
      </div>

      {/* Z axis */}
      <div className="flex flex-col items-center gap-2">
        <ArrowButton label="Z+" axisLabel="Z+" axis="Z" direction={1} disabled={disabled}>
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

        <ArrowButton label="Z-" axisLabel="Z-" axis="Z" direction={-1} disabled={disabled}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
          </svg>
        </ArrowButton>
      </div>
    </div>
  )
}
