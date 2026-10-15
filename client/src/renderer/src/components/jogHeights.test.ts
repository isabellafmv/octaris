import { describe, expect, it } from 'vitest'
import type { NozzleSpec } from '../types'
import { jogHeightNozzles, toggleJogHeight } from './jogHeights'

const nozzle = (id: string, height: string): NozzleSpec => ({
  id,
  name: id,
  height_axis: height,
  plunger_axis: height === 'Z' ? 'B' : 'C'
})
const PRINTESS = [nozzle('left', 'Z'), nozzle('right', 'A')]
const FOUR = [nozzle('s1', 'Z'), nozzle('s2', 'A'), nozzle('s3', 'U'), nozzle('s4', 'W')]
const ids = (ns: NozzleSpec[]): string[] => ns.map((n) => n.id)

describe('the jog height chooser', () => {
  it('defaults to the syringes selected for printing, and follows them', () => {
    expect(ids(jogHeightNozzles(PRINTESS, ['left'], null))).toEqual(['left'])
    expect(ids(jogHeightNozzles(PRINTESS, ['right', 'left'], null))).toEqual(['left', 'right'])
    expect(ids(jogHeightNozzles(FOUR, ['s4', 's2'], null))).toEqual(['s2', 's4'])
  })

  it('keeps a choice made by hand, whatever the print selection', () => {
    let chosen = ids(jogHeightNozzles(FOUR, ['s1'], null))
    chosen = toggleJogHeight(chosen, 's3')
    chosen = toggleJogHeight(chosen, 's4')
    expect(ids(jogHeightNozzles(FOUR, ['s2'], chosen))).toEqual(['s1', 's3', 's4'])
    chosen = toggleJogHeight(chosen, 's1')
    expect(ids(jogHeightNozzles(FOUR, ['s1', 's2'], chosen))).toEqual(['s3', 's4'])
  })

  it('always moves at least one', () => {
    expect(toggleJogHeight(['left'], 'left')).toEqual(['left'])
    expect(toggleJogHeight(['left', 'right'], 'left')).toEqual(['right'])
    // A choice or selection left over from another profile
    expect(ids(jogHeightNozzles(FOUR, ['s2'], ['left']))).toEqual(['s2'])
    expect(ids(jogHeightNozzles(FOUR, ['right'], ['left']))).toEqual(['s1'])
    expect(ids(jogHeightNozzles([nozzle('only', 'Z')], ['only'], null))).toEqual(['only'])
  })
})
