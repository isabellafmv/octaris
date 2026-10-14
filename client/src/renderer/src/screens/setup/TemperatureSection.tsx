import { useEffect, useState } from 'react'
import { api } from '../../api'
import { SensorsNotReporting } from '../../components/SensorsNotReporting'
import { useTemperature } from '../../stores/temperature'
import type { SensorTestResponse } from '../../types'

type TestReading = SensorTestResponse['sensors'][number]

function describe(s: TestReading): string {
  const reading = `${s.name} ${s.actual.toFixed(1)} °C`
  if (!s.target) return reading
  if (s.status === 'at_target') return `${reading} (at ${s.target} °C)`
  return `${reading} (${s.status} to ${s.target} °C)`
}

interface TemperatureSectionProps {
  printerConnected: boolean
  // The temperature_sensors setting, as the backend last reported it
  enabled: boolean
  onError: (msg: string) => void
}

// The temperature_sensors setting, and a one-off M105 to check the printer's sensors
export function TemperatureSection({
  printerConnected,
  enabled,
  onError
}: TemperatureSectionProps): React.JSX.Element {
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<SensorTestResponse | null>(null)
  // Lab firmware: its heater channels, listed by name in the test result
  const channels = useTemperature((s) => s.status?.channels) ?? []

  // Whether polling is paused comes with the temperature status
  useEffect(() => {
    api
      .getTemperature()
      .then(useTemperature.getState().applyStatus)
      .catch(() => {})
  }, [printerConnected, enabled])

  const handleToggle = async (): Promise<void> => {
    setSaving(true)
    try {
      // The settings event that follows updates `enabled`
      await api.saveSettings({ temperature_sensors: !enabled })
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Saving the setting failed')
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async (): Promise<void> => {
    setTesting(true)
    setTest(null)
    try {
      setTest(await api.testSensors())
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Sensor test failed')
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-semibold tracking-widest uppercase text-text-muted">
        Temperature control
      </span>

      <div className="flex items-center justify-between gap-3">
        <label className="flex items-center gap-2.5 text-sm cursor-pointer">
          <button
            role="switch"
            aria-checked={enabled}
            onClick={handleToggle}
            disabled={saving}
            className={`relative w-9 h-5 rounded-full shrink-0 transition-colors disabled:opacity-40 ${
              enabled ? 'bg-primary' : 'bg-border-strong'
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                enabled ? 'translate-x-4' : ''
              }`}
            />
          </button>
          This printer has temperature sensors
        </label>
        <button
          onClick={handleTest}
          disabled={!printerConnected || testing}
          title={
            printerConnected ? 'Send M105 once and show the reply' : 'Connect the printer first'
          }
          className="text-xs font-semibold px-3 py-1 rounded-lg shrink-0 transition-all active:scale-95 disabled:opacity-40 bg-surface-sunken text-text-muted"
        >
          {testing ? 'Testing…' : 'Test sensors'}
        </button>
      </div>

      {test && (
        <div className="rounded-lg px-3 py-2 text-xs bg-surface-card">
          {test.reply === null ? (
            <p className="text-danger">No reply</p>
          ) : (
            <>
              <pre className="font-mono whitespace-pre-wrap break-all text-text-secondary">
                {test.reply}
              </pre>
              {channels.length > 0 ? (
                <ul className="mt-1 text-text">
                  {channels.map((c) => {
                    const found = test.sensors.find((s) => s.sensor === c.sensor)
                    return (
                      <li key={c.sensor}>
                        <span className="font-mono text-text-subtle">{c.sensor}</span>{' '}
                        {found ? (
                          describe(found)
                        ) : (
                          <span className="text-danger">
                            {c.name}: not in the reply (no {c.reply_key})
                          </span>
                        )}
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <p className="mt-1 text-text">
                  {test.sensors.length === 0
                    ? 'No sensors found in the reply'
                    : test.sensors.map(describe).join(' · ')}
                </p>
              )}
            </>
          )}
        </div>
      )}

      {enabled && <SensorsNotReporting onError={onError} />}
    </div>
  )
}
