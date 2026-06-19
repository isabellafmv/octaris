import { useCallback, useState } from 'react'
import { ExtrusionSlider } from '../components/ExtrusionSlider'
import { PrintOverlay } from '../components/PrintOverlay'
import { api } from '../api'
import type { PrintStatus } from '../types'

function SetupIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="w-5 h-5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75" />
    </svg>
  )
}

function MonitorIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="w-5 h-5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
    </svg>
  )
}

function LibraryIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="w-5 h-5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
    </svg>
  )
}

interface PrintScreenProps {
  status: PrintStatus
  linesSent: number
  linesTotal: number
  timeRemainingS: number | null
  extrusionRate: number
  filename: string | null
  onBack: () => void
  onRestart: () => void
  onTakeOver: () => void
  printerConnected: boolean
}



function CircularProgress({ percentage }: { percentage: number }) {
  const r = 88
  const cx = 120
  const cy = 120
  const circumference = 2 * Math.PI * r

  return (
    <svg viewBox="0 0 240 240" className="w-52 h-52">
      {/* Pale outer gradient fill */}
      <defs>
        <radialGradient id="innerGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#D4EAE9" stopOpacity="0.6" />
          <stop offset="100%" stopColor="#D4EAE9" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx={cx} cy={cy} r={r + 12} fill="url(#innerGlow)" />

      {/* Track */}
      <circle
        cx={cx} cy={cy} r={r}
        fill="none"
        stroke="#E0DBD0"
        strokeWidth="7"
        strokeLinecap="round"
      />
      {/* Progress arc */}
      <circle
        cx={cx} cy={cy} r={r}
        fill="none"
        stroke="#1A8B8D"
        strokeWidth="7"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference - (percentage / 100) * circumference}
        transform={`rotate(-90 ${cx} ${cy})`}
        style={{ transition: 'stroke-dashoffset 0.5s ease' }}
      />
      {/* Percentage */}
      <text
        x={cx} y={cy - 10}
        textAnchor="middle"
        fill="#1A8B8D"
        fontSize="46"
        fontWeight="700"
        fontFamily="DM Sans, system-ui, sans-serif"
      >
        {percentage}
      </text>
      <text
        x={cx} y={cy + 18}
        textAnchor="middle"
        fill="#1A8B8D"
        fontSize="13"
        fontWeight="500"
        fontFamily="DM Sans, system-ui, sans-serif"
      >
        %
      </text>
      <text
        x={cx} y={cy + 40}
        textAnchor="middle"
        fill="#8B9090"
        fontSize="10"
        letterSpacing="3"
        fontFamily="DM Sans, system-ui, sans-serif"
      >
        COMPLETED
      </text>
    </svg>
  )
}

