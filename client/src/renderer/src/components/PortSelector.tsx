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
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

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

  if (connected) {
    return (
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1.5">
          <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: '#1A8B8D' }} />
          <span className="text-xs font-medium" style={{ color: '#2D3333' }}>
            {selected || 'Connected'}
          </span>
        </div>
        <button
          onClick={handleDisconnect}
          className="text-xs px-2.5 py-1 rounded-lg font-medium transition-all active:scale-95"
          style={{ backgroundColor: '#E8E3D8', color: '#5A6060' }}
        >
          Disconnect
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <select
        value={selected}
        onChange={(e) => setSelected(e.target.value)}
        className="text-xs px-2.5 py-1.5 rounded-lg border outline-none"
        style={{ borderColor: '#D8D3C8', backgroundColor: 'white', color: '#2D3333' }}
      >
        {ports.length === 0 && <option value="">No ports found</option>}
        {ports.map((p) => (
          <option key={p.device} value={p.device}>
            {p.device}
          </option>
        ))}
      </select>

      <button
        onClick={refresh}
        className="text-xs px-2 py-1.5 rounded-lg border transition-all active:scale-95"
        style={{ borderColor: '#D8D3C8', backgroundColor: 'white', color: '#5A6060' }}
        title="Refresh ports"
      >
        ↺
      </button>

      <button
        onClick={handleConnect}
        disabled={!selected || loading}
        className="text-xs px-3 py-1.5 rounded-lg font-semibold text-white transition-all active:scale-95 disabled:opacity-40"
        style={{ backgroundColor: '#1A8B8D' }}
      >
        {loading ? 'Connecting…' : 'Connect'}
      </button>
    </div>
  )
}
