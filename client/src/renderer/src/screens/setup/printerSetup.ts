// The printer setup editor on the Setup screen: each syringe's name, axes
// and offset, checked as the backend checks them (backend/printer.py), so
// nothing invalid is ever sent, and the bed limits. Pure, for the tests.
import type { CustomProfileRequest, PrinterProfile } from '../../types'

// The letters a syringe's height motor or plunger can have: Marlin's axis
// names except X/Y (the stage) and E (the slicer's extruder). As
// printer.NOZZLE_AXIS_LETTERS in the backend.
export const AXIS_LETTERS = ['Z', 'A', 'B', 'C', 'I', 'J', 'K', 'U', 'V', 'W']
// As printer.MAX_NOZZLES
export const MAX_SYRINGES = 6
// The profile edited in the app (printer.CUSTOM_PROFILE_ID)
export const CUSTOM_PROFILE_ID = 'custom'

// One syringe as typed: offsets stay text until saved, so "-" or "1." can
// be typed on the way to a number
export interface SyringeDraft {
  id: string
  name: string
  heightAxis: string
  plungerAxis: string
  offsetX: string
  offsetY: string
}

export type DraftField = 'name' | 'heightAxis' | 'plungerAxis' | 'offsetX' | 'offsetY'
export type DraftErrors = Partial<Record<DraftField, string>>

export function draftsFrom(profile: PrinterProfile): SyringeDraft[] {
  return profile.nozzles.map((n) => ({
    id: n.id,
    name: n.name,
    heightAxis: n.height_axis,
    plungerAxis: n.plunger_axis,
    offsetX: String(n.offset_x ?? 0),
    offsetY: String(n.offset_y ?? 0)
  }))
}

export function normalizeAxis(text: string): string {
  return text.trim().toUpperCase()
}

function axisError(letter: string): string | undefined {
  if (letter === '') return 'Required'
  if (letter === 'X' || letter === 'Y') return `${letter} is the stage`
  if (letter === 'E') return "E is the slicer's extruder"
  if (!AXIS_LETTERS.includes(letter)) return `Not an axis: use ${AXIS_LETTERS.join(' ')}`
  return undefined
}

// A number, as the backend reads it; NaN for anything else
export function parseOffset(text: string): number {
  return /^\s*[-+]?(\d+\.?\d*|\.\d+)\s*$/.test(text) ? Number(text) : NaN
}

// Per syringe, what is wrong with each field (empty when it's fine)
export function validateDrafts(drafts: SyringeDraft[]): DraftErrors[] {
  const errors: DraftErrors[] = drafts.map(() => ({}))
  // Where each valid letter is used: [syringe index, field]
  const uses = new Map<string, [number, DraftField][]>()
  drafts.forEach((d, i) => {
    if (d.name.trim() === '') errors[i].name = 'Required'
    for (const field of ['heightAxis', 'plungerAxis'] as const) {
      const letter = normalizeAxis(d[field])
      const problem = axisError(letter)
      if (problem) errors[i][field] = problem
      else uses.set(letter, [...(uses.get(letter) ?? []), [i, field]])
    }
    for (const field of ['offsetX', 'offsetY'] as const) {
      if (Number.isNaN(parseOffset(d[field]))) errors[i][field] = 'Enter a number in mm'
    }
  })
  for (const [letter, places] of uses) {
    if (places.length < 2) continue
    for (const [i, field] of places) {
      const others = places.filter(([j, f]) => j !== i || f !== field)
      const sameSyringe = others.some(([j]) => j === i)
      errors[i][field] ??= sameSyringe
        ? 'Height and plunger must differ'
        : `${letter} is also ${others.map(([j]) => drafts[j].name.trim() || `syringe ${j + 1}`).join(', ')}'s`
    }
  }
  return errors
}

export function isValid(drafts: SyringeDraft[], errors = validateDrafts(drafts)): boolean {
  return (
    drafts.length >= 1 &&
    drafts.length <= MAX_SYRINGES &&
    errors.every((e) => Object.keys(e).length === 0)
  )
}

