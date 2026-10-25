import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useTemperature } from '../stores/temperature'
import { channelCommand, usedChannels, type ChannelAction } from './temperatureChannels'

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
  { label: 'Absolute', gcode: 'G90' },
  { label: 'Read Temp', gcode: 'M105' }
] as const

const MAX_HISTORY = 50
const MIN_TEMP_C = 0
const MAX_TEMP_C = 120

export function GcodeInput({ disabled, printing = false }: GcodeInputProps): React.JSX.Element {
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<string[]>([])
  const [historyIndex, setHistoryIndex] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  // Inline M104 field: null while closed, else the raw °C text
  const [tempText, setTempText] = useState<string | null>(null)
  // Lab firmware (protocol "printess"): M104 sets one of its heater channels,
  // those in use, by what they heat
  const status = useTemperature((s) => s.status)
  const channels = usedChannels(status)
  const printess = status?.protocol === 'printess'
  const [tempSensor, setTempSensor] = useState<string | null>(null)
  const tempChannel = channels.find((c) => c.sensor === tempSensor) ?? channels[0]

  // The protocol comes with the temperature status
  useEffect(() => {
    if (useTemperature.getState().status) return
    api
      .getTemperature()
      .then(useTemperature.getState().applyStatus)
      .catch(() => {})
  }, [])

  const commandsDisabled = disabled || printing

  // Resolves true once the command was accepted
  // Add to history (dedup consecutive identical commands)
  const remember = (line: string): void =>
    setHistory((prev) => {
      const updated = prev[prev.length - 1] === line ? prev : [...prev, line]
      return updated.length > MAX_HISTORY ? updated.slice(-MAX_HISTORY) : updated
    })

  const sendCommand = useCallback(async (line: string): Promise<boolean> => {
    const trimmed = line.trim()
    if (!trimmed) return false

    setError(null)
    setSending(true)
    try {
      await api.sendGcode(trimmed)
      remember(trimmed)
      setInput('')
      setHistoryIndex(-1)
      return true
    } catch (e) {
      setError((e instanceof Error && e.message) || 'Command failed. Check the connection.')
      return false
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

  const tempValue = parseFloat(tempText ?? '')
  const tempMin = printess ? (tempChannel?.min ?? MIN_TEMP_C) : MIN_TEMP_C
  const tempMax = printess ? (tempChannel?.max ?? MAX_TEMP_C) : MAX_TEMP_C
  const tempValid = tempValue >= tempMin && tempValue <= tempMax
  const tempInvalid = !!tempText && !tempValid

  const handleSetTemp = async (): Promise<void> => {
    if (!tempValid || sending || printess) return
    if (await sendCommand(`M104 S${tempValue}`)) setTempText(null)
  }

  // Lab firmware: heat (H) or cool (C) the channel to the target, or switch it off (D)
  const handleChannel = async (action: ChannelAction): Promise<void> => {
    if (!tempChannel || sending || (action !== 'off' && !tempValid)) return
    setError(null)
    setSending(true)
    try {
      const { command } =
        action === 'off'
          ? await api.setTemperatureTarget(tempChannel.sensor, 0)
          : await api.setTemperatureTarget(tempChannel.sensor, tempValue, action)
      remember(command)
      if (action !== 'off') setTempText(null)
    } catch (e) {
      setError((e instanceof Error && e.message) || 'Setting the temperature failed')
    } finally {
      setSending(false)
    }
  }

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
            onClick={() => setTempText((t) => (t === null ? '' : null))}
            disabled={commandsDisabled || sending}
            title={printess ? 'M104 I<channel> T<°C> H|C, or M104 I<channel> D' : 'M104 S<°C>'}
            aria-expanded={tempText !== null}
            className={`px-3 py-3 rounded font-medium text-sm transition-opacity active:opacity-70 disabled:opacity-40 disabled:cursor-not-allowed ${
              tempText !== null
                ? 'bg-primary-muted text-primary'
                : 'bg-surface-sunken text-text-secondary'
            }`}
          >
            <span className="block">Set Temp</span>
            <span className="block text-xs opacity-60 font-mono">
              {printess ? 'M104 I…' : 'M104'}
            </span>
          </button>
          {tempText !== null && !printess && (
            <div className="col-span-2">
              <div className="flex gap-2">
                <label className="flex-1 flex items-center gap-2 rounded px-3 py-2 bg-surface-card border border-border">
                  <span className="font-mono text-sm text-text-muted">M104 S</span>
                  <input
                    type="number"
                    min={tempMin}
                    max={tempMax}
                    step="1"
                    autoFocus
                    value={tempText}
                    onChange={(e) => setTempText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleSetTemp()
                      else if (e.key === 'Escape') setTempText(null)
                    }}
                    disabled={commandsDisabled || sending}
                    aria-label="Target temperature in °C"
                    aria-invalid={tempInvalid}
                    className={`flex-1 min-w-0 bg-transparent font-mono text-sm outline-none ${
                      tempInvalid ? 'text-danger' : 'text-text'
                    }`}
                  />
                  <span className="text-sm text-text-muted">°C</span>
                </label>
                <button
                  onClick={handleSetTemp}
                  disabled={commandsDisabled || sending || !tempValid}
                  className="px-4 py-2 rounded font-medium text-sm text-white transition-opacity active:opacity-70 disabled:opacity-40 disabled:cursor-not-allowed bg-primary"
                >
                  Set
                </button>
              </div>
              {tempInvalid && (
                <p className="mt-1 text-xs text-danger">
                  Temperature must be between {tempMin} and {tempMax} °C
                </p>
              )}
            </div>
          )}
          {tempText !== null && printess && (
            <div className="col-span-2 flex flex-col gap-2">
              {channels.length === 0 ? (
                <p className="text-xs text-text-subtle">
                  No heater channel in use: choose what each channel heats in Setup.
                </p>
              ) : (
                <>
                  <div className="flex gap-2">
                    <select
                      value={tempChannel?.sensor ?? ''}
                      onChange={(e) => setTempSensor(e.target.value)}
                      disabled={commandsDisabled || sending}
                      aria-label="Heater channel"
                      className="flex-1 min-w-0 rounded px-2 py-2 text-sm bg-surface-card border border-border text-text"
                    >
                      {channels.map((c) => (
                        <option key={c.sensor} value={c.sensor}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                    <label className="w-24 flex items-center gap-1 rounded px-3 py-2 bg-surface-card border border-border">
                      <input
                        type="number"
                        min={tempMin}
                        max={tempMax}
                        step="1"
                        autoFocus
                        value={tempText}
                        placeholder={`${tempMin}–${tempMax}`}
                        onChange={(e) => setTempText(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Escape') setTempText(null)
                        }}
                        disabled={commandsDisabled || sending}
                        aria-label="Target temperature in °C"
                        aria-invalid={tempInvalid}
                        className={`flex-1 min-w-0 bg-transparent font-mono text-sm outline-none ${
                          tempInvalid ? 'text-danger' : 'text-text'
                        }`}
                      />
                      <span className="text-sm text-text-muted">°C</span>
                    </label>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {(['heat', 'cool', 'off'] as const).map((action) => (
                      <button
                        key={action}
                        onClick={() => handleChannel(action)}
                        disabled={
                          commandsDisabled ||
                          sending ||
                          !tempChannel ||
                          (action !== 'off' && !tempValid)
                        }
                        title={
                          tempChannel &&
                          channelCommand(
                            tempChannel.index,
                            action,
                            tempValid ? tempValue : undefined
                          )
                        }
                        className={`px-3 py-2 rounded font-medium text-sm transition-opacity active:opacity-70 disabled:opacity-40 disabled:cursor-not-allowed ${
                          action === 'off'
                            ? 'bg-surface-sunken text-text-secondary'
                            : 'bg-primary text-white'
                        }`}
                      >
                        <span className="block">
                          {action === 'heat' ? 'Heat' : action === 'cool' ? 'Cool' : 'Off'}
                        </span>
                        {tempChannel && (
                          <span className="block text-xs opacity-70 font-mono">
                            {channelCommand(
                              tempChannel.index,
                              action,
                              tempValid ? tempValue : undefined
                            )}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                  {tempInvalid && (
                    <p className="text-xs text-danger">
                      Temperature must be between {tempMin} and {tempMax} °C
                    </p>
                  )}
                </>
              )}
            </div>
          )}
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
