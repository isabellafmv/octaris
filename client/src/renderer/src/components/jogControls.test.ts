import { describe, expect, it } from 'vitest'
import type { NozzleSpec } from '../types'
import { MAX_STEP, jogNozzles, parseFeed, parseStep, toggleJogNozzle } from './jogControls'

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
    expect(ids(jogNozzles(PRINTESS, ['left'], null))).toEqual(['left'])
    expect(ids(jogNozzles(PRINTESS, ['right', 'left'], null))).toEqual(['left', 'right'])
    expect(ids(jogNozzles(FOUR, ['s4', 's2'], null))).toEqual(['s2', 's4'])
  })

  it('keeps a choice made by hand, whatever the print selection', () => {
    let chosen = ids(jogNozzles(FOUR, ['s1'], null))
    chosen = toggleJogNozzle(chosen, 's3')
    chosen = toggleJogNozzle(chosen, 's4')
    expect(ids(jogNozzles(FOUR, ['s2'], chosen))).toEqual(['s1', 's3', 's4'])
    chosen = toggleJogNozzle(chosen, 's1')
    expect(ids(jogNozzles(FOUR, ['s1', 's2'], chosen))).toEqual(['s3', 's4'])
  })

  it('always moves at least one', () => {
    expect(toggleJogNozzle(['left'], 'left')).toEqual(['left'])
    expect(toggleJogNozzle(['left', 'right'], 'left')).toEqual(['right'])
    // A choice or selection left over from another profile
    expect(ids(jogNozzles(FOUR, ['s2'], ['left']))).toEqual(['s2'])
    expect(ids(jogNozzles(FOUR, ['right'], ['left']))).toEqual(['s1'])
    expect(ids(jogNozzles([nozzle('only', 'Z')], ['only'], null))).toEqual(['only'])
  })
})

// Heights Z A B C, plungers U V W D
const PRINTESS_2: NozzleSpec[] = ['ZU', 'AV', 'BW', 'CD'].map(([height, plunger], i) => ({
  id: `s${i + 1}`,
  name: `Syringe ${i + 1}`,
  height_axis: height,
  plunger_axis: plunger
}))
const plungers = (ns: NozzleSpec[]): string[] => ns.map((n) => n.plunger_axis)

describe('the jog plunger chooser', () => {
  it('defaults to the plungers of the syringes selected for printing', () => {
    expect(plungers(jogNozzles(PRINTESS_2, ['s4'], null))).toEqual(['D'])
    expect(plungers(jogNozzles(PRINTESS_2, ['s3', 's1'], null))).toEqual(['U', 'W'])
  })

  it('is chosen apart from the heights, and kept once picked by hand', () => {
    const selection = ['s1', 's2']
    const heights = toggleJogNozzle(ids(jogNozzles(PRINTESS_2, selection, null)), 's1')
    const picked = toggleJogNozzle(ids(jogNozzles(PRINTESS_2, selection, null)), 's4')
    expect(ids(jogNozzles(PRINTESS_2, selection, heights))).toEqual(['s2'])
    expect(plungers(jogNozzles(PRINTESS_2, ['s3'], picked))).toEqual(['U', 'V', 'D'])
  })
})

describe('the jog step and feed rates', () => {
  it('takes a step of 0.01 to 200 mm', () => {
    expect(MAX_STEP).toBe(200)
    expect(parseStep('200')).toBe(200)
    expect(parseStep('0.01')).toBe(0.01)
    expect(parseStep('150.5')).toBe(150.5)
    for (const bad of ['201', '200.01', '0.001', '0', '-5', '', 'abc', '1e2']) {
      expect(parseStep(bad)).toBeNaN()
    }
  })

  it('takes feed rates of 1 to 3000 mm/min', () => {
    expect(parseFeed('300')).toBe(300)
    expect(parseFeed('1')).toBe(1)
    expect(parseFeed('3000')).toBe(3000)
    for (const bad of ['0', '0.5', '3001', '-300', '', 'fast']) {
      expect(parseFeed(bad)).toBeNaN()
    }
  })
})