// The letters no syringe uses yet, in AXIS_LETTERS order
export function freeLetters(drafts: SyringeDraft[]): string[] {
  const used = new Set(
    drafts.flatMap((d) => [normalizeAxis(d.heightAxis), normalizeAxis(d.plungerAxis)])
  )
  return AXIS_LETTERS.filter((letter) => !used.has(letter))
}

// Another syringe can be added: under the limit, and two letters left for it
export function canAdd(drafts: SyringeDraft[]): boolean {
  return drafts.length < MAX_SYRINGES && freeLetters(drafts).length >= 2
}

// A new syringe on the right, with the first two free letters
export function newDraft(drafts: SyringeDraft[]): SyringeDraft {
  const ids = new Set(drafts.map((d) => d.id))
  let n = drafts.length + 1
  while (ids.has(`syringe-${n}`)) n++
  const [heightAxis = '', plungerAxis = ''] = freeLetters(drafts)
  return {
    id: `syringe-${n}`,
    name: `Syringe ${n}`,
    heightAxis,
    plungerAxis,
    offsetX: '0',
    offsetY: '0'
  }
}

// --- bed limits ---------------------------------------------------------------

export const BED_AXES = ['x', 'y', 'z'] as const
export type BedAxis = (typeof BED_AXES)[number]
// Where the nozzles may go, in mm relative to the zero point, as typed
export type BedDraft = Record<BedAxis, { min: string; max: string }>
// Per axis, what is wrong with its min/max (missing when they're fine)
export type BedErrors = Partial<Record<BedAxis, string>>

// The backend's default (printer.BedLimits), for a profile without a bed
const DEFAULT_BED = { x: { min: -30, max: 30 }, y: { min: -30, max: 30 }, z: { min: 0, max: 60 } }

export function bedDraftFrom(profile: PrinterProfile): BedDraft {
  const bed = { ...DEFAULT_BED, ...profile.bed }
  const draft = {} as BedDraft
  for (const axis of BED_AXES) {
    draft[axis] = { min: String(bed[axis].min), max: String(bed[axis].max) }
  }
  return draft
}

// As the backend checks them (printer.AxisRange): numbers, min below max
export function validateBed(bed: BedDraft): BedErrors {
  const errors: BedErrors = {}
  for (const axis of BED_AXES) {
    const min = parseOffset(bed[axis].min)
    const max = parseOffset(bed[axis].max)
    if (Number.isNaN(min) || Number.isNaN(max)) errors[axis] = 'Enter a number in mm'
    else if (min >= max) errors[axis] = `${axis.toUpperCase()} min must be below max`
  }
  return errors
}

// --- the request --------------------------------------------------------------

// The body of PUT /printer/custom. `base`: the profile being edited, which
// the Custom profile copies its build volume from.
export function toRequest(
  drafts: SyringeDraft[],
  offsetsMeasured: boolean,
  base: string,
  bed: BedDraft
): CustomProfileRequest {
  const range = (axis: BedAxis): { min: number; max: number } => ({
    min: parseOffset(bed[axis].min),
    max: parseOffset(bed[axis].max)
  })
  return {
    nozzles: drafts.map((d) => ({
      id: d.id,
      name: d.name.trim(),
      height_axis: normalizeAxis(d.heightAxis),
      plunger_axis: normalizeAxis(d.plungerAxis),
      offset_x: parseOffset(d.offsetX),
      offset_y: parseOffset(d.offsetY)
    })),
    nozzle_offset_measured: offsetsMeasured,
    base,
    bed: { x: range('x'), y: range('y'), z: range('z') }
  }
}

// The drafts, the offsets-measured box or the bed differ from `profile`
export function isChanged(
  drafts: SyringeDraft[],
  offsetsMeasured: boolean,
  profile: PrinterProfile,
  bed: BedDraft
): boolean {
  if (offsetsMeasured !== (profile.nozzle_offset_measured ?? false)) return true
  const saved = toRequest(draftsFrom(profile), offsetsMeasured, profile.id, bedDraftFrom(profile))
  const edited = toRequest(drafts, offsetsMeasured, profile.id, bed)
  return JSON.stringify(saved) !== JSON.stringify(edited)
}
