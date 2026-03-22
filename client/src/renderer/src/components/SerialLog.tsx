import { useEffect, useRef, useState } from 'react'
import type { SerialLogEntry } from '../types'

interface SerialLogProps {
  entries: SerialLogEntry[]
  onClear: () => void
}

export function SerialLog({ entries, onClear }: SerialLogProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  // Auto-scroll to bottom on new entries (unless user scrolled up)
  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [entries, autoScroll])

  const handleScroll = () => {
    if (!containerRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current
    // If user is within 40px of bottom, keep auto-scrolling
    const atBottom = scrollHeight - scrollTop - clientHeight < 40
    setAutoScroll(atBottom)
  }

  const formatTime = (iso: string) => {
    try {
      const d = new Date(iso)
      return d.toLocaleTimeString('en-GB', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
    } catch {
      return ''
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div
        className="flex items-center justify-between px-3 py-2 border-b shrink-0"
        style={{ backgroundColor: '#EDE9DC', borderColor: '#D8D3C8' }}
      >
        <h3 className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#8B9090' }}>Serial Log</h3>
        <div className="flex items-center gap-2">
          {!autoScroll && (
            <button
              onClick={() => {
                setAutoScroll(true)
                if (containerRef.current) {
                  containerRef.current.scrollTop = containerRef.current.scrollHeight
                }
              }}
              className="text-xs px-2 py-1 rounded transition-opacity active:opacity-60"
              style={{ backgroundColor: '#D8D3C8', color: '#5A6060' }}
            >
              ↓ Scroll to bottom
            </button>
          )}
          <button
            onClick={onClear}
            className="text-xs px-2 py-1 rounded transition-opacity active:opacity-60"
            style={{ backgroundColor: '#D8D3C8', color: '#5A6060' }}
          >
            Clear
          </button>
        </div>
      </div>
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-3 font-mono text-sm leading-relaxed"
        style={{ backgroundColor: '#F5F1E6' }}
      >
        {entries.length === 0 ? (
          <p className="italic" style={{ color: '#A0A8A8' }}>No serial activity yet...</p>
        ) : (
          entries.map((entry, i) => (
            <div key={i} className="flex gap-2">
              <span className="shrink-0 select-none" style={{ color: '#A0A8A8' }}>{formatTime(entry.timestamp)}</span>
              <span style={{ color: entry.direction === 'sent' ? '#1A8B8D' : '#B5614A' }}>
                {entry.direction === 'sent' ? '›' : '‹'}
              </span>
              <span style={{ color: entry.direction === 'sent' ? '#1A8B8D' : '#2D3333' }}>
                {entry.content}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
