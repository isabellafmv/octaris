import type { UploadResult } from '../types'

interface GcodePreviewProps {
  result: UploadResult
}

export function GcodePreview({ result }: GcodePreviewProps): React.JSX.Element {
  const timeMin = result.time_estimate_s != null
    ? Math.round(result.time_estimate_s / 60)
    : null

  return (
    <div className="rounded-2xl overflow-hidden border border-border">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-surface-card border-b border-b-border">
        <span className="text-xs font-semibold uppercase tracking-widest text-text-muted">
          G-code Preview
        </span>
        <div className="flex items-center gap-3">
          {timeMin != null && (
            <span className="text-xs font-medium text-primary">
              ~{timeMin} min
            </span>
          )}
          <span className="text-xs text-text-muted">
            {result.lines_total.toLocaleString()} lines
          </span>
        </div>
      </div>

      {/* Lines */}
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
    </div>
  )
}
