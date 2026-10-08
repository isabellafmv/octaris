import { useState } from 'react'
import { api } from '../api'
import type { SensorReading } from '../types'

const STATUS: Record<SensorReading['status'], { label: string; icon: string; className: string }> =
  {
    heating: { label: 'Heating', icon: '↑', className: 'bg-[#F6E4DC] text-warning' },
    cooling: { label: 'Cooling', icon: '↓', className: 'bg-[#E1E8F2] text-[#3F5F86]' },
    at_target: { label: 'At target', icon: '✓', className: 'bg-primary-muted text-primary' },
    off: { label: 'Off', icon: '○', className: 'bg-surface-sunken text-text-muted' }
  }

interface SensorCardProps {
  reading: SensorReading
  colorVar: string // the sensor's line in the chart
  disabled: boolean
}

export function SensorCard({ reading, colorVar, disabled }: SensorCardProps): React.JSX.Element {
  const { sensor, name, actual, target, min, max, settable } = reading
  // The target field's raw text
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const value = parseFloat(text)
  const valid = value >= min && value <= max
  const invalid = text !== '' && !valid
  const status = STATUS[reading.status]

  const send = async (newTarget: number): Promise<void> => {
    setError(null)
    setSending(true)
    try {
      await api.setTemperatureTarget(sensor, newTarget)
      setText('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Setting the target failed')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="rounded-2xl p-4 flex flex-col gap-3 bg-surface-card">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span
              className="w-2.5 h-2.5 rounded-full shrink-0"
              style={{ background: `var(${colorVar})` }}
            />
            <span className="text-sm font-semibold truncate">{name}</span>
          </div>
          {name !== sensor && (
            <span className="text-[10px] font-mono text-text-subtle ml-4">{sensor}</span>
          )}
        </div>
        <span
          className={`shrink-0 text-[10px] font-semibold px-2 py-0.5 rounded-full ${status.className}`}
        >
          <span aria-hidden="true">{status.icon}</span> {status.label}
        </span>
      </div>

      <div>
        <p className="text-4xl font-bold tracking-tight tabular-nums">
          {actual.toFixed(1)}
          <span className="text-base font-medium ml-1 text-text-muted">°C</span>
        </p>
        <p className="text-xs mt-1 text-text-muted">
          {!settable ? 'Read-only sensor' : target ? `Target ${target} °C` : 'Heater off'}
        </p>
      </div>

      {settable && (
        <div>
          <div className="flex gap-1.5">
            <label className="flex-1 min-w-0 flex items-center gap-1 rounded-lg px-2.5 py-1.5 bg-surface border border-border">
              <input
                type="number"
                min={min}
                max={max}
                step="0.5"
                value={text}
                placeholder={`${min}–${max}`}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && valid && !sending) send(value)
                }}
                disabled={disabled || sending}
                aria-label={`${name} target in °C`}
                aria-invalid={invalid}
                className={`w-full min-w-0 bg-transparent text-sm tabular-nums outline-none disabled:opacity-50 ${
                  invalid ? 'text-danger' : 'text-text'
                }`}
              />
              <span className="text-xs text-text-muted">°C</span>
            </label>
            <button
              onClick={() => send(value)}
              disabled={disabled || sending || !valid}
              className="px-3 rounded-lg text-xs font-semibold text-white bg-primary transition-all active:scale-95 disabled:opacity-40"
            >
              Set
            </button>
            <button
              onClick={() => send(0)}
              disabled={disabled || sending || !target}
              className="px-3 rounded-lg text-xs font-semibold bg-surface-sunken text-text-secondary transition-all active:scale-95 disabled:opacity-40"
            >
              Off
            </button>
          </div>
          {invalid && (
            <p className="mt-1 text-[11px] text-danger">
              Enter {min} to {max} °C, or use Off
            </p>
          )}
          {error && <p className="mt-1 text-[11px] text-danger">{error}</p>}
        </div>
      )}
    </div>
  )
}
