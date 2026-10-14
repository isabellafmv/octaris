// G-code → line segments for the 3D preview. Pure TypeScript, no DOM, so it
// runs in a Web Worker (parse.worker.ts) and under vitest.
//
// Understands G0/G1 moves on X, Y and the nozzles' height and plunger axes
// (and E, for raw slicer files), G90/G91, G92 and M82/M83. Everything else
// is ignored.

import type { PrinterProfile } from '../types'

// A nozzle as the preview needs it, from the printer profile. Segments name
// their nozzle by its index in the profile's list (left to right).
export interface PreviewNozzle {
  heightAxis: string
  plungerAxis: string
  // mm from the profile's first (leftmost) nozzle
  offsetX: number
  offsetY: number
}

export function previewNozzles(profile: PrinterProfile): PreviewNozzle[] {
  return profile.nozzles.map((n) => ({
    heightAxis: n.height_axis,
    plungerAxis: n.plunger_axis,
    offsetX: n.offset_x ?? 0,
    offsetY: n.offset_y ?? 0
  }))
}

export const TRAVEL = 0
export const EXTRUDE = 1
export type SegmentKind = typeof TRAVEL | typeof EXTRUDE

export interface ParsedGcode {
  segmentCount: number
  // The profile's nozzle count; nozzles[i] is below it
  nozzleCount: number
  // [x0, y0, z0, x1, y1, z1] per segment
  positions: Float32Array
  kinds: Uint8Array
  nozzles: Uint8Array
  // Index into layerHeights
  layers: Uint32Array
  // Ascending heights of the layers, from the extrusion moves
  layerHeights: number[]
}

// Smaller plunger or position changes are float noise
const EPSILON = 1e-6
// Heights closer than this are the same layer
const LAYER_RESOLUTION = 1e-3

const PROGRESS_EVERY = 20_000

type Position = Record<string, number>

// Growable typed array, so a large file isn't parsed into JS arrays of numbers
class Buffer<T extends Float32Array | Uint8Array | Uint32Array> {
  data: T
  length = 0

  constructor(private readonly make: (size: number) => T) {
    this.data = make(1024)
  }

  push(value: number): void {
    if (this.length === this.data.length) {
      const bigger = this.make(this.data.length * 2)
      bigger.set(this.data)
      this.data = bigger
    }
    this.data[this.length++] = value
  }

  trimmed(): T {
    return this.data.slice(0, this.length) as T
  }
}

const CODE_A = 65
const CODE_Z = 90
const CODE_SEMICOLON = 59

// Letter/number words of one line, ignoring the ; comment. Hand-rolled
// rather than a regex: it runs once per line of files with 500k+ lines.
function parseWords(line: string, letters: string[], values: number[]): number {
  let count = 0
  let i = 0
  const n = line.length
  while (i < n) {
    let c = line.charCodeAt(i)
    if (c === CODE_SEMICOLON) break
    if (c >= 97 && c <= 122) c -= 32 // lower case
    if (c < CODE_A || c > CODE_Z) {
      i++
      continue
    }
    let j = i + 1
    while (j < n && line.charCodeAt(j) === 32) j++
    const start = j
    while (j < n) {
      const d = line.charCodeAt(j)
      // digits, '.', '-', '+'
      if ((d >= 48 && d <= 57) || d === 46 || d === 45 || d === 43) j++
      else break
    }
    const value = start < j ? Number(line.slice(start, j)) : NaN
    letters[count] = String.fromCharCode(c)
    values[count] = value
    count++
    i = j
  }
  return count
}

