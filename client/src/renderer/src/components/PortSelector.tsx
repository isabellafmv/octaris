import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { PortInfo } from '../types'

interface PortSelectorProps {
  connected: boolean
  onConnect: (port: string) => void
  onDisconnect: () => void
  onError: (msg: string) => void
}

export function PortSelector({ connected, onConnect, onDisconnect, onError }: PortSelectorProps) {
  const [ports, setPorts] = useState<PortInfo[]>([])
  const [selected, setSelected] = useState('')
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const data = await api.getPorts()
      setPorts(data.ports)
      if (data.ports.length > 0 && !selected) {
        setSelected(data.ports[0].device)
      }
    } catch {
      onError('Could not list serial ports')
    }
  }, [selected, onError])

  useEffect(() => {
    refresh()
  }, [])

  const handleConnect = async () => {
    if (!selected) return
    setLoading(true)
    try {
      await api.connect(selected)
      onConnect(selected)
    } catch {
      onError('Printer not found. Check the USB cable and try again.')
    } finally {
      setLoading(false)
    }
  }

  const handleDisconnect = async () => {
    try {
      await api.disconnect()
      onDisconnect()
    } catch {
      onError('Failed to disconnect')
    }
  }

  return (
    <div className="flex items-center gap-3 flex-wrap">
      <select
        value={selected}
        onChange={(e) => setSelected(e.target.value)}
        disabled={connected}
        className="min-h-[48px] px-3 py-2 border rounded-lg bg-white text-base disabled:opacity-50"
      >
        {ports.length === 0 && <option value="">No ports found</option>}
        {ports.map((p) => (
          <option key={p.device} value={p.device}>
            {p.device} — {p.description}
          </option>
        ))}
      </select>

      <button
        onClick={refresh}
        disabled={connected}
        className="min-h-[48px] px-4 py-2 rounded-lg border text-base active:scale-95 transition-transform disabled:opacity-50"
      >
        Refresh
      </button>

      {!connected ? (
        <button
          onClick={handleConnect}
          disabled={!selected || loading}
          className="min-h-[48px] px-6 py-2 rounded-lg bg-green-600 text-white text-base font-semibold active:scale-95 transition-transform disabled:opacity-50"
        >
          {loading ? 'Connecting...' : 'Connect'}
        </button>
      ) : (
        <button
          onClick={handleDisconnect}
          className="min-h-[48px] px-6 py-2 rounded-lg bg-gray-600 text-white text-base font-semibold active:scale-95 transition-transform"
        >
          Disconnect
        </button>
      )}
    </div>
  )
}
