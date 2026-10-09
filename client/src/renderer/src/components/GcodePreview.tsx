import { useState } from 'react'
import type { UploadResult } from '../types'
import { GcodeViewer } from './GcodeViewer'

interface GcodePreviewProps {
  result: UploadResult
}

type View = '3d' | 'text'
const VIEWS: { view: View; label: string }[] = [
  { view: '3d', label: '3D' },
  { view: 'text', label: 'Text' }
]

export function GcodePreview({ result }: GcodePreviewProps): React.JSX.Element {
  const [view, setView] = useState<View>('3d')
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
        <div className="flex items-center gap-3">
          {timeMin != null && (
            <span className="text-xs font-medium text-primary">~{timeMin} min</span>
          )}
          <span className="text-xs text-text-muted">
            {result.lines_total.toLocaleString()} lines
          </span>
        </div>
      </div>

      {/* Kept mounted while hidden, so switching back doesn't re-parse */}
      <div className={view === '3d' ? '' : 'hidden'}>
        <GcodeViewer />
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
    </div>
  )
}
