import { describe, expect, it } from 'vitest'
import { groupByCategory } from './categories'
import { EXTRUDE, NOZZLE_RIGHT, TRAVEL, parseGcode } from './parse'

// The parsed segments as plain objects, for readable assertions
function segments(text: string): {
  from: number[]
  to: number[]
  kind: 'travel' | 'extrude'
  nozzle: 'left' | 'right'
  layer: number
}[] {
  const p = parseGcode(text)
  return Array.from({ length: p.segmentCount }, (_, i) => ({
    from: Array.from(p.positions.subarray(i * 6, i * 6 + 3)),
    to: Array.from(p.positions.subarray(i * 6 + 3, i * 6 + 6)),
    kind: p.kinds[i] === EXTRUDE ? 'extrude' : 'travel',
    nozzle: p.nozzles[i] === NOZZLE_RIGHT ? 'right' : 'left',
    layer: p.layers[i]
  }))
}

const gcode = (...lines: string[]): string => lines.join('\n')

describe('absolute (G90)', () => {
  it('turns moves into travel and extrusion segments', () => {
    const result = segments(
      gcode(
        'G90',
        'G0 F400 X10 Y10 Z0.3',
        'G1 F200 X20 Y10 B-0.5',
        'G1 F200 X20 Y20 B-1 ; comment X99'
      )
    )
    expect(result).toEqual([
      {
        from: [0, 0, 0],
        to: [10, 10, expect.closeTo(0.3)],
        kind: 'travel',
        nozzle: 'left',
        layer: 0
      },
      {
        from: [10, 10, expect.closeTo(0.3)],
        to: [20, 10, expect.closeTo(0.3)],
        kind: 'extrude',
        nozzle: 'left',
        layer: 0
      },
      {
        from: [20, 10, expect.closeTo(0.3)],
        to: [20, 20, expect.closeTo(0.3)],
        kind: 'extrude',
        nozzle: 'left',
        layer: 0
      }
    ])
  })

  it('does not count a plunger pulling back as extrusion', () => {
    const result = segments(gcode('G1 X0 Y0 B-1', 'G1 X5 Y0 B-0.5', 'G1 X10 Y0 B-0.5'))
    expect(result.map((s) => s.kind)).toEqual(['travel', 'travel'])
  })

  it('skips plunger-only moves', () => {
    expect(segments(gcode('G1 B-0.2 F400', 'G1 C-0.2'))).toEqual([])
  })

  it('assigns the right nozzle to C and travel to the nozzle that last extruded', () => {
    const result = segments(gcode('G1 C-0.2', 'G0 X10', 'G1 X20 C-1', 'G0 X0 Y5', 'G1 X5 B-1'))
    expect(result.map((s) => [s.kind, s.nozzle])).toEqual([
      ['travel', 'right'],
      ['extrude', 'right'],
      ['travel', 'right'],
      ['extrude', 'left']
    ])
  })

  it('draws a move that drives both plungers once per nozzle', () => {
    const result = segments(gcode('G1 X10 B-1 C-1'))
    expect(result.map((s) => s.nozzle)).toEqual(['left', 'right'])
  })

  it('takes the right nozzle height from A once the file moves A', () => {
    const result = segments(gcode('G1 Z5 A0.2', 'G1 X10 C-1'))
    expect(result[1]).toMatchObject({ from: [0, 0, expect.closeTo(0.2)], nozzle: 'right' })
  })

  it('takes the right nozzle height from Z in files that never move A', () => {
    const result = segments(gcode('G0 X41 Z0.3', 'G1 X51 C-1', 'G0 Z0.5', 'G1 X41 C-2'))
    expect(result.filter((s) => s.kind === 'extrude').map((s) => s.to[2])).toEqual([
      expect.closeTo(0.3),
      expect.closeTo(0.5)
    ])
  })

  it('extrudes with E in raw slicer files (M82 absolute)', () => {
    const result = segments(gcode('M82', 'G1 X10 E1', 'G1 X20 E2', 'G1 X30 E1.5'))
    expect(result.map((s) => s.kind)).toEqual(['extrude', 'extrude', 'travel'])
  })

  it('reads E relative after M83 even with G90', () => {
    const result = segments(gcode('G90', 'M83', 'G1 X10 E1', 'G1 X20 E1', 'G1 X30 E-0.5'))
    expect(result.map((s) => s.kind)).toEqual(['extrude', 'extrude', 'travel'])
    expect(result[2].to).toEqual([30, 0, 0]) // X stays absolute
  })

  it('ignores unknown commands and malformed words', () => {
    const result = segments(gcode('M104 S30', 'T1', 'G28', '; G1 X50', 'g1 x5 y5', 'G1 X', ''))
    expect(result).toEqual([
      { from: [0, 0, 0], to: [5, 5, 0], kind: 'travel', nozzle: 'left', layer: 0 }
    ])
  })
})

