import { useState } from 'react'
import type { GcodeEditResult, UploadResult } from '../types'
import { GcodeEditor } from './GcodeEditor'
import { GcodeViewer } from './GcodeViewer'

interface GcodePreviewProps {
  result: UploadResult
}

type View = '3d' | 'text'
const VIEWS: { view: View; label: string }[] = [
  { view: '3d', label: '3D' },
  { view: 'text', label: 'Text' }
]

export function GcodePreview({ result: uploaded }: GcodePreviewProps): React.JSX.Element {
  const [view, setView] = useState<View>('3d')
  // The last save from the editor. A new upload remounts the preview, so
  // this never outlives the file it was made from.
  const [saved, setSaved] = useState<GcodeEditResult | null>(null)
  const [editing, setEditing] = useState(false)
  const result: UploadResult = saved ?? uploaded
  const timeMin = result.time_estimate_s != null ? Math.round(result.time_estimate_s / 60) : null

  return (
    <div className="rounded-2xl overflow-hidden border border-border shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 px-4 py-2 whitespace-nowrap bg-surface-card border-b border-b-border">
        <div className="flex items-center gap-3">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-muted">
            G-code Preview
          </span>
          <div className="flex rounded-lg p-0.5 gap-0.5 bg-surface-sunken">
            {VIEWS.map(({ view: v, label }) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-2 py-0.5 rounded-md text-[11px] font-semibold transition-all ${
                  view === v
                    ? 'bg-white text-primary shadow-[0_1px_2px_rgba(0,0,0,0.08)]'
                    : 'text-text-muted'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-xs text-text-secondary truncate" title={result.filename}>
            {result.filename}
          </span>
          {result.edited && (
            <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wider bg-primary-muted text-primary-strong">
              Edited
            </span>
          )}
          {timeMin != null && (
            <span className="text-xs font-medium text-primary">~{timeMin} min</span>
          )}
          <span className="text-xs text-text-muted">
            {result.lines_total.toLocaleString()} lines
          </span>
          <button
            onClick={() => setEditing(true)}
            title="Edit G-code"
            aria-label="Edit G-code"
            className="p-1 -mr-1 rounded-md text-text-muted transition-colors hover:text-primary hover:bg-surface-sunken"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              className="w-4 h-4"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L6.832 19.82a4.5 4.5 0 0 1-1.897 1.13l-2.685.8.8-2.685a4.5 4.5 0 0 1 1.13-1.897L16.863 4.487Zm0 0L19.5 7.125"
              />
            </svg>
          </button>
        </div>
      </div>

      {/* Kept mounted while hidden, so switching back doesn't re-parse. A
          saved edit remounts it, to fetch and parse the new program. */}
      <div className={view === '3d' ? '' : 'hidden'}>
        <GcodeViewer key={saved?.program_id ?? 'uploaded'} />
      </div>

      {/* Lines */}
      {view === 'text' && (
        <div className="overflow-y-auto font-mono text-xs p-3 space-y-0.5 bg-surface max-h-[140px]">
          {result.preview_lines.map((line, i) => (
            <div key={i} className="flex gap-3">
              <span className="shrink-0 select-none w-6 text-right text-border-strong">
                {i + 1}
              </span>
              <span className={line.startsWith(';') ? 'text-text-subtle' : 'text-text'}>
                {line || ' '}
              </span>
            </div>
          ))}
          {result.lines_total > 40 && (
            <div className="pt-1 text-text-subtle">
              … {(result.lines_total - 40).toLocaleString()} more lines
            </div>
          )}
        </div>
      )}

      {editing && (
        <GcodeEditor
          filename={result.filename}
          onSaved={(edit) => {
            setSaved(edit)
            setEditing(false)
          }}
          onClose={() => setEditing(false)}
        />
      )}
    </div>
  )
}
