import { EXTRUDE, NOZZLE_RIGHT, type ParsedGcode } from './parse'

export type Category = 'left' | 'right' | 'travel'
export const CATEGORIES: Category[] = ['left', 'right', 'travel']

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
  categories: Record<Category, CategorySegments>
}

function categoryOf(kind: number, nozzle: number): Category {
  if (kind !== EXTRUDE) return 'travel'
  return nozzle === NOZZLE_RIGHT ? 'right' : 'left'
}

// Counting sort by layer, keeping file order within a layer
export function groupByCategory(parsed: ParsedGcode): GcodePreviewData {
  const layerCount = Math.max(parsed.layerHeights.length, 1)
  const counts = {} as Record<Category, Uint32Array>
  for (const c of CATEGORIES) counts[c] = new Uint32Array(layerCount + 1)

  for (let i = 0; i < parsed.segmentCount; i++) {
    counts[categoryOf(parsed.kinds[i], parsed.nozzles[i])][parsed.layers[i] + 1]++
  }

  const categories = {} as Record<Category, CategorySegments>
  const next = {} as Record<Category, Uint32Array>
  for (const c of CATEGORIES) {
    const layerStart = counts[c]
    for (let l = 1; l <= layerCount; l++) layerStart[l] += layerStart[l - 1]
    categories[c] = { positions: new Float32Array(layerStart[layerCount] * 6), layerStart }
    next[c] = layerStart.slice(0, layerCount)
  }

  for (let i = 0; i < parsed.segmentCount; i++) {
    const c = categoryOf(parsed.kinds[i], parsed.nozzles[i])
    const slot = next[c][parsed.layers[i]]++
    categories[c].positions.set(parsed.positions.subarray(i * 6, i * 6 + 6), slot * 6)
  }

  return { layerHeights: parsed.layerHeights, categories }
}