// `nozzles`: the printer profile's (see previewNozzles).
export function parseGcode(
  text: string,
  nozzles: PreviewNozzle[],
  onProgress?: (fraction: number) => void
): ParsedGcode {
  const positions = new Buffer((n) => new Float32Array(n))
  const kinds = new Buffer((n) => new Uint8Array(n))
  const segmentNozzles = new Buffer((n) => new Uint8Array(n))
  // Height of each segment, turned into a layer index at the end
  const heights = new Buffer((n) => new Float32Array(n))

  const pos: Position = { X: 0, Y: 0, Z: 0, E: 0 }
  for (const n of nozzles) pos[n.heightAxis] = pos[n.plungerAxis] = 0
  // Plungers push in the negative direction (see backend gcode_processor.py);
  // E in raw slicer files extrudes in the positive one, from the first nozzle.
  const plungers = [
    ...nozzles.map((n, i) => ({ axis: n.plungerAxis, nozzle: i, sign: -1 })),
    { axis: 'E', nozzle: 0, sign: 1 }
  ]
  let relative = false // G91
  let relativeE = false // M83, or G91
  // A nozzle's height is its own axis, but a file that never moves that
  // axis (an older file for one nozzle, or a raw slicer file) changes
  // layers on Cura's Z: fall back to it, unless the profile's Z is a plunger.
  const curaZ = nozzles.some((n) => n.plungerAxis === 'Z') ? null : 'Z'
  const movedAxes = new Set<string>(curaZ ? [curaZ] : [])
  // Travel belongs to the nozzle that last extruded
  let active = 0

  const letters: string[] = []
  const values: number[] = []
  const extruding: number[] = []

  const height = (nozzle: number, p: Position): number => {
    const axis = nozzles[nozzle]?.heightAxis
    if (axis !== undefined && (movedAxes.has(axis) || curaZ === null)) return p[axis]
    return p.Z
  }

  const addSegment = (
    from: Position,
    to: Position,
    kind: SegmentKind,
    nozzle: number,
    dx = 0,
    dy = 0
  ): void => {
    const z0 = height(nozzle, from)
    const z1 = height(nozzle, to)
    if (
      Math.abs(to.X - from.X) < EPSILON &&
      Math.abs(to.Y - from.Y) < EPSILON &&
      Math.abs(z1 - z0) < EPSILON
    ) {
      return // a plunger-only move
    }
    positions.push(from.X + dx)
    positions.push(from.Y + dy)
    positions.push(z0)
    positions.push(to.X + dx)
    positions.push(to.Y + dy)
    positions.push(z1)
    kinds.push(kind)
    segmentNozzles.push(nozzle)
    heights.push(z1)
  }

  let lineStart = 0
  let lineNo = 0
  const total = text.length
  while (lineStart <= total) {
    let lineEnd = text.indexOf('\n', lineStart)
    if (lineEnd === -1) lineEnd = total
    const line = text.slice(lineStart, lineEnd)
    lineStart = lineEnd + 1
    if (onProgress && ++lineNo % PROGRESS_EVERY === 0) onProgress(lineStart / total)

    const count = parseWords(line, letters, values)
    if (count === 0) continue
    const code = values[0]

    if (letters[0] === 'M') {
      if (code === 82) relativeE = false
      else if (code === 83) relativeE = true
      continue
    }
    if (letters[0] !== 'G') continue

    if (code === 90) {
      relative = false
      relativeE = false
    } else if (code === 91) {
      relative = true
      relativeE = true
    } else if (code === 92) {
      if (count === 1) {
        for (const axis of Object.keys(pos)) pos[axis] = 0
      }
      for (let k = 1; k < count; k++) {
        const axis = letters[k]
        if (axis in pos && !Number.isNaN(values[k])) pos[axis] = values[k]
      }
    } else if (code === 0 || code === 1) {
      const from = { ...pos }
      for (let k = 1; k < count; k++) {
        const axis = letters[k]
        const value = values[k]
        if (!(axis in pos) || Number.isNaN(value)) continue
        const isRelative = axis === 'E' ? relativeE : relative
        pos[axis] = isRelative ? pos[axis] + value : value
        movedAxes.add(axis)
      }

      extruding.length = 0
      for (const { axis, nozzle, sign } of plungers) {
        if ((pos[axis] - from[axis]) * sign > EPSILON && !extruding.includes(nozzle)) {
          extruding.push(nozzle)
        }
      }
      extruding.sort((a, b) => a - b)
      if (extruding.length > 1) {
        // Several nozzles at once: X/Y are the leftmost one's, and each
        // other prints its copy beside it, as far over as it sits on the
        // head. Travel is drawn as the leftmost nozzle's (the head's) path.
        const [first] = extruding
        active = first
        for (const n of extruding) {
          const dx = nozzles[n].offsetX - nozzles[first].offsetX
          const dy = nozzles[n].offsetY - nozzles[first].offsetY
          addSegment(from, pos, EXTRUDE, n, dx, dy)
        }
      } else if (extruding.length > 0) {
        active = extruding[0]
        addSegment(from, pos, EXTRUDE, active)
      } else {
        addSegment(from, pos, TRAVEL, active)
      }
    }
  }
  onProgress?.(1)

  return assignLayers(
    Math.max(nozzles.length, 1),
    positions.trimmed(),
    kinds.trimmed(),
    segmentNozzles.trimmed(),
    heights.trimmed()
  )
}

// Layers are the distinct heights extrusion happens at. A travel move gets
// the highest layer at or below its height, so the move up to the next
// layer belongs to that next layer, and a final lift to the last one.
function assignLayers(
  nozzleCount: number,
  positions: Float32Array,
  kinds: Uint8Array,
  nozzles: Uint8Array,
  heights: Float32Array
): ParsedGcode {
  const segmentCount = kinds.length
  const key = (h: number): number => Math.round(h / LAYER_RESOLUTION)

  const keys = new Set<number>()
  for (let i = 0; i < segmentCount; i++) {
    if (kinds[i] === EXTRUDE) keys.add(key(heights[i]))
  }
  const sorted = [...keys].sort((a, b) => a - b)
  const layerHeights = sorted.map((k) => k * LAYER_RESOLUTION)

  const layers = new Uint32Array(segmentCount)
  if (sorted.length > 0) {
    const index = new Map(sorted.map((k, i) => [k, i]))
    for (let i = 0; i < segmentCount; i++) {
      const k = key(heights[i])
      layers[i] = index.get(k) ?? highestAtOrBelow(sorted, k)
    }
  }
  return { segmentCount, nozzleCount, positions, kinds, nozzles, layers, layerHeights }
}

function highestAtOrBelow(sorted: number[], k: number): number {
  let lo = 0
  let hi = sorted.length - 1
  if (k < sorted[0]) return 0
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (sorted[mid] <= k) lo = mid
    else hi = mid - 1
  }
  return lo
}
