import type { NozzleCalibration, SyringeMode } from '../../types'

export type Nozzle = 'left' | 'right'

// The nozzles each syringe mode prints with; each must be zeroed. In both
// mode the left nozzle comes first: it sets the start point (X/Y).
export const MODE_NOZZLES: Record<SyringeMode, Nozzle[]> = {
  left: ['left'],
  right: ['right'],
  both: ['left', 'right']
}

export const NO_NOZZLES: NozzleCalibration = { left: false, right: false }

export function isCalibratedFor(mode: SyringeMode, nozzles: NozzleCalibration): boolean {
  return MODE_NOZZLES[mode].every((nozzle) => nozzles[nozzle])
}
