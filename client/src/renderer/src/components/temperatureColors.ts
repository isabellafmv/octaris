// Chart colors by sensor. Each sensor keeps its color whatever else is
// reported, so a sensor coming or going never repaints the others: the
// known ones have fixed slots, any others take the remaining slots by key.
const FIXED_SLOT: Record<string, number> = { T: 1, T0: 1, T1: 2, B: 3, C: 4 }
const SLOTS = 6

export function sensorColorVar(sensor: string, allSensors: readonly string[]): string {
  const fixed = FIXED_SLOT[sensor]
  if (fixed) return `--color-chart-${fixed}`
  const others = allSensors.filter((s) => !(s in FIXED_SLOT)).sort()
  const slot = 5 + others.indexOf(sensor)
  return slot <= SLOTS ? `--color-chart-${slot}` : '--color-chart-other'
}

// Sensors in a fixed order (by color slot, then key), so the legend doesn't
// reshuffle when a sensor stops or starts reporting
export function compareSensors(a: string, b: string): number {
  return (FIXED_SLOT[a] ?? SLOTS) - (FIXED_SLOT[b] ?? SLOTS) || a.localeCompare(b)
}

// The value of a theme color, for code that can't use a CSS class (canvas)
export function themeColor(variable: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
}
