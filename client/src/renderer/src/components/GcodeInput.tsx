import { useCallback, useRef, useState } from 'react'
import { api } from '../api'

interface GcodeInputProps {
  disabled: boolean
}

const QUICK_COMMANDS = [
  { label: 'Home All', gcode: 'G28', style: 'default' as const },
  { label: 'Home XY', gcode: 'G28 X0 Y0', style: 'default' as const },
  { label: 'Position', gcode: 'M114', style: 'default' as const },
  { label: 'Settings', gcode: 'M503', style: 'default' as const },
  { label: 'Relative', gcode: 'G91', style: 'default' as const },
  { label: 'Absolute', gcode: 'G90', style: 'default' as const },
  { label: 'STOP', gcode: 'M410', style: 'danger' as const },
] as const

const MAX_HISTORY = 50

export function GcodeInput({ disabled }: GcodeInputProps) {
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)

  const sendCommand = useCallback(async (line: string) => {
    const trimmed = line.trim()
    if (!trimmed) return

    setError(null)
    setSending(true)
    try {
      await api.sendGcode(trimmed)
      // Add to history (dedup consecutive identical commands)
      setHistory((prev) => {
        const updated = prev[prev.length - 1] === trimmed ? prev : [...prev, trimmed]
        return updated.length > MAX_HISTORY ? updated.slice(-MAX_HISTORY) : updated
      })
      setInput('')
      setHistoryIndex(-1)
    } catch (e: any) {
      setError(e.message || 'Command failed. Check the connection.')
    } finally {
      setSending(false)
    }
  }, [])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !sending) {
      sendCommand(input)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (history.length === 0) return
      const newIndex = historyIndex === -1 ? history.length - 1 : Math.max(0, historyIndex - 1)
      setHistoryIndex(newIndex)
      setInput(history[newIndex])
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (historyIndex === -1) return
      if (historyIndex >= history.length - 1) {
        setHistoryIndex(-1)
        setInput('')
      } else {
        const newIndex = historyIndex + 1
        setHistoryIndex(newIndex)
        setInput(history[newIndex])
      }
    }
  }

  return (
    <div className="flex flex-col gap-4 h-full">
      {/* Header */}
      <div className="px-3 py-2 bg-gray-800 border-b border-gray-700">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Send G-code</h3>
      </div>

      {/* Input area */}
      <div className="px-3">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            onKeyDown={handleKeyDown}
            placeholder="e.g. G28, M114, G1 X10 F200"
            disabled={disabled || sending}
            className="flex-1 bg-gray-900 border border-gray-700 rounded px-3 py-2 text-white font-mono text-sm
              placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
              disabled:opacity-50 disabled:cursor-not-allowed"
          />
          <button
            onClick={() => sendCommand(input)}
            disabled={disabled || sending || !input.trim()}
            className="px-4 py-2 rounded bg-blue-600 text-white font-medium text-sm
              hover:bg-blue-500 active:bg-blue-700
              disabled:opacity-50 disabled:cursor-not-allowed
              min-w-[60px]"
          >
            {sending ? '...' : 'Send'}
          </button>
        </div>
        {error && (
          <p className="mt-2 text-sm text-red-400">{error}</p>
        )}
        {disabled && (
          <p className="mt-2 text-sm text-amber-400">Connect to the printer to send commands.</p>
        )}
      </div>

      {/* Quick commands */}
      <div className="px-3 flex-1">
        <p className="text-xs text-gray-500 uppercase tracking-wide mb-2">Quick Commands</p>
        <div className="grid grid-cols-2 gap-2">
          {QUICK_COMMANDS.map(({ label, gcode, style }) => (
            <button
              key={gcode}
              onClick={() => sendCommand(gcode)}
              disabled={disabled || sending}
              title={gcode}
              className={`px-3 py-3 rounded font-medium text-sm
                disabled:opacity-50 disabled:cursor-not-allowed
                ${style === 'danger'
                  ? 'bg-red-700 text-white hover:bg-red-600 active:bg-red-800 col-span-2'
                  : 'bg-gray-700 text-gray-200 hover:bg-gray-600 active:bg-gray-500'
                }`}
            >
              <span className="block">{label}</span>
              <span className="block text-xs opacity-60 font-mono">{gcode}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