export function PrintScreen({
  status,
  linesSent,
  linesTotal,
  timeRemainingS: _timeRemainingS,
  extrusionRate,
  filename,
  onBack,
  onRestart,
  onTakeOver
}: PrintScreenProps) {
  const [error, setError] = useState<string | null>(null)

  const handleStop = useCallback(async () => {
    try {
      await api.printStop()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Stop failed')
    }
  }, [])

  const handlePause = useCallback(async () => {
    try {
      await api.printPause()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Pause failed')
    }
  }, [])

  const handleResume = useCallback(async () => {
    try {
      await api.printResume()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Resume failed')
    }
  }, [])

  const percentage = linesTotal > 0 ? Math.round((linesSent / linesTotal) * 100) : 0
  const vesselName = filename ?? 'Printing File'
  const isStabilized = status === 'printing'

  return (
    <div className="flex h-full" style={{ color: '#2D3333' }}>
      {/* ── Sidebar ── */}
      <div
        className="flex flex-col items-center gap-1 py-5 px-3 shrink-0 border-r"
        style={{ borderColor: '#E8E3D8', width: '72px' }}
      >
        {/* Logo text */}
        <div className="mb-3">
          <span
            className="text-xs font-bold tracking-widest uppercase"
            style={{ color: '#1A8B8D', writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}
          >
            Octaris
          </span>
        </div>

        {/* Nav icons */}
        {[
          { icon: <SetupIcon />, label: 'SETUP', action: onBack },
          { icon: <MonitorIcon />, label: 'MONITOR', active: true, action: () => {} },
          { icon: <LibraryIcon />, label: 'LOGS', action: onTakeOver },
        ].map((item) => (
          <div key={item.label} className="flex flex-col items-center gap-0.5 w-full">
            <button
              onClick={item.action}
              className="w-10 h-10 rounded-full flex items-center justify-center transition-all active:scale-90"
              style={
                item.active
                  ? { backgroundColor: '#1A8B8D', color: 'white' }
                  : { color: '#8B9090' }
              }
            >
              {item.icon}
            </button>
            <span
              className="text-[8px] tracking-widest uppercase font-medium"
              style={{ color: item.active ? '#1A8B8D' : '#A0A8A8' }}
            >
              {item.label}
            </span>
          </div>
        ))}
      </div>

      {/* ── Main content ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {error && (
          <div
            className="mx-6 mt-4 px-4 py-2 rounded-lg text-sm flex items-center justify-between"
            style={{ backgroundColor: '#FDEAEA', color: '#B54040' }}
          >
            <span>{error}</span>
            <button onClick={() => setError(null)} className="ml-3 font-bold text-lg leading-none">&times;</button>
          </div>
        )}

        {/* Header */}
        <div className="flex items-start justify-between px-7 pt-6 pb-2 shrink-0">
          <div>
            <p className="text-xs font-semibold tracking-widest uppercase" style={{ color: '#8B9090' }}>
              Live Feed
            </p>
            <h2 className="text-2xl font-bold mt-0.5">{vesselName}</h2>
          </div>
          <div className="flex items-center gap-1.5 mt-2">
            <div
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: isStabilized ? '#1A8B8D' : '#B5614A' }}
            />
            <span className="text-[10px] font-semibold tracking-widest uppercase" style={{ color: '#8B9090' }}>
              {isStabilized ? 'READY' : status.toUpperCase()}
            </span>
          </div>
        </div>

        {/* Circular progress */}
        <div className="flex justify-center py-3 shrink-0">
          <CircularProgress percentage={percentage} />
        </div>

        {/* Stats row */}
        <div
          className="mx-7 rounded-2xl overflow-hidden shrink-0"
          style={{
            background: 'linear-gradient(135deg, #D4EAE9 0%, #E8E3D8 100%)',
          }}
        >
          <div className="flex">
            <div className="flex-1 px-5 py-4 border-r" style={{ borderColor: '#C8C3B4' }}>
              <p className="text-[9px] font-semibold tracking-widest uppercase" style={{ color: '#5A7070' }}>
                Lines Sent
              </p>
              <p className="text-2xl font-bold mt-1" style={{ color: '#2D3333' }}>
                {linesSent.toLocaleString()}
                <span className="text-xs font-medium ml-1" style={{ color: '#5A7070' }}>/ {linesTotal.toLocaleString()}</span>
              </p>
            </div>
            <div className="flex-1 px-5 py-4">
              <p className="text-[9px] font-semibold tracking-widest uppercase" style={{ color: '#5A7070' }}>
                Extrusion Rate
              </p>
              <p className="text-2xl font-bold mt-1" style={{ color: '#2D3333' }}>
                {extrusionRate}
                <span className="text-xs font-medium ml-1" style={{ color: '#5A7070' }}>M221</span>
              </p>
            </div>
          </div>
        </div>

        {/* Flow Adjustment slider */}
        <div className="px-7 py-4 shrink-0">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold tracking-widest uppercase" style={{ color: '#8B9090' }}>
              Flow Adjustment
            </span>
            <span className="text-xs font-semibold" style={{ color: '#2D3333' }}>{extrusionRate}%</span>
          </div>
          <ExtrusionSlider currentRate={extrusionRate} />
          <div className="flex justify-between mt-1">
            <span className="text-[10px]" style={{ color: '#A0A8A8' }}>50%</span>
            <span className="text-[10px]" style={{ color: '#A0A8A8' }}>150%</span>
          </div>
        </div>

        {/* Pause / Stop buttons */}
        <div className="flex gap-4 px-7 pb-4 shrink-0">
          {status !== 'paused' && (
            <button
              onClick={handlePause}
              className="flex-1 flex items-center justify-center gap-2 py-4 rounded-2xl text-white font-semibold text-sm tracking-wide transition-all active:scale-95"
              style={{ backgroundColor: '#C07060' }}
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M6.75 5.25a.75.75 0 0 1 .75-.75H9a.75.75 0 0 1 .75.75v13.5a.75.75 0 0 1-.75.75H7.5a.75.75 0 0 1-.75-.75V5.25Zm7.5 0A.75.75 0 0 1 15 4.5h1.5a.75.75 0 0 1 .75.75v13.5a.75.75 0 0 1-.75.75H15a.75.75 0 0 1-.75-.75V5.25Z" clipRule="evenodd" />
              </svg>
              PAUSE
            </button>
          )}
          {status === 'paused' && (
            <button
              onClick={handleResume}
              className="flex-1 flex items-center justify-center gap-2 py-4 rounded-2xl text-white font-semibold text-sm tracking-wide transition-all active:scale-95"
              style={{ backgroundColor: '#1A8B8D' }}
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M4.5 5.653c0-1.427 1.529-2.33 2.779-1.643l11.54 6.347c1.295.712 1.295 2.573 0 3.286L7.28 19.99c-1.25.687-2.779-.217-2.779-1.643V5.653Z" clipRule="evenodd" />
              </svg>
              RESUME
            </button>
          )}
          <button
            onClick={handleStop}
            className="flex-1 flex items-center justify-center gap-2 py-4 rounded-2xl text-white font-semibold text-sm tracking-wide transition-all active:scale-95"
            style={{ backgroundColor: '#9B4A3A' }}
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
              <path fillRule="evenodd" d="M4.5 7.5a3 3 0 0 1 3-3h9a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3h-9a3 3 0 0 1-3-3v-9Z" clipRule="evenodd" />
            </svg>
            STOP
          </button>
        </div>

      </div>

      {(status === 'stopped' || status === 'completed') && (
        <PrintOverlay status={status} onResume={handleResume} onRestart={onRestart} onBack={onBack} />
      )}
    </div>
  )
}
