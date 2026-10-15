import { describe, expect, it } from 'vitest'
import type { PrinterProfile } from '../../types'
import {
  bedDraftFrom,
  canAdd,
  draftsFrom,
  freeLetters,
  isChanged,
  isValid,
  newDraft,
  toRequest,
  validateBed,
  validateDrafts,
  type SyringeDraft
} from './printerSetup'

const PRINTESS_1: PrinterProfile = {
  id: 'printess-1',
  name: 'Printess 1.0',
  nozzles: [
    { id: 'left', name: 'Left', height_axis: 'Z', plunger_axis: 'B', offset_x: 0, offset_y: 0 },
    { id: 'right', name: 'Right', height_axis: 'A', plunger_axis: 'C', offset_x: 30, offset_y: 0 }
  ],
  nozzle_offset_measured: false
}

function four(): SyringeDraft[] {
  const drafts = draftsFrom(PRINTESS_1)
  drafts.push(newDraft(drafts))
  drafts.push(newDraft(drafts))
  return drafts
}

describe('the printer setup editor', () => {
  it('starts from the profile', () => {
    expect(draftsFrom(PRINTESS_1)[1]).toEqual({
      id: 'right',
      name: 'Right',
      heightAxis: 'A',
      plungerAxis: 'C',
      offsetX: '30',
      offsetY: '0'
    })
    expect(isValid(draftsFrom(PRINTESS_1))).toBe(true)
    expect(isChanged(draftsFrom(PRINTESS_1), false, PRINTESS_1, bedDraftFrom(PRINTESS_1))).toBe(
      false
    )
  })

  it('adds syringes with free letters and ids', () => {
    const drafts = four()
    expect(drafts.map((d) => [d.id, d.name, d.heightAxis, d.plungerAxis])).toEqual([
      ['left', 'Left', 'Z', 'B'],
      ['right', 'Right', 'A', 'C'],
      ['syringe-3', 'Syringe 3', 'I', 'J'],
      ['syringe-4', 'Syringe 4', 'K', 'U']
    ])
    expect(isValid(drafts)).toBe(true)
    expect(freeLetters(drafts)).toEqual(['V', 'W'])
    expect(canAdd(drafts)).toBe(true)
    drafts.push(newDraft(drafts))
    // Ten letters: five syringes use them all
    expect(canAdd(drafts)).toBe(false)
  })

  it('marks a letter used twice on both fields', () => {
    const drafts = four()
    drafts[3].heightAxis = 'z'
    const errors = validateDrafts(drafts)
    expect(errors[0].heightAxis).toBe("Z is also Syringe 4's")
    expect(errors[3].heightAxis).toBe("Z is also Left's")
    expect(errors[1]).toEqual({})
    expect(isValid(drafts, errors)).toBe(false)
  })

  it('wants height and plunger to differ', () => {
    const drafts = four()
    drafts[2].plungerAxis = 'I'
    expect(validateDrafts(drafts)[2]).toEqual({
      heightAxis: 'Height and plunger must differ',
      plungerAxis: 'Height and plunger must differ'
    })
  })

  it('refuses X, Y, E and letters that are no axis', () => {
    const drafts = draftsFrom(PRINTESS_1)
    drafts[0].heightAxis = 'x'
    drafts[0].plungerAxis = 'E'
    drafts[1].heightAxis = 'Q'
    drafts[1].plungerAxis = ''
    const errors = validateDrafts(drafts)
    expect(errors[0]).toEqual({
      heightAxis: 'X is the stage',
      plungerAxis: "E is the slicer's extruder"
    })
    expect(errors[1].heightAxis).toMatch(/^Not an axis/)
    expect(errors[1].plungerAxis).toBe('Required')
  })

  it('wants a name and offsets in mm', () => {
    const drafts = draftsFrom(PRINTESS_1)
    drafts[0].name = ' '
    drafts[1].offsetX = '3o'
    drafts[1].offsetY = '-'
    expect(validateDrafts(drafts)).toEqual([
      { name: 'Required' },
      { offsetX: 'Enter a number in mm', offsetY: 'Enter a number in mm' }
    ])
  })

  it('needs one to six syringes', () => {
    expect(isValid([])).toBe(false)
  })

  it('sends what was typed, cleaned up', () => {
    const drafts = four()
    drafts[2].heightAxis = ' u '
    drafts[2].offsetX = '-12.5'
    drafts[3].heightAxis = 'W'
    drafts[3].plungerAxis = 'k'
    expect(toRequest(drafts, true, 'printess-1', bedDraftFrom(PRINTESS_1))).toEqual({
      nozzles: [
        { id: 'left', name: 'Left', height_axis: 'Z', plunger_axis: 'B', offset_x: 0, offset_y: 0 },
        {
          id: 'right',
          name: 'Right',
          height_axis: 'A',
          plunger_axis: 'C',
          offset_x: 30,
          offset_y: 0
        },
        {
          id: 'syringe-3',
          name: 'Syringe 3',
          height_axis: 'U',
          plunger_axis: 'J',
          offset_x: -12.5,
          offset_y: 0
        },
        {
          id: 'syringe-4',
          name: 'Syringe 4',
          height_axis: 'W',
          plunger_axis: 'K',
          offset_x: 0,
          offset_y: 0
        }
      ],
      nozzle_offset_measured: true,
      base: 'printess-1',
      bed: { x: { min: -30, max: 30 }, y: { min: -30, max: 30 }, z: { min: 0, max: 60 } }
    })
    const bed = bedDraftFrom(PRINTESS_1)
    expect(isChanged(drafts, false, PRINTESS_1, bed)).toBe(true)
    expect(isChanged(draftsFrom(PRINTESS_1), true, PRINTESS_1, bed)).toBe(true)
  })
})

describe('the bed limits', () => {
  const DISH = { x: { min: -45, max: 45 }, y: { min: -45, max: 45 }, z: { min: 0, max: 20 } }

  it("starts from the profile's bed, or the backend's default", () => {
    expect(bedDraftFrom({ ...PRINTESS_1, bed: DISH }).x).toEqual({ min: '-45', max: '45' })
    expect(bedDraftFrom(PRINTESS_1)).toEqual({
      x: { min: '-30', max: '30' },
      y: { min: '-30', max: '30' },
      z: { min: '0', max: '60' }
    })
  })

  it('needs numbers, each min below its max', () => {
    const bed = bedDraftFrom(PRINTESS_1)
    expect(validateBed(bed)).toEqual({})
    bed.x = { min: '45', max: '-45' }
    bed.y = { min: '-', max: '10' }
    bed.z = { min: '3', max: '3' }
    expect(validateBed(bed)).toEqual({
      x: 'X min must be below max',
      y: 'Enter a number in mm',
      z: 'Z min must be below max'
    })
  })

  it('sends the bed, and a changed bed counts as a change', () => {
    const profile = { ...PRINTESS_1, bed: DISH }
    const bed = bedDraftFrom(profile)
    expect(isChanged(draftsFrom(profile), false, profile, bed)).toBe(false)
    bed.z = { min: '0', max: '12.5' }
    expect(isChanged(draftsFrom(profile), false, profile, bed)).toBe(true)
    expect(toRequest(draftsFrom(profile), false, 'printess-1', bed).bed).toEqual({
      ...DISH,
      z: { min: 0, max: 12.5 }
    })
  })
})
