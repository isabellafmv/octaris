import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { SensorCard } from '../components/SensorCard'
import { TemperatureChart, type ChartSeries } from '../components/TemperatureChart'
import { compareSensors, sensorColorVar } from '../components/temperatureColors'
import { RANGES_MIN, useTemperature } from '../stores/temperature'

interface TemperatureScreenProps {
  printerConnected: boolean
  // id changes on every error or warning event, so a repeated one shows again
  lastError: { id: number; message: string } | null
}

function EmptyState({ title, hint }: { title: string; hint: string }): React.JSX.Element {
  return (
    <div className="rounded-2xl px-6 py-10 flex flex-col items-center text-center gap-1 bg-surface-card">
      <p className="text-sm font-semibold text-text-secondary">{title}</p>
      <p className="text-xs text-text-muted max-w-sm">{hint}</p>
    </div>
  )
}

function LineKey({ dashed }: { dashed?: boolean }): React.JSX.Element {
  return (
    <svg width="18" height="6" aria-hidden="true">
      <line
        x1="1"
        y1="3"
        x2="17"
        y2="3"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={dashed ? '4 3' : undefined}
      />
    </svg>
  )
}

export function TemperatureScreen({
  printerConnected,
  lastError
}: TemperatureScreenProps): React.JSX.Element {
  const status = useTemperature((s) => s.status)
  const sensors = useTemperature((s) => s.sensors)
  const history = useTemperature((s) => s.history)
  const rangeMin = useTemperature((s) => s.rangeMin)
  const setRange = useTemperature((s) => s.setRange)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  // Show each new warning or error once (until dismissed)
  const [shownErrorId, setShownErrorId] = useState<number | null>(null)
  if (lastError && lastError.id !== shownErrorId) {
    setShownErrorId(lastError.id)
    setError(lastError.message)
  }

  // The current state, and the chart's longest range from before this screen opened
  useEffect(() => {
    const { applyStatus, backfill } = useTemperature.getState()
    api
      .getTemperature()
      .then(applyStatus)
      .catch(() => {})
    api
      .getTemperatureHistory(RANGES_MIN[RANGES_MIN.length - 1])
      .then(({ series }) => backfill(series))
      .catch(() => {})
  }, [printerConnected])

  const state = status?.state ?? (printerConnected ? 'waiting' : 'disconnected')
  const readings = useMemo(() => status?.sensors ?? [], [status])

  const chartSeries = useMemo<ChartSeries[]>(() => {
    const all = Object.keys(sensors)
    return all
      .filter((key) => history[key]?.timestamps.length)
      .sort(compareSensors)
      .map((key) => ({
        sensor: key,
        name: sensors[key].name,
        colorVar: sensorColorVar(key, all),
        history: history[key],
        showTarget: sensors[key].settable
      }))
  }, [sensors, history])
  const allSensors = Object.keys(sensors)

  const handleExport = async (): Promise<void> => {
    setError(null)
    setExporting(true)
    try {
      const to = new Date()
      await api.exportTemperatureCsv({ from: new Date(to.getTime() - rangeMin * 60_000), to })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex flex-col h-full overflow-y-auto text-text">
      {/* Header */}
      <div className="flex items-start justify-between px-8 pt-7 pb-4 shrink-0">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-primary">Temperature</h1>
          <p className="text-xs tracking-widest uppercase mt-1 text-text-muted">
            Heaters &amp; sensors
          </p>
        </div>
      </div>

      {error && (
        <div className="mx-8 mb-3 px-4 py-2 rounded-lg text-sm flex items-start justify-between gap-3 bg-danger-muted text-danger">
          <span>{error}</span>
          <button
            onClick={() => setError(null)}
            className="font-bold text-lg leading-none shrink-0"
          >
            &times;
          </button>
        </div>
      )}

      <div className="flex flex-col gap-5 px-8 pb-6">
        {/* Current readings */}
        {state === 'no_sensors' ? (
          <EmptyState
            title="No temperature sensor reported"
            hint="The printer is connected but hasn't reported any temperature. Check that its firmware has temperature sensors configured."
          />
        ) : state === 'waiting' ? (
          <EmptyState
            title="Waiting for the first temperature report…"
            hint="The printer reports its temperatures every couple of seconds."
          />
        ) : state === 'disconnected' ? (
          <EmptyState
            title="Printer not connected"
            hint="Connect the printer on the Setup screen to read and set its temperatures."
          />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
            {readings.map((reading) => (
              <SensorCard
                key={reading.sensor}
                reading={reading}
                colorVar={sensorColorVar(reading.sensor, allSensors)}
                disabled={!printerConnected}
              />
            ))}
          </div>
        )}

        {/* Chart; no point in an empty one when the printer has no sensors */}
        {state !== 'no_sensors' && (
          <div className="rounded-2xl p-4 bg-surface-card">
            <div className="flex items-center justify-between gap-3 mb-3">
              <span className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">
                Actual vs target
              </span>
              <div className="flex items-center gap-2">
                <div className="flex rounded-lg p-0.5 bg-surface-sunken" role="group">
                  {RANGES_MIN.map((minutes) => (
                    <button
                      key={minutes}
                      onClick={() => setRange(minutes)}
                      aria-pressed={rangeMin === minutes}
                      className={`px-2.5 py-1 rounded-md text-xs font-semibold transition-all ${
                        rangeMin === minutes
                          ? 'bg-surface text-primary shadow-sm'
                          : 'text-text-muted'
                      }`}
                    >
                      {minutes} min
                    </button>
                  ))}
                </div>
                <button
                  onClick={handleExport}
                  disabled={exporting || chartSeries.length === 0}
                  className="text-xs font-semibold px-3 py-1.5 rounded-lg transition-all active:scale-95 disabled:opacity-40 bg-primary text-white"
                >
                  {exporting ? 'Exporting…' : 'Export CSV'}
                </button>
              </div>
            </div>

            {chartSeries.length === 0 ? (
              <p className="py-16 text-center text-xs text-text-muted">No readings yet</p>
            ) : (
              <>
                <TemperatureChart series={chartSeries} windowS={rangeMin * 60} height={280} />
                {/* Legend: identity by color and name, actual vs target by line style */}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-3 text-xs text-text-secondary">
                  {chartSeries.map((s) => (
                    <span key={s.sensor} className="flex items-center gap-1.5">
                      <span
                        className="w-2.5 h-2.5 rounded-full"
                        style={{ background: `var(${s.colorVar})` }}
                      />
                      {s.name}
                    </span>
                  ))}
                  <span className="flex items-center gap-1.5 ml-auto text-text-muted">
                    <LineKey /> Actual
                    <LineKey dashed /> Target
                  </span>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
