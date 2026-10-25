// The jog panel's choices: which syringes' height axes and plungers its
// up/down columns move, and the step and feed rates typed in it. Pure, for
// the tests.
import type { NozzleSpec } from '../types'

// The custom step, in mm (as session.MAX_JOG_MM in the backend)
export const MIN_STEP = 0.01
export const MAX_STEP = 200
// Jog and Go to Origin feed rates, in mm/min (as session.MIN_JOG_FEED and
// MAX_JOG_FEED); 300 is the speed jogs always had
export const MIN_FEED = 1
export const MAX_FEED = 3000
export const DEFAULT_FEED = 300

// A typed step or feed rate within [min, max]; NaN for anything else
function parseWithin(text: string, min: number, max: number): number {
  const value = /^\s*(\d+\.?\d*|\.\d+)\s*$/.test(text) ? Number(text) : NaN
  return value >= min && value <= max ? value : NaN
}

export const parseStep = (text: string): number => parseWithin(text, MIN_STEP, MAX_STEP)
export const parseFeed = (text: string): number => parseWithin(text, MIN_FEED, MAX_FEED)

// The chosen syringes of one column, left to right: the hand-picked ones
// (`manual`, null until the user taps one of its chips) or else those
// selected for printing. Ids the profile doesn't have are dropped; never
// none (falls back to the print selection, then the first syringe).
export function jogNozzles(
  nozzles: NozzleSpec[],
  printSelection: string[],
  manual: string[] | null
): NozzleSpec[] {
  for (const ids of [manual, printSelection]) {
    const chosen = nozzles.filter((n) => ids?.includes(n.id))
    if (chosen.length > 0) return chosen
  }
  return nozzles.slice(0, 1)
}

// `chosen` (ids) with `id` added or taken away; the last one stays
export function toggleJogNozzle(chosen: string[], id: string): string[] {
  if (!chosen.includes(id)) return [...chosen, id]
  return chosen.length > 1 ? chosen.filter((n) => n !== id) : chosen
}
