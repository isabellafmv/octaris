// G-code → line segments for the 3D preview. Pure TypeScript, no DOM, so it
// runs in a Web Worker (parse.worker.ts) and under vitest.
//
// Understands G0/G1 moves on X Y Z A B C (and E, for raw slicer files),
// G90/G91, G92 and M82/M83. Everything else is ignored.

export const NOZZLE_LEFT = 0
export const NOZZLE_RIGHT = 1
export type Nozzle = typeof NOZZLE_LEFT | typeof NOZZLE_RIGHT

export const TRAVEL = 0
export const EXTRUDE = 1
export type SegmentKind = typeof TRAVEL | typeof EXTRUDE

export interface ParsedGcode {
  segmentCount: number
  // [x0, y0, z0, x1, y1, z1] per segment
  positions: Float32Array
  kinds: Uint8Array
  nozzles: Uint8Array
  // Index into layerHeights
  layers: Uint32Array
  // Ascending heights of the layers, from the extrusion moves
  layerHeights: number[]
}

// Plungers push in the negative direction (see backend gcode_processor.py);
// E in raw slicer files extrudes in the positive one.
const PLUNGERS = [
  { axis: 'B', nozzle: NOZZLE_LEFT, sign: -1 },
  { axis: 'C', nozzle: NOZZLE_RIGHT, sign: -1 },
  { axis: 'E', nozzle: NOZZLE_LEFT, sign: 1 }
] as const

// Smaller plunger or position changes are float noise
const EPSILON = 1e-6
// Heights closer than this are the same layer
const LAYER_RESOLUTION = 1e-3

const PROGRESS_EVERY = 20_000

type Axis = 'X' | 'Y' | 'Z' | 'A' | 'B' | 'C' | 'E'
type Position = Record<Axis, number>

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

export function parseGcode(text: string, onProgress?: (fraction: number) => void): ParsedGcode {
  const positions = new Buffer((n) => new Float32Array(n))
  const kinds = new Buffer((n) => new Uint8Array(n))
  const nozzles = new Buffer((n) => new Uint8Array(n))
  // Height of each segment, turned into a layer index at the end
  const heights = new Buffer((n) => new Float32Array(n))

  const pos: Position = { X: 0, Y: 0, Z: 0, A: 0, B: 0, C: 0, E: 0 }
  let relative = false // G91
  let relativeE = false // M83, or G91
  // The right nozzle's height is A, but files that never move A (the
  // backend's "right" mode today) change layers on Z, so fall back to it.
  let usesA = false
  // Travel belongs to the nozzle that last extruded
  let active: Nozzle = NOZZLE_LEFT

  const letters: string[] = []
  const values: number[] = []
  const extruding: Nozzle[] = []

  const height = (nozzle: Nozzle, p: Position): number =>
    nozzle === NOZZLE_RIGHT && usesA ? p.A : p.Z

  const addSegment = (from: Position, to: Position, kind: SegmentKind, nozzle: Nozzle): void => {
    const z0 = height(nozzle, from)
    const z1 = height(nozzle, to)
    if (
      Math.abs(to.X - from.X) < EPSILON &&
      Math.abs(to.Y - from.Y) < EPSILON &&
      Math.abs(z1 - z0) < EPSILON
    ) {
      return // a plunger-only move
    }
    positions.push(from.X)
    positions.push(from.Y)
    positions.push(z0)
    positions.push(to.X)
    positions.push(to.Y)
    positions.push(z1)
    kinds.push(kind)
    nozzles.push(nozzle)
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
        for (const axis of Object.keys(pos) as Axis[]) pos[axis] = 0
      }
      for (let k = 1; k < count; k++) {
        const axis = letters[k] as Axis
        if (axis in pos && !Number.isNaN(values[k])) pos[axis] = values[k]
      }
    } else if (code === 0 || code === 1) {
      const from = { ...pos }
      for (let k = 1; k < count; k++) {
        const axis = letters[k] as Axis
        const value = values[k]
        if (!(axis in pos) || Number.isNaN(value)) continue
        const isRelative = axis === 'E' ? relativeE : relative
        pos[axis] = isRelative ? pos[axis] + value : value
        if (axis === 'A') usesA = true
      }

      extruding.length = 0
      for (const { axis, nozzle, sign } of PLUNGERS) {
        if ((pos[axis] - from[axis]) * sign > EPSILON && !extruding.includes(nozzle)) {
          extruding.push(nozzle)
        }
      }
      if (extruding.length > 0) {
        active = extruding[extruding.length - 1]
        for (const nozzle of extruding) addSegment(from, pos, EXTRUDE, nozzle)
      } else {
        addSegment(from, pos, TRAVEL, active)
      }
    }
  }
  onProgress?.(1)

  return assignLayers(positions.trimmed(), kinds.trimmed(), nozzles.trimmed(), heights.trimmed())
}

// Layers are the distinct heights extrusion happens at. A travel move gets
// the highest layer at or below its height, so the move up to the next
// layer belongs to that next layer, and a final lift to the last one.
function assignLayers(
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
  return { segmentCount, positions, kinds, nozzles, layers, layerHeights }
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
