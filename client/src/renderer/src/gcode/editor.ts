// CodeMirror pieces for the G-code editor. Highlighting decorates only the
// lines in view, so it costs the same for 50 lines as for 500k: nothing is
// parsed ahead of the viewport.
import {
  RangeSetBuilder,
  StateEffect,
  StateField,
  type EditorState,
  type Text
} from '@codemirror/state'
import { undoDepth } from '@codemirror/commands'
import {
  Decoration,
  EditorView,
  ViewPlugin,
  type DecorationSet,
  type ViewUpdate
} from '@codemirror/view'

export type TokenKind = 'command' | 'axis' | 'param' | 'comment'

export interface Token {
  from: number
  to: number
  kind: TokenKind
}

// The words the printer moves: stage, nozzle heights and plungers (E before
// it is substituted)
const AXIS_LETTERS = new Set(['X', 'Y', 'Z', 'A', 'B', 'C', 'E'])
// A letter and its number, as the backend reads them (parse_words): the
// number may lack a leading zero ("X-.82")
const WORD = /([A-Za-z])\s*([-+]?\d*\.?\d+)/g

// The highlighted spans of one line, in order: the first word is the
// command (G1, M83, T0), after any N line number; later ones axis or
// other parameter words, and
// everything from a ; is a comment.
export function tokenizeGcodeLine(line: string): Token[] {
  const tokens: Token[] = []
  const semicolon = line.indexOf(';')
  const code = semicolon === -1 ? line : line.slice(0, semicolon)
  let first = true
  for (const match of code.matchAll(WORD)) {
    const from = match.index
    const letter = match[1].toUpperCase()
    // A line number (N12 G1 ...) comes before the command
    const isCommand = first && letter !== 'N'
    const kind: TokenKind = isCommand ? 'command' : AXIS_LETTERS.has(letter) ? 'axis' : 'param'
    tokens.push({ from, to: from + match[0].length, kind })
    if (isCommand) first = false
  }
  if (semicolon !== -1) tokens.push({ from: semicolon, to: line.length, kind: 'comment' })
  return tokens
}

const TOKEN_MARKS: Record<TokenKind, Decoration> = {
  command: Decoration.mark({ class: 'cm-gc-command' }),
  axis: Decoration.mark({ class: 'cm-gc-axis' }),
  param: Decoration.mark({ class: 'cm-gc-param' }),
  comment: Decoration.mark({ class: 'cm-gc-comment' })
}

function highlightVisible(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>()
  const doc = view.state.doc
  let lastLine = 0
  for (const { from, to } of view.visibleRanges) {
    for (let pos = from; pos <= to; ) {
      const line = doc.lineAt(pos)
      if (line.number > lastLine) {
        for (const t of tokenizeGcodeLine(line.text)) {
          builder.add(line.from + t.from, line.from + t.to, TOKEN_MARKS[t.kind])
        }
        lastLine = line.number
      }
      pos = line.to + 1
    }
  }
  return builder.finish()
}

export const gcodeHighlight = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet
    constructor(view: EditorView) {
      this.decorations = highlightVisible(view)
    }
    update(update: ViewUpdate): void {
      if (update.docChanged || update.viewportChanged) {
        this.decorations = highlightVisible(update.view)
      }
    }
  },
  { decorations: (plugin) => plugin.decorations }
)

// The line the backend rejected, marked until the next save attempt
export const setErrorLine = StateEffect.define<number | null>()
const errorLineMark = Decoration.line({ class: 'cm-gc-error-line' })

export const errorLine = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(marks, tr) {
    marks = marks.map(tr.changes)
    for (const effect of tr.effects) {
      if (!effect.is(setErrorLine)) continue
      const n = effect.value
      marks =
        n === null
          ? Decoration.none
          : Decoration.set([
              errorLineMark.range(tr.state.doc.line(clampLine(n, tr.state.doc)).from)
            ])
    }
    return marks
  },
  provide: (field) => EditorView.decorations.from(field)
})

export function clampLine(line: number, doc: Text): number {
  return Math.min(Math.max(1, Math.round(line)), doc.lines)
}

// What the "Go to line" box asks for: a line number (thousands separators
// allowed), clamped to the document, or null if it isn't one.
export function parseLineTarget(input: string, lineCount: number): number | null {
  const digits = input.trim().replace(/[\s,.'_]/g, '')
  if (!/^\d+$/.test(digits)) return null
  return Math.min(Math.max(1, Number(digits)), lineCount)
}

// Put the cursor at the start of `line` and scroll it to the middle
export function goToLine(view: EditorView, line: number): void {
  const { from } = view.state.doc.line(clampLine(line, view.state.doc))
  view.dispatch({
    selection: { anchor: from },
    effects: EditorView.scrollIntoView(from, { y: 'center' })
  })
  view.focus()
}

// Cheap enough to run on every change: no edit in the undo history means
// the document is the one that was opened
export function maybeEdited(state: EditorState): boolean {
  return undoDepth(state) > 0
}

// Exact, for the cancel prompt: edits undone or typed back aren't changes
export function hasUnsavedChanges(state: EditorState, original: Text): boolean {
  return maybeEdited(state) && !state.doc.eq(original)
}

export const gcodeTheme = EditorView.theme({
  '&': {
    height: '100%',
    fontSize: '12px',
    backgroundColor: 'var(--color-surface)',
    color: 'var(--color-text)'
  },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': {
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
    lineHeight: '1.6'
  },
  '.cm-gutters': {
    backgroundColor: 'var(--color-surface-card)',
    color: 'var(--color-text-muted)',
    borderRight: '1px solid var(--color-border)'
  },
  '.cm-activeLine': { backgroundColor: 'rgba(26, 139, 141, 0.06)' },
  '.cm-activeLineGutter': {
    backgroundColor: 'var(--color-primary-muted)',
    color: 'var(--color-text)'
  },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': {
    backgroundColor: 'var(--color-primary-muted) !important'
  },
  '.cm-cursor': { borderLeftColor: 'var(--color-primary)' },
  '.cm-gc-command': { color: 'var(--color-primary-strong)', fontWeight: '600' },
  '.cm-gc-axis': { color: 'var(--color-chart-3)' },
  '.cm-gc-param': { color: 'var(--color-warning)' },
  '.cm-gc-comment': { color: 'var(--color-text-muted)', fontStyle: 'italic' },
  '.cm-gc-error-line': { backgroundColor: 'var(--color-danger-muted)' },
  '.cm-searchMatch': { backgroundColor: 'rgba(201, 133, 0, 0.25)' },
  '.cm-searchMatch-selected': { backgroundColor: 'rgba(201, 133, 0, 0.5)' },
  '.cm-panels': {
    backgroundColor: 'var(--color-surface-card)',
    color: 'var(--color-text)',
    borderColor: 'var(--color-border)'
  }
})
