import { useEffect, useState } from 'react'
import { api } from '../../api'
import { SensorsNotReporting } from '../../components/SensorsNotReporting'
import { UNUSED, useOptions, withUse } from '../../components/temperatureChannels'
import { usePrinter } from '../../stores/printer'
import { useTemperature } from '../../stores/temperature'
import type { SensorTestResponse, TemperatureProtocol } from '../../types'

type TestReading = SensorTestResponse['sensors'][number]

// How the heaters are addressed: the temperature_protocol setting
const PROTOCOLS: { value: NonNullable<TemperatureProtocol>; label: string }[] = [
  { value: 'marlin', label: 'Marlin (M104/M140)' },
  { value: 'printess', label: 'Printess lab firmware (M104 I<n> T<°C> H/C)' }
]

function describe(s: TestReading): string {
  // Lab firmware: a disabled channel has no reading, only its status
  if (s.actual === null) return `${s.name}: ${s.firmware_status ?? 'no reading'}`
  const reading = `${s.name} ${s.actual.toFixed(1)} °C`
  const moving = s.firmware_status ?? s.status
  if (!s.target) return s.firmware_status ? `${reading} (${s.firmware_status})` : reading
  if (s.status === 'at_target') return `${reading} (${moving}, at ${s.target} °C)`
  return `${reading} (${moving} to ${s.target} °C)`
}

interface TemperatureSectionProps {
  printerConnected: boolean
  // The temperature_sensors setting, as the backend last reported it
  enabled: boolean
  onError: (msg: string) => void
}

// The temperature_sensors, temperature_protocol and (lab firmware)
// temperature_channels settings, and a one-off M105 to check the printer's
// sensors
export function TemperatureSection({
  printerConnected,
  enabled,
  onError
}: TemperatureSectionProps): React.JSX.Element {
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<SensorTestResponse | null>(null)
  // Lab firmware: its heater channels, what each heats, and their names
  const channels = useTemperature((s) => s.status?.channels) ?? []
  // The protocol in use: the one chosen here, or config.json's until then
  const protocol = useTemperature((s) => s.status?.protocol)
  // A channel heats one of this profile's syringes, or the bed
  const profile = usePrinter((s) => s.profile)
  const options = useOptions(profile)

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

  const handleProtocol = async (value: NonNullable<TemperatureProtocol>): Promise<void> => {
    if (value === protocol) return
    setSaving(true)
    try {
      // The temperature_status event that follows updates `protocol`
      await api.saveSettings({ temperature_protocol: value })
      setTest(null) // read with the old protocol's sensor names
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Saving the setting failed')
    } finally {
      setSaving(false)
    }
  }

  const handleUse = async (sensor: string, use: string): Promise<void> => {
    setSaving(true)
    try {
      // The temperature_status event that follows updates `channels`
      await api.saveSettings({ temperature_channels: withUse(channels, sensor, use) })
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

      <div
        role="radiogroup"
        aria-label="Temperature protocol"
        className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-secondary"
      >
        <span className="text-text-muted">Protocol</span>
        {PROTOCOLS.map((p) => (
          <label key={p.value} className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="radio"
              name="temperature-protocol"
              value={p.value}
              checked={protocol === p.value}
              onChange={() => handleProtocol(p.value)}
              disabled={saving}
            />
            {p.label}
          </label>
        ))}
      </div>

      {protocol === 'printess' && channels.length > 0 && (
        <div className="flex flex-col gap-1 text-xs text-text-secondary">
          <span className="text-text-muted">
            What each channel heats (it can change between prints)
          </span>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {channels.map((c) => (
              <label key={c.sensor} className="flex items-center gap-2">
                <span className="font-mono text-text-subtle w-5">{c.sensor}</span>
                <select
                  value={c.use ?? UNUSED}
                  onChange={(e) => handleUse(c.sensor, e.target.value)}
                  disabled={saving}
                  aria-label={`What channel ${c.sensor} heats`}
                  className="flex-1 min-w-0 rounded px-2 py-1 bg-surface-card border border-border text-text disabled:opacity-50"
                >
                  {options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
        </div>
      )}

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
                          <span className="text-danger">{c.name}: not in the reply</span>
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