describe('relative (G91)', () => {
  it('adds each move to the current position', () => {
    const result = segments(
      gcode('G91', 'G0 X10 Y10 Z0.3', 'G1 X10 B-0.5', 'G1 Y10 B-0.5', 'G1 X-10 B0.2')
    )
    expect(result.map((s) => [s.to.map((v) => +v.toFixed(3)), s.kind])).toEqual([
      [[10, 10, 0.3], 'travel'],
      [[20, 10, 0.3], 'extrude'],
      [[20, 20, 0.3], 'extrude'],
      [[10, 20, 0.3], 'travel']
    ])
  })

  it('splits a fully relative file into layers', () => {
    const layer = ['G1 X10 B-1', 'G1 Y10 B-1', 'G1 X-10 B-1', 'G1 Y-10 B-1']
    const p = parseGcode(gcode('G91', 'G0 Z0.2', ...layer, 'G0 Z0.2', ...layer, 'G0 Z5'))
    expect(p.layerHeights).toEqual([expect.closeTo(0.2), expect.closeTo(0.4)])
    // the move up to layer 2 belongs to layer 2, the final lift to the last layer
    expect(Array.from(p.layers)).toEqual([0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1])
    expect(Array.from(p.kinds)).toEqual([
      TRAVEL,
      EXTRUDE,
      EXTRUDE,
      EXTRUDE,
      EXTRUDE,
      TRAVEL,
      EXTRUDE,
      EXTRUDE,
      EXTRUDE,
      EXTRUDE,
      TRAVEL
    ])
  })

  it('extrudes with relative E', () => {
    expect(segments(gcode('G91', 'G1 X1 E0.1', 'G1 X1 E-0.1')).map((s) => s.kind)).toEqual([
      'extrude',
      'travel'
    ])
  })
})

describe('mixed G90/G91', () => {
  // How the backend writes its plunger moves: G91, a relative push, G90
  it('switches modes in the middle of a file', () => {
    const result = segments(
      gcode(
        'G90',
        'G0 X10 Y10 Z0.3',
        'G91',
        'G1 B-0.6 F400 ; prime',
        'G90',
        'G1 X20 Y10 B-1.1',
        'G91',
        'G1 B0.6 ; retract',
        'G1 X5',
        'G90',
        'G1 X0 Y0'
      )
    )
    expect(result.map((s) => [s.to.map((v) => +v.toFixed(3)), s.kind])).toEqual([
      [[10, 10, 0.3], 'travel'],
      [[20, 10, 0.3], 'extrude'], // B -0.6 → -1.1
      [[25, 10, 0.3], 'travel'],
      [[0, 0, 0.3], 'travel']
    ])
  })
})

describe('G92', () => {
  it('sets positions without moving', () => {
    const result = segments(gcode('G1 X10 Y10', 'G92 X0 Y0', 'G1 X5'))
    expect(result.map((s) => [s.from, s.to])).toEqual([
      [
        [0, 0, 0],
        [10, 10, 0]
      ],
      [
        [0, 0, 0],
        [5, 0, 0]
      ]
    ])
  })

  it('resets the plunger after pressurizing, so the next push still extrudes', () => {
    const result = segments(gcode('G1 B-0.2', 'G92 B0', 'G1 X10 B-0.5', 'G92 B5', 'G1 X20 B4'))
    expect(result.map((s) => s.kind)).toEqual(['extrude', 'extrude'])
  })

  it('resets E in raw files', () => {
    const result = segments(gcode('G1 X10 E5', 'G92 E0', 'G1 X20 E1'))
    expect(result.map((s) => s.kind)).toEqual(['extrude', 'extrude'])
  })

  it('zeroes every axis without arguments', () => {
    const result = segments(gcode('G1 X10 Z1', 'G92', 'G1 X1'))
    expect(result[1]).toMatchObject({ from: [0, 0, 0], to: [1, 0, 0] })
  })
})

describe('groupByCategory', () => {
  it('orders each category by layer', () => {
    const p = parseGcode(
      gcode('G0 Z0.2', 'G1 X10 B-1', 'G1 X20 C-1', 'G0 Z0.4', 'G1 X10 B-2', 'G1 X0 C-2')
    )
    const { categories, layerHeights } = groupByCategory(p)
    expect(layerHeights).toHaveLength(2)
    expect(Array.from(categories.left.layerStart)).toEqual([0, 1, 2])
    expect(Array.from(categories.right.layerStart)).toEqual([0, 1, 2])
    expect(Array.from(categories.travel.layerStart)).toEqual([0, 1, 2])
    expect(Array.from(categories.left.positions.subarray(3, 6))).toEqual([
      10,
      0,
      expect.closeTo(0.2)
    ])
  })

  it('keeps file order within a layer when layers are revisited', () => {
    // "both" with tool changes can go back down to an earlier height
    const p = parseGcode(
      gcode('G0 Z0.4', 'G1 X1 B-1', 'G0 Z0.2', 'G1 X2 B-2', 'G0 Z0.4', 'G1 X3 B-3')
    )
    const left = groupByCategory(p).categories.left
    expect(Array.from(left.layerStart)).toEqual([0, 1, 3])
    expect([0, 1, 2].map((i) => left.positions[i * 6 + 3])).toEqual([2, 1, 3])
  })

  it('handles a file without moves', () => {
    const { categories, layerHeights } = groupByCategory(parseGcode('G90\nM83'))
    expect(layerHeights).toEqual([])
    expect(Array.from(categories.travel.layerStart)).toEqual([0, 0])
  })
})

describe('large files', () => {
  it('parses 500k lines quickly and reports progress', () => {
    const lines = ['G91']
    for (let layer = 0; layer < 500; layer++) {
      lines.push('G0 Z0.2')
      for (let i = 0; i < 999; i++)
        lines.push(i % 2 ? `G1 X0.5 Y${i % 4 ? 0.1 : -0.1} B-0.01` : 'G0 X-0.2')
    }
    const progress: number[] = []
    const start = performance.now()
    const p = parseGcode(lines.join('\n'), (f) => progress.push(f))
    const elapsed = performance.now() - start

    expect(p.layerHeights).toHaveLength(500)
    expect(p.segmentCount).toBe(500 * 1000)
    expect(progress.at(-1)).toBe(1)
    expect(progress.length).toBeGreaterThan(10)
    expect(elapsed).toBeLessThan(3000)
  })
})
