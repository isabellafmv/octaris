import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { openSearchPanel, search, searchKeymap } from '@codemirror/search'
import { EditorState, type Text } from '@codemirror/state'
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers
} from '@codemirror/view'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { GcodeSaveError, api } from '../api'
import {
  errorLine,
  gcodeHighlight,
  gcodeTheme,
  goToLine,
  hasUnsavedChanges,
  maybeEdited,
  parseLineTarget,
  setErrorLine
} from '../gcode/editor'
import type { GcodeEditResult } from '../types'

interface GcodeEditorProps {
  filename: string
  onSaved: (result: GcodeEditResult) => void
  onClose: () => void
}

type Load = { kind: 'loading' } | { kind: 'ready' } | { kind: 'error'; message: string }
type SaveError = { message: string; line: number | null }

// Full-screen editor for the loaded print's processed G-code. Saving sends
// it back to the backend, which re-checks it; a rejected line is marked and
// scrolled to.
export function GcodeEditor({ filename, onSaved, onClose }: GcodeEditorProps): React.JSX.Element {
  const mountRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const originalRef = useRef<Text | null>(null)
  const etagRef = useRef<string | null>(null)
  const goToRef = useRef<HTMLInputElement>(null)

  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [lineCount, setLineCount] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<SaveError | null>(null)
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const [goTo, setGoTo] = useState('')

  // The keymap is built once; these always call the current handlers
  const saveRef = useRef<() => void>(() => {})
  const cancelRef = useRef<() => void>(() => {})

  useEffect(() => {
    const abort = new AbortController()
    api
      .getLoadedGcodeForEdit(abort.signal)
      .then(({ text, etag }) => {
        if (abort.signal.aborted) return
        etagRef.current = etag
        const state = EditorState.create({
          doc: text,
          extensions: [
            lineNumbers(),
            highlightActiveLineGutter(),
            highlightActiveLine(),
            drawSelection(),
            history(),
            search({ top: true }),
            keymap.of([
              { key: 'Mod-s', run: () => (saveRef.current(), true), preventDefault: true },
              {
                key: 'Mod-g',
                run: () => (goToRef.current?.focus(), goToRef.current?.select(), true),
                preventDefault: true
              },
              ...searchKeymap,
              // After the search keymap, so Escape closes an open search panel first
              { key: 'Escape', run: () => (cancelRef.current(), true) },
              ...historyKeymap,
              ...defaultKeymap
            ]),
            gcodeHighlight,
            errorLine,
            gcodeTheme,
            EditorView.updateListener.of((update) => {
              if (!update.docChanged) return
              setDirty(maybeEdited(update.state))
              setLineCount(update.state.doc.lines)
            })
          ]
        })
        originalRef.current = state.doc
        viewRef.current = new EditorView({ state, parent: mountRef.current! })
        viewRef.current.focus()
        setLineCount(state.doc.lines)
        setLoad({ kind: 'ready' })
      })
      .catch((e: unknown) => {
        if (abort.signal.aborted) return
        setLoad({ kind: 'error', message: e instanceof Error ? e.message : String(e) })
      })
    return () => {
      abort.abort()
      viewRef.current?.destroy()
      viewRef.current = null
    }
  }, [])

  const save = async (): Promise<void> => {
    const view = viewRef.current
    if (!view || saving) return
    setSaving(true)
    setSaveError(null)
    view.dispatch({ effects: setErrorLine.of(null) })
    try {
      onSaved(await api.saveLoadedGcode(view.state.doc.toString(), etagRef.current))
    } catch (e) {
      const errors = e instanceof GcodeSaveError ? e.errors : []
      const line = errors.find((err) => err.line != null)?.line ?? null
      setSaveError({ message: e instanceof Error ? e.message : String(e), line })
      if (line != null) {
        view.dispatch({ effects: setErrorLine.of(line) })
        goToLine(view, line)
      }
    } finally {
      setSaving(false)
    }
  }

  const cancel = (): void => {
    const view = viewRef.current
    if (saving) return
    if (view && originalRef.current && hasUnsavedChanges(view.state, originalRef.current)) {
      setConfirmDiscard(true)
    } else {
      onClose()
    }
  }

  useEffect(() => {
    saveRef.current = () => void save()
    cancelRef.current = cancel
  })

  const submitGoTo = (e: React.FormEvent): void => {
    e.preventDefault()
    const view = viewRef.current
    const line = view && parseLineTarget(goTo, view.state.doc.lines)
    if (view && line != null) goToLine(view, line)
  }

  const ready = load.kind === 'ready'

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Edit G-code: ${filename}`}
      className="fixed inset-0 z-50 flex flex-col bg-surface text-text"
    >
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-b-border bg-surface-card">
        <div className="flex flex-col min-w-0 mr-auto">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-muted">
            Edit G-code
          </span>
          <span className="text-sm font-medium truncate">
            {filename}
            {dirty && (
              <span className="ml-2 text-xs font-normal text-warning">· unsaved changes</span>
            )}
          </span>
        </div>

        {ready && (
          <span className="text-xs text-text-muted whitespace-nowrap">
            {lineCount.toLocaleString()} lines
          </span>
        )}

        <button
          onClick={() => viewRef.current && openSearchPanel(viewRef.current)}
          disabled={!ready}
          title="Search (⌘F / Ctrl+F)"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-sunken text-text-secondary disabled:opacity-40"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="w-3.5 h-3.5"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
            />
          </svg>
          Search
        </button>

        <form onSubmit={submitGoTo} className="flex items-center gap-1.5">
          <label htmlFor="gcode-go-to" className="text-xs text-text-muted whitespace-nowrap">
            Go to line
          </label>
          <input
            id="gcode-go-to"
            ref={goToRef}
            value={goTo}
            onChange={(e) => setGoTo(e.target.value)}
            disabled={!ready}
            inputMode="numeric"
            placeholder="1"
            title="Go to line (⌘G / Ctrl+G)"
            className="w-24 px-2 py-1 rounded-lg text-xs bg-surface border border-border focus:outline-none focus:border-primary disabled:opacity-40"
          />
        </form>

        <button
          onClick={cancel}
          disabled={saving}
          className="px-4 py-1.5 rounded-lg text-xs font-semibold bg-surface-sunken text-text-secondary disabled:opacity-40"
        >
          Cancel
        </button>
        <button
          onClick={() => void save()}
          disabled={!ready || saving}
          title="Save (⌘S / Ctrl+S)"
          className="px-4 py-1.5 rounded-lg text-xs font-semibold text-white bg-primary disabled:opacity-40"
        >
          {saving ? 'Checking…' : 'Save'}
        </button>
      </div>

      {saveError && (
        <div
          role="alert"
          className="flex items-start gap-3 px-4 py-2 text-sm bg-danger-muted text-danger"
        >
          <pre className="flex-1 whitespace-pre-wrap break-words font-sans max-h-24 overflow-y-auto">
            {saveError.message}
          </pre>
          {saveError.line != null && (
            <button
              onClick={() => viewRef.current && goToLine(viewRef.current, saveError.line!)}
              className="shrink-0 text-xs font-semibold underline"
            >
              Show line {saveError.line.toLocaleString()}
            </button>
          )}
          <button
            onClick={() => setSaveError(null)}
            aria-label="Dismiss"
            className="shrink-0 font-bold text-lg leading-none"
          >
            &times;
          </button>
        </div>
      )}

      <div className="relative flex-1 min-h-0">
        <div ref={mountRef} className="absolute inset-0" />
        {load.kind !== 'ready' && (
          <div className="absolute inset-0 flex items-center justify-center text-sm">
            {load.kind === 'loading' ? (
              <span className="text-text-muted">Loading G-code…</span>
            ) : (
              <span className="text-danger">Couldn&apos;t load the G-code: {load.message}</span>
            )}
          </div>
        )}
      </div>

      {confirmDiscard && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-[rgba(40,43,43,0.7)]">
          <div
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="discard-title"
            className="rounded-3xl p-7 mx-4 max-w-sm bg-surface text-center shadow-[0_24px_60px_rgba(0,0,0,0.3)]"
          >
            <h2 id="discard-title" className="text-lg font-bold mb-1">
              Discard your changes?
            </h2>
            <p className="text-sm mb-6 text-text-muted">
              The edits you made to {filename} haven&apos;t been saved.
            </p>
            <div className="flex flex-col gap-2.5">
              <button
                autoFocus
                onClick={() => {
                  setConfirmDiscard(false)
                  viewRef.current?.focus()
                }}
                className="w-full py-3 rounded-2xl font-semibold text-sm text-white bg-primary"
              >
                Keep editing
              </button>
              <button
                onClick={onClose}
                className="w-full py-3 rounded-2xl font-semibold text-sm bg-surface-sunken text-danger"
              >
                Discard changes
              </button>
            </div>
          </div>
        </div>
      )}
    </div>,
    document.body
  )
}
