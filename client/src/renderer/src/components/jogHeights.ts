// Which syringes' height axes the jog panel's up/down buttons move. Pure,
// for the tests.
import type { NozzleSpec } from '../types'

// The chosen syringes, left to right: the hand-picked ones (`manual`, null
// until the user taps a chip) or else those selected for printing. Ids the
// profile doesn't have are dropped; never none (falls back to the print
// selection, then the first syringe).
export function jogHeightNozzles(
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
export function toggleJogHeight(chosen: string[], id: string): string[] {
  if (!chosen.includes(id)) return [...chosen, id]
  return chosen.length > 1 ? chosen.filter((n) => n !== id) : chosen
}
