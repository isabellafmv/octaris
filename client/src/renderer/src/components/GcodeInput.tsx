import { useCallback, useRef, useState } from 'react'
import { api } from '../api'

interface GcodeInputProps {
  disabled: boolean
  printing?: boolean
}

const QUICK_COMMANDS = [
  { label: 'Home All', gcode: 'G28' },
  { label: 'Home XY', gcode: 'G28 X0 Y0' },
  { label: 'Position', gcode: 'M114' },
  { label: 'Settings', gcode: 'M503' },
  { label: 'Relative', gcode: 'G91' },
  { label: 'Absolute', gcode: 'G90' }
] as const

const MAX_HISTORY = 50

export function GcodeInput({ disabled, printing = false }: GcodeInputProps): React.JSX.Element {
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)

  const commandsDisabled = disabled || printing

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
    } catch (e) {
      setError((e instanceof Error && e.message) || 'Command failed. Check the connection.')
    } finally {
      setSending(false)
      // Delay focus to ensure React has re-rendered the enabled input
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [])

  const handleStop = useCallback(async () => {
    setError(null)
    setSending(true)
    try {
      await api.printStop()
    } catch (e) {
      setError((e instanceof Error && e.message) || 'Stop failed. Check the connection.')
    } finally {
      setSending(false)
    }
  }, [])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
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
      <div className="px-3 py-2 border-b shrink-0 bg-surface-card border-border">
        <h3 className="text-xs font-semibold uppercase tracking-widest text-text-muted">
          Send G-code
        </h3>
      </div>

      {/* Input area */}
      <div className="px-3 pt-3">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            onKeyDown={handleKeyDown}
            placeholder="e.g. G28, M114, G1 X10 F200"
            disabled={commandsDisabled || sending}
            className="flex-1 rounded px-3 py-2 font-mono text-sm focus:outline-none focus:ring-1 disabled:opacity-50 disabled:cursor-not-allowed bg-surface-card border border-border text-text"
          />
          <button
            onClick={() => sendCommand(input)}
            disabled={commandsDisabled || sending || !input.trim()}
            className="px-4 py-2 rounded font-medium text-sm text-white transition-opacity active:opacity-70 disabled:opacity-40 disabled:cursor-not-allowed min-w-15 bg-primary"
          >
            {sending ? '...' : 'Send'}
          </button>
        </div>
        {error && <p className="mt-2 text-sm text-warning">{error}</p>}
        {disabled && (
          <p className="mt-2 text-sm text-text-subtle">Connect to the printer to send commands.</p>
        )}
        {!disabled && printing && (
          <p className="mt-2 text-sm text-text-subtle">
            Print running — use Monitor to pause or stop
          </p>
        )}
      </div>

      {/* Quick commands */}
      <div className="px-3 pt-4 flex-1">
        <p className="text-xs uppercase tracking-widest mb-2 text-text-muted">Quick Commands</p>
        <div className="grid grid-cols-2 gap-2">
          {QUICK_COMMANDS.map(({ label, gcode }) => (
            <button
              key={gcode}
              onClick={() => sendCommand(gcode)}
              disabled={commandsDisabled || sending}
              title={gcode}
              className="px-3 py-3 rounded font-medium text-sm transition-opacity active:opacity-70 disabled:opacity-40 disabled:cursor-not-allowed bg-surface-sunken text-text-secondary"
            >
              <span className="block">{label}</span>
              <span className="block text-xs opacity-60 font-mono">{gcode}</span>
            </button>
          ))}
          <button
            onClick={handleStop}
            disabled={disabled || sending}
            title="M410 (via print stop)"
            className="px-3 py-3 rounded font-medium text-sm transition-opacity active:opacity-70 disabled:opacity-40 disabled:cursor-not-allowed col-span-2 bg-danger-strong text-white"
          >
            <span className="block">STOP</span>
          </button>
        </div>
      </div>
    </div>
  )
}
