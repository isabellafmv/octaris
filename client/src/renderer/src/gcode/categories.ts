import { EXTRUDE, type ParsedGcode } from './parse'

// The segments of one category, ordered by layer, so that the viewer can
// show a range of layers by drawing a range of the buffer.
export interface CategorySegments {
  // [x0, y0, z0, x1, y1, z1] per segment
  positions: Float32Array
  // Segments of layer i are layerStart[i] .. layerStart[i + 1] - 1
  layerStart: Uint32Array
}

export interface GcodePreviewData {
  layerHeights: number[]
  // Each nozzle's extrusion, by its index in the printer profile
  nozzles: CategorySegments[]
  travel: CategorySegments
}

// Category i < nozzleCount is nozzle i's extrusion; nozzleCount is travel
function categoryOf(kind: number, nozzle: number, nozzleCount: number): number {
  return kind === EXTRUDE ? nozzle : nozzleCount
}

// Counting sort by layer, keeping file order within a layer
export function groupByCategory(parsed: ParsedGcode): GcodePreviewData {
  const layerCount = Math.max(parsed.layerHeights.length, 1)
  const categoryCount = parsed.nozzleCount + 1
  const counts = Array.from({ length: categoryCount }, () => new Uint32Array(layerCount + 1))
  const category = (i: number): number =>
    categoryOf(parsed.kinds[i], parsed.nozzles[i], parsed.nozzleCount)

  for (let i = 0; i < parsed.segmentCount; i++) {
    counts[category(i)][parsed.layers[i] + 1]++
  }

  const categories: CategorySegments[] = []
  const next: Uint32Array[] = []
  for (const layerStart of counts) {
    for (let l = 1; l <= layerCount; l++) layerStart[l] += layerStart[l - 1]
    categories.push({ positions: new Float32Array(layerStart[layerCount] * 6), layerStart })
    next.push(layerStart.slice(0, layerCount))
  }

  for (let i = 0; i < parsed.segmentCount; i++) {
    const c = category(i)
    const slot = next[c][parsed.layers[i]]++
    categories[c].positions.set(parsed.positions.subarray(i * 6, i * 6 + 6), slot * 6)
  }

  return {
    layerHeights: parsed.layerHeights,
    nozzles: categories.slice(0, parsed.nozzleCount),
    travel: categories[parsed.nozzleCount]
  }
}

// Every buffer of the preview data, to transfer from the worker
export function buffers(data: GcodePreviewData): ArrayBuffer[] {
  return [...data.nozzles, data.travel].flatMap((c) => [
    c.positions.buffer as ArrayBuffer,
    c.layerStart.buffer as ArrayBuffer
  ])
}
