import { useState } from 'react'
import { api } from '../api'
import type { SensorReading } from '../types'

type Badge = { label: string; icon: string; className: string }

const STATUS: Record<SensorReading['status'], Badge> = {
  heating: { label: 'Heating', icon: '↑', className: 'bg-[#F6E4DC] text-warning' },
  cooling: { label: 'Cooling', icon: '↓', className: 'bg-[#E1E8F2] text-[#3F5F86]' },
  at_target: { label: 'At target', icon: '✓', className: 'bg-primary-muted text-primary' },
  off: { label: 'Off', icon: '○', className: 'bg-surface-sunken text-text-muted' }
}

// Lab firmware: its own status, as it reports it (Heating, Cooling, Disabled)
function firmwareBadge(status: string): Badge {
  const style = { heating: STATUS.heating, cooling: STATUS.cooling }[status.toLowerCase()]
  return { ...(style ?? STATUS.off), label: status }
}

interface SensorCardProps {
  reading: SensorReading
  colorVar: string // the sensor's line in the chart
  disabled: boolean
  // The lab firmware's channel: heat or cool to a target, chosen when it has no reading
  printess?: boolean
}

export function SensorCard({
  reading,
  colorVar,
  disabled,
  printess = false
}: SensorCardProps): React.JSX.Element {
  const { sensor, name, actual, target, min, max, settable, mode } = reading
  const firmwareStatus = reading.firmware_status ?? null
  // The target field's raw text
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const value = parseFloat(text)
  const valid = value >= min && value <= max
  const invalid = text !== '' && !valid
  const status = firmwareStatus !== null ? firmwareBadge(firmwareStatus) : STATUS[reading.status]
  // Lab firmware: the firmware's status doesn't say when it got there
  const atTarget = firmwareStatus !== null && reading.status === 'at_target'
  // A disabled channel has no reading to choose heating or cooling from
  const chooseDirection = printess && actual === null

  const send = async (newTarget: number, direction?: 'heat' | 'cool'): Promise<void> => {
    setError(null)
    setSending(true)
    try {
      await api.setTemperatureTarget(sensor, newTarget, direction)
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
          {name !== sensor && !name.endsWith(`(${sensor})`) && (
            <span className="text-[10px] font-mono text-text-subtle ml-4">{sensor}</span>
          )}
        </div>
        <span className="shrink-0 flex flex-col items-end gap-1">
          <span
            className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${status.className}`}
          >
            <span aria-hidden="true">{status.icon}</span> {status.label}
          </span>
          {atTarget && (
            <span
              className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${STATUS.at_target.className}`}
            >
              <span aria-hidden="true">{STATUS.at_target.icon}</span> At target
            </span>
          )}
        </span>
      </div>

      <div>
        <p className="text-4xl font-bold tracking-tight tabular-nums">
          {actual === null ? '—' : actual.toFixed(1)}
          <span className="text-base font-medium ml-1 text-text-muted">°C</span>
        </p>
        <p className="text-xs mt-1 text-text-muted">
          {!settable
            ? 'Read-only sensor'
            : !target
              ? actual === null
                ? 'Off · no reading'
                : 'Heater off'
              : // Lab firmware: a channel only heats (H) or only cools (C) towards its target
                `Target ${target} °C${mode === 'heat' ? ' · heats only' : mode === 'cool' ? ' · cools only' : ''}`}
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
                  if (e.key === 'Enter' && valid && !sending && !chooseDirection) send(value)
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
            {chooseDirection ? (
              <>
                <button
                  onClick={() => send(value, 'heat')}
                  disabled={disabled || sending || !valid}
                  title={`M104 ${sensor} T${valid ? value : '<°C>'} H`}
                  className="px-2.5 rounded-lg text-xs font-semibold text-white bg-primary transition-all active:scale-95 disabled:opacity-40"
                >
                  Heat to
                </button>
                <button
                  onClick={() => send(value, 'cool')}
                  disabled={disabled || sending || !valid}
                  title={`M104 ${sensor} T${valid ? value : '<°C>'} C`}
                  className="px-2.5 rounded-lg text-xs font-semibold text-white bg-primary transition-all active:scale-95 disabled:opacity-40"
                >
                  Cool to
                </button>
              </>
            ) : (
              <button
                onClick={() => send(value)}
                disabled={disabled || sending || !valid}
                title={printess ? 'Heats or cools, from the current reading' : undefined}
                className="px-3 rounded-lg text-xs font-semibold text-white bg-primary transition-all active:scale-95 disabled:opacity-40"
              >
                Set
              </button>
            )}
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
