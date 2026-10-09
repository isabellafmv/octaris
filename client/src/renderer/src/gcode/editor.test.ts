import { history, undo } from '@codemirror/commands'
import { EditorState, Text } from '@codemirror/state'
import { describe, expect, it } from 'vitest'
import {
  clampLine,
  errorLine,
  hasUnsavedChanges,
  maybeEdited,
  parseLineTarget,
  setErrorLine,
  tokenizeGcodeLine
} from './editor'

// Each token as [text, kind], for readable assertions
function tokens(line: string): [string, string][] {
  return tokenizeGcodeLine(line).map((t) => [line.slice(t.from, t.to), t.kind])
}

describe('tokenizeGcodeLine', () => {
  it('splits a move into command, axis and parameter words', () => {
    expect(tokens('G1 X10.5 Y-3 B-.02 F200')).toEqual([
      ['G1', 'command'],
      ['X10.5', 'axis'],
      ['Y-3', 'axis'],
      ['B-.02', 'axis'],
      ['F200', 'param']
    ])
  })

  it('marks everything from a semicolon as a comment', () => {
    expect(tokens('G1 B-0.2 F400 ; pressurize B')).toEqual([
      ['G1', 'command'],
      ['B-0.2', 'axis'],
      ['F400', 'param'],
      ['; pressurize B', 'comment']
    ])
    expect(tokens('; layer change; G1 X1')).toEqual([['; layer change; G1 X1', 'comment']])
  })

  it('reads lower case, spaced and line-numbered words as the backend does', () => {
    expect(tokens('n12 g1 x 5 e1')).toEqual([
      ['n12', 'param'],
      ['g1', 'command'],
      ['x 5', 'axis'],
      ['e1', 'axis']
    ])
  })

  it('has nothing to mark on a blank line', () => {
    expect(tokens('')).toEqual([])
    expect(tokens('   ')).toEqual([])
  })

  it('treats M and T codes as commands', () => {
    expect(tokens('M83')).toEqual([['M83', 'command']])
    expect(tokens('T1')).toEqual([['T1', 'command']])
    expect(tokens('M104 S37')).toEqual([
      ['M104', 'command'],
      ['S37', 'param']
    ])
  })
})

describe('parseLineTarget', () => {
  it('reads a line number, with or without thousands separators', () => {
    expect(parseLineTarget('42', 500_000)).toBe(42)
    expect(parseLineTarget(' 250,000 ', 500_000)).toBe(250_000)
    expect(parseLineTarget('250 000', 500_000)).toBe(250_000)
  })

  it('clamps to the document', () => {
    expect(parseLineTarget('0', 100)).toBe(1)
    expect(parseLineTarget('999999', 100)).toBe(100)
  })

  it('rejects anything else', () => {
    expect(parseLineTarget('', 100)).toBeNull()
    expect(parseLineTarget('abc', 100)).toBeNull()
    expect(parseLineTarget('-5', 100)).toBeNull()
    expect(parseLineTarget('1e3', 100)).toBeNull()
  })
})

describe('clampLine', () => {
  it('keeps a line within the document', () => {
    const doc = Text.of(['a', 'b', 'c'])
    expect(clampLine(0, doc)).toBe(1)
    expect(clampLine(2, doc)).toBe(2)
    expect(clampLine(9, doc)).toBe(3)
  })
})

describe('unsaved changes', () => {
  const open = (text: string): EditorState =>
    EditorState.create({ doc: text, extensions: [history()] })

  it('are none in a freshly opened document', () => {
    const state = open('G91\nG1 X1')
    expect(maybeEdited(state)).toBe(false)
    expect(hasUnsavedChanges(state, state.doc)).toBe(false)
  })

  it('are seen after an edit', () => {
    const state = open('G91\nG1 X1')
    const edited = state.update({ changes: { from: 4, to: 6, insert: 'G0' } }).state
    expect(maybeEdited(edited)).toBe(true)
    expect(hasUnsavedChanges(edited, state.doc)).toBe(true)
  })

  it('are gone once undone, or typed back as they were', () => {
    const state = open('G91\nG1 X1')
    let edited = state.update({ changes: { from: 4, to: 6, insert: 'G0' } }).state
    const undone = { state: edited }
    undo({ state: edited, dispatch: (tr) => (undone.state = tr.state) })
    expect(hasUnsavedChanges(undone.state, state.doc)).toBe(false)

    edited = edited.update({ changes: { from: 4, to: 6, insert: 'G1' } }).state
    expect(maybeEdited(edited)).toBe(true)
    expect(hasUnsavedChanges(edited, state.doc)).toBe(false)
  })
})

describe('errorLine', () => {
  const marked = (state: EditorState): number[] => {
    const lines: number[] = []
    state.field(errorLine).between(0, state.doc.length, (from) => {
      lines.push(state.doc.lineAt(from).number)
    })
    return lines
  }

  it('marks the rejected line and follows it through edits', () => {
    let state = EditorState.create({ doc: 'G91\nG90\nG1 X1', extensions: [errorLine] })
    state = state.update({ effects: setErrorLine.of(2) }).state
    expect(marked(state)).toEqual([2])

    state = state.update({ changes: { from: 0, insert: '; note\n' } }).state
    expect(marked(state)).toEqual([3])

    state = state.update({ effects: setErrorLine.of(null) }).state
    expect(marked(state)).toEqual([])
  })

  it('handles a line number past the end, and a 500k-line program', () => {
    const lines = Array.from({ length: 500_000 }, (_, i) => `G1 X${i % 2 ? 1 : -1} B-0.001`)
    let state = EditorState.create({ doc: lines.join('\n'), extensions: [errorLine] })
    state = state.update({ effects: setErrorLine.of(499_999) }).state
    expect(marked(state)).toEqual([499_999])
    state = state.update({ effects: setErrorLine.of(600_000) }).state
    expect(marked(state)).toEqual([500_000])
  })
})
