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
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800 border-b border-gray-700">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Serial Log</h3>
        <div className="flex items-center gap-2">
          {!autoScroll && (
            <button
              onClick={() => {
                setAutoScroll(true)
                if (containerRef.current) {
                  containerRef.current.scrollTop = containerRef.current.scrollHeight
                }
              }}
              className="text-xs px-2 py-1 rounded bg-gray-700 text-gray-300 hover:bg-gray-600 active:bg-gray-500"
            >
              ↓ Scroll to bottom
            </button>
          )}
          <button
            onClick={onClear}
            className="text-xs px-2 py-1 rounded bg-gray-700 text-gray-300 hover:bg-gray-600 active:bg-gray-500"
          >
            Clear
          </button>
        </div>
      </div>
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto bg-gray-950 p-3 font-mono text-sm leading-relaxed"
      >
        {entries.length === 0 ? (
          <p className="text-gray-600 italic">No serial activity yet...</p>
        ) : (
          entries.map((entry, i) => (
            <div key={i} className="flex gap-2">
              <span className="text-gray-600 shrink-0 select-none">{formatTime(entry.timestamp)}</span>
              <span className={entry.direction === 'sent' ? 'text-blue-400' : 'text-green-400'}>
                {entry.direction === 'sent' ? '›' : '‹'}
              </span>
              <span className={entry.direction === 'sent' ? 'text-blue-300' : 'text-green-300'}>
                {entry.content}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
