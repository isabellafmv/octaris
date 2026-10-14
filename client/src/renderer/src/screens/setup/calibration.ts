import type { NozzleCalibration, NozzleSpec } from '../../types'

export const NO_NOZZLES: NozzleCalibration = {}

// Every selected nozzle is zeroed
export function isCalibratedFor(selected: NozzleSpec[], nozzles: NozzleCalibration): boolean {
  return selected.length > 0 && selected.every((n) => nozzles[n.id])
}

export interface CalibrationStep {
  nozzle: string
  instruction: string
  label: string
}

// What to do before each zero. X/Y are set at the first (leftmost) selected
// nozzle over the start point; each nozzle's height with that nozzle lowered
// onto the bed, so each further nozzle only sets its height.
export function calibrationSteps(selected: NozzleSpec[]): CalibrationStep[] {
  const name = (n: NozzleSpec): string => n.name.toLowerCase()
  return selected.map((n, i) => ({
    nozzle: n.id,
    instruction:
      selected.length === 1
        ? `Jog the ${name(n)} nozzle over the start point and lower it onto the bed (${n.height_axis})`
        : i === 0
          ? `Lower the ${name(n)} nozzle onto the bed (${n.height_axis}) over the start point`
          : `Now lower the ${name(n)} nozzle onto the bed (${n.height_axis})`,
    label: `Zero ${name(n)}`
  }))
}
