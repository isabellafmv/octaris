import { useCallback, useEffect, useRef, useState } from 'react'
import type { PrintStatus, SerialLogEntry, WsEvent } from '../types'

const WS_URL = 'ws://localhost:8000/ws'
const RECONNECT_DELAY = 2000
const MAX_LOG_ENTRIES = 200

interface PrintState {
  status: PrintStatus
  linesSent: number
  linesTotal: number
  timeRemainingS: number | null
  extrusionRate: number
  connected: boolean
  serialLog: SerialLogEntry[]
}

export function useWebSocket() {
  const [state, setState] = useState<PrintState>({
    status: 'idle',
    linesSent: 0,
    linesTotal: 0,
    timeRemainingS: null,
    extrusionRate: 100,
    connected: false,
    serialLog: []
  })

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setState((s) => ({ ...s, connected: true }))
    }

    ws.onmessage = (evt) => {
      const data: WsEvent = JSON.parse(evt.data)
      setState((prev) => {
        switch (data.type) {
          case 'progress':
            return {
              ...prev,
              linesSent: data.lines_sent ?? prev.linesSent,
              linesTotal: data.lines_total ?? prev.linesTotal
            }
          case 'time_remaining_s':
            return { ...prev, timeRemainingS: (data.value as number) ?? prev.timeRemainingS }
          case 'status':
            return { ...prev, status: (data.value as PrintStatus) ?? prev.status }
          case 'extrusion_rate':
            return { ...prev, extrusionRate: (data.value as number) ?? prev.extrusionRate }
          case 'disconnected':
            return { ...prev, connected: false }
          case 'serial_log':
            if (data.entry) {
              const updated = [...prev.serialLog, data.entry]
              return {
                ...prev,
                serialLog: updated.length > MAX_LOG_ENTRIES
                  ? updated.slice(-MAX_LOG_ENTRIES)
                  : updated
              }
            }
            return prev
          default:
            return prev
        }
      })
    }

    ws.onclose = () => {
      setState((s) => ({ ...s, connected: false }))
      reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const clearSerialLog = useCallback(() => {
    setState((s) => ({ ...s, serialLog: [] }))
  }, [])

  const setSerialLog = useCallback((entries: SerialLogEntry[]) => {
    setState((s) => ({ ...s, serialLog: entries }))
  }, [])

  return { ...state, clearSerialLog, setSerialLog }
}
