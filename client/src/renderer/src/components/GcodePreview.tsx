import type { UploadResult } from '../types'

interface GcodePreviewProps {
  result: UploadResult
}

export function GcodePreview({ result }: GcodePreviewProps) {
  const timeMin = result.time_estimate_s != null
    ? Math.round(result.time_estimate_s / 60)
    : null

  return (
    <div className="rounded-2xl overflow-hidden" style={{ border: '1px solid #D8D3C8' }}>
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 py-2.5"
        style={{ backgroundColor: '#EDE9DC', borderBottom: '1px solid #D8D3C8' }}
      >
        <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#8B9090' }}>
          G-code Preview
        </span>
        <div className="flex items-center gap-3">
          {timeMin != null && (
            <span className="text-xs font-medium" style={{ color: '#1A8B8D' }}>
              ~{timeMin} min
            </span>
          )}
          <span className="text-xs" style={{ color: '#8B9090' }}>
            {result.lines_total.toLocaleString()} lines
          </span>
        </div>
      </div>

      {/* Lines */}
      <div
        className="overflow-y-auto font-mono text-xs p-3 space-y-0.5"
        style={{ backgroundColor: '#F5F1E6', maxHeight: '140px' }}
      >
        {result.preview_lines.map((line, i) => (
          <div key={i} className="flex gap-3">
            <span className="shrink-0 select-none w-6 text-right" style={{ color: '#C8C3B8' }}>
              {i + 1}
            </span>
            <span style={{ color: line.startsWith(';') ? '#A0A8A8' : '#2D3333' }}>
              {line || ' '}
            </span>
          </div>
        ))}
        {result.lines_total > 40 && (
          <div className="pt-1" style={{ color: '#A0A8A8' }}>
            … {(result.lines_total - 40).toLocaleString()} more lines
          </div>
        )}
      </div>
    </div>
  )
}
