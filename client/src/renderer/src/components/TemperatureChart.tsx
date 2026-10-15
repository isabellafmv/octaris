import { useEffect, useMemo, useRef } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { SensorHistory } from '../stores/temperature'
import { themeColor } from './temperatureColors'

export interface ChartSeries {
  sensor: string
  name: string
  colorVar: string // e.g. --color-chart-1
  history: SensorHistory
  // Draw the target as a dashed line (where it's set; 0 means off)
  showTarget: boolean
}

interface TemperatureChartProps {
  series: ChartSeries[]
  // Live: show the last `windowS` seconds, sliding with the clock.
  // Without it the chart fits all the data (a finished print).
  windowS?: number
  height?: number
}

const FONT = '11px DM Sans, system-ui, sans-serif'

function buildData(series: ChartSeries[]): uPlot.AlignedData {
  if (series.length === 0) return [[]]
  // One table per sensor; join() lines them up on a shared time axis
  const tables = series.map(({ history, showTarget }) => {
    const table: (number | null)[][] = [history.timestamps, history.actual]
    // Off (0) and no target are gaps, not a line at 0 °C
    if (showTarget) table.push(history.target.map((t) => (t ? t : null)))
    return table as uPlot.AlignedData
  })
  return uPlot.join(tables)
}

function formatTime(seconds: number, withDate: boolean): string {
  const date = new Date(seconds * 1000)
  const time = date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
  return withDate ? `${date.toLocaleDateString()} ${time}` : time
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

// Live: the last `windowS` seconds up to now
function showWindow(u: uPlot, windowS: number | undefined): void {
  if (windowS === undefined) return
  const now = Date.now() / 1000
  u.setScale('x', { min: now - windowS, max: now })
}

function formatTemp(value: number | null | undefined): string {
  return value == null ? '–' : `${value.toFixed(1)} °C`
}

function buildOptions(
  series: ChartSeries[],
  width: number,
  height: number,
  tooltip: HTMLDivElement,
  withDate: boolean
): uPlot.Options {
  const axisColor = themeColor('--color-text-muted')
  const gridColor = themeColor('--color-border')
  const axis: uPlot.Axis = {
    stroke: axisColor,
    font: FONT,
    grid: { stroke: gridColor, width: 1 },
    ticks: { stroke: gridColor, width: 1, size: 4 }
  }
  const lines: uPlot.Series[] = [{}]
  // Which data column each sensor's actual and target are in
  const columns: { s: ChartSeries; actual: number; target: number | null }[] = []
  for (const s of series) {
    const color = themeColor(s.colorVar)
    const actual = lines.length
    lines.push({ label: s.name, stroke: color, width: 2, points: { show: false } })
    let target: number | null = null
    if (s.showTarget) {
      target = lines.length
      lines.push({
        label: `${s.name} target`,
        stroke: color,
        width: 1.5,
        dash: [6, 4],
        points: { show: false }
      })
    }
    columns.push({ s, actual, target })
  }

  const showTooltip = (u: uPlot): void => {
    const idx = u.cursor.idx
    if (idx == null || u.cursor.left == null || u.cursor.left < 0) {
      tooltip.style.display = 'none'
      return
    }
    const rows = columns
      .map(({ s, actual, target }) => {
        const value = u.data[actual][idx]
        if (value == null) return ''
        const targetValue = target === null ? null : u.data[target][idx]
        const targetText =
          targetValue == null ? '' : ` <span class="opacity-60">→ ${formatTemp(targetValue)}</span>`
        return `<div class="flex items-center gap-1.5"><span class="w-2 h-2 rounded-full shrink-0" style="background:var(${s.colorVar})"></span><span class="text-text-secondary">${escapeHtml(s.name)}</span><span class="ml-auto pl-3 font-semibold text-text">${formatTemp(value)}</span>${targetText}</div>`
      })
      .join('')
    if (!rows) {
      tooltip.style.display = 'none'
      return
    }
    tooltip.innerHTML = `<div class="text-text-muted mb-1">${formatTime(u.data[0][idx], withDate)}</div>${rows}`
    tooltip.style.display = 'block'
    // Keep it inside the plot: flip to the left of the cursor near the right edge
    const left = u.cursor.left
    const flip = left > u.over.clientWidth / 2
    tooltip.style.left = flip ? '' : `${left + 12}px`
    tooltip.style.right = flip ? `${u.over.clientWidth - left + 12}px` : ''
    tooltip.style.top = '8px'
  }

  return {
    width,
    height,
    series: lines,
    legend: { show: false },
    cursor: { points: { size: 8, width: 2 }, y: false },
    scales: {
      x: { time: true },
      // A little headroom, so a line at a target isn't drawn on the plot's edge
      y: { range: (_u, min, max) => [Math.floor(min - 2), Math.ceil(max + 2)] }
    },
    axes: [
      {
        ...axis,
        space: 80,
        // Seconds only when ticks are less than a minute apart
        values: (_u, ticks, _axis, _space, step) =>
          ticks.map((t) => formatTime(t, false).slice(0, step < 60 ? 8 : 5))
      },
      { ...axis, size: 52, values: (_u, ticks) => ticks.map((t) => `${t}°C`) }
    ],
    hooks: {
      setCursor: [showTooltip],
      ready: [(u) => u.over.appendChild(tooltip)]
    }
  }
}

export function TemperatureChart({
  series,
  windowS,
  height = 260
}: TemperatureChartProps): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null)
  const plotRef = useRef<uPlot | null>(null)
  const data = useMemo(() => buildData(series), [series])
  // The latest props, for effects that only rerun when the layout changes
  const latest = useRef({ series, data, windowS })
  useEffect(() => {
    latest.current = { series, data, windowS }
  })

  // (Re)create the plot when the set of lines changes
  const layout = series.map((s) => `${s.sensor}:${s.colorVar}:${s.showTarget}`).join(',')
  const live = windowS !== undefined
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const tooltip = document.createElement('div')
    tooltip.className =
      'absolute z-10 pointer-events-none hidden rounded-lg px-2.5 py-2 text-[11px] leading-5 bg-surface border border-border shadow-[0_4px_14px_rgba(0,0,0,0.12)] min-w-40'
    const { series, data } = latest.current
    const u = new uPlot(buildOptions(series, el.clientWidth, height, tooltip, !live), data, el)
    plotRef.current = u
    showWindow(u, latest.current.windowS)
    const resize = new ResizeObserver(() => u.setSize({ width: el.clientWidth, height }))
    resize.observe(el)
    return () => {
      resize.disconnect()
      u.destroy()
      plotRef.current = null
    }
  }, [layout, height, live])

  useEffect(() => {
    const u = plotRef.current
    if (!u) return
    u.setData(data)
    showWindow(u, windowS)
  }, [data, windowS])

  // Slide the window even while no readings come in
  useEffect(() => {
    if (!live) return
    const timer = setInterval(() => {
      if (plotRef.current) showWindow(plotRef.current, latest.current.windowS)
    }, 5000)
    return () => clearInterval(timer)
  }, [live])

  return <div ref={containerRef} className="relative w-full" />
}
