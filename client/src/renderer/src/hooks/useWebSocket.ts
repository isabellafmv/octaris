import { useCallback, useEffect, useRef, useState } from 'react'
import { useSerialLog } from '../stores/serialLog'
import type { PrintStatus, StopInfo, WsEvent } from '../types'

const WS_BASE_URL = 'ws://127.0.0.1:8000/ws'

// Present in Electron (set by preload from --octaris-token=...); absent when
// the renderer is opened outside Electron, e.g. a plain browser during dev.
function wsUrl(): string {
  const token = window.octaris?.token
  return token ? `${WS_BASE_URL}?token=${encodeURIComponent(token)}` : WS_BASE_URL
}
const RECONNECT_DELAY = 2000

interface PrintState {
  status: PrintStatus
  linesSent: number
  linesTotal: number
  timeRemainingS: number | null
  extrusionRate: number
  // Whether the websocket to the backend itself is open.
  wsConnected: boolean
  // Whether the backend currently has a serial connection to the printer.
  printerConnected: boolean
  port: string | null
  calibrated: boolean
  // id changes on every error event, so a repeated message is shown again
  lastError: { id: number; message: string } | null
  stopInfo: StopInfo
}

export function useWebSocket(): PrintState & { resetPrintState: () => void } {
  const [state, setState] = useState<PrintState>({
    status: 'idle',
    linesSent: 0,
    linesTotal: 0,
    timeRemainingS: null,
    extrusionRate: 100,
    wsConnected: false,
    printerConnected: false,
    port: null,
    calibrated: false,
    lastError: null,
    stopInfo: null
  })

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(wsUrl())
    wsRef.current = ws

    ws.onopen = () => {
      setState((s) => ({ ...s, wsConnected: true }))
    }

    ws.onmessage = (evt) => {
      const data: WsEvent = JSON.parse(evt.data)
      // Serial lines go to their own store so they don't re-render the app
      if (data.type === 'serial_log') {
        if (data.entry) useSerialLog.getState().append(data.entry)
        return
      }
      setState((prev) => {
        switch (data.type) {
          case 'progress':
            return {
              ...prev,
              linesSent: data.lines_sent ?? prev.linesSent,
              linesTotal: data.lines_total ?? prev.linesTotal,
              timeRemainingS: data.time_remaining_s ?? prev.timeRemainingS
            }
          case 'status':
            return {
              ...prev,
              status: data.value ?? prev.status,
              // A new stop gets its own stop event; drop the old one's info
              stopInfo: data.value === 'stopped' ? prev.stopInfo : null
            }
          case 'stop':
            return { ...prev, stopInfo: { resumable: data.resumable, reason: data.reason } }
          case 'extrusion_rate':
            return { ...prev, extrusionRate: data.value ?? prev.extrusionRate }
          case 'error':
          case 'warning':
            return {
              ...prev,
              lastError: {
                id: (prev.lastError?.id ?? 0) + 1,
                message: data.message ?? 'Printer error'
              }
            }
          case 'printer':
            return { ...prev, printerConnected: data.connected, port: data.port }
          case 'snapshot': {
            const timeRemainingS =
              data.time_estimate_s != null && data.lines_total > 0
                ? data.time_estimate_s * (1 - data.lines_sent / data.lines_total)
                : prev.timeRemainingS
            return {
              ...prev,
              printerConnected: data.printer_connected,
              port: data.port,
              status: data.print_status,
              linesSent: data.lines_sent,
              linesTotal: data.lines_total,
              calibrated: data.calibrated,
              extrusionRate: data.flow_rate,
              stopInfo:
                data.print_status === 'stopped'
                  ? { resumable: data.resumable, reason: data.stop_reason }
                  : null,
              timeRemainingS
            }
          }
          case 'calibration':
            return { ...prev, calibrated: data.value === 'calibrated' }
          default:
            return prev
        }
      })
    }

    ws.onclose = () => {
      setState((s) => ({ ...s, wsConnected: false }))
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

  const resetPrintState = useCallback(() => {
    setState((s) => ({
      ...s,
      status: 'idle',
      linesSent: 0,
      linesTotal: 0,
      timeRemainingS: null,
      extrusionRate: 100,
    }))
  }, [])

  return { ...state, resetPrintState }
}
