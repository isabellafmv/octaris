import { useCallback, useRef, useState } from 'react'
import { SetupScreen } from './screens/SetupScreen'
import { PrintScreen } from './screens/PrintScreen'
import { TakeOverScreen } from './screens/TakeOverScreen'
import { useWebSocket } from './hooks/useWebSocket'
import { api } from './api'

type Screen = 'setup' | 'print' | 'takeover'

const screenTitles: Record<Screen, string> = {
  setup: 'Setup',
  print: 'Monitoring',
  takeover: 'Manual Control',
}

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

function GearIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="w-5 h-5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
    </svg>
  )
}

function OctarisLogo() {
  return (
    <svg viewBox="0 0 24 24" fill="white" className="w-4 h-4">
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5" />
    </svg>
  )
}

function App(): React.JSX.Element {
  const [screen, setScreen] = useState<Screen>('setup')
  const [connectedPort, setConnectedPort] = useState<string | null>(null)
  const [filename, setFilename] = useState<string | null>(null)
  const previousScreen = useRef<Screen>('setup')
  const ws = useWebSocket()

  const printerConnected = connectedPort !== null

  const handleConnect = useCallback((port: string) => {
    setConnectedPort(port)
  }, [])

  const handleDisconnect = useCallback(() => {
    setConnectedPort(null)
  }, [])

  const handleStartPrint = useCallback(async () => {
    try {
      await api.printStart()
    } catch (e) {
      console.error('Failed to start print:', e)
    }
    setScreen('print')
  }, [])

  const handleBack = useCallback(() => {
    setScreen('setup')
    setFilename(null)
  }, [])

  const handleTakeOver = useCallback(() => {
    previousScreen.current = screen === 'takeover' ? 'setup' : screen
    setScreen('takeover')
  }, [screen])

  const handleTakeOverBack = useCallback(() => {
    setScreen(previousScreen.current)
  }, [])

  const navItems = [
    { id: 'setup' as Screen, icon: <SetupIcon />, label: 'SETUP' },
    { id: 'print' as Screen, icon: <MonitorIcon />, label: 'MONITOR' },
    { id: 'takeover' as Screen, icon: <LibraryIcon />, label: 'LOGS' },
  ]

  const handleNavClick = (id: Screen) => {
    if (id === 'setup') {
      if (screen === 'takeover') handleTakeOverBack()
      else setScreen('setup')
    } else if (id === 'takeover') {
      handleTakeOver()
    } else if (id === 'print' && screen === 'print') {
      // already there
    }
  }

  return (
    <div className="dot-grid h-screen flex flex-col select-none">
      {/* ── Title bar ── */}
      <div className="drag-region flex items-center gap-3 px-5 py-3 shrink-0">
        <div className="no-drag flex items-center gap-2.5">
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
            style={{ backgroundColor: '#1A8B8D' }}
          >
            <OctarisLogo />
          </div>
          <span className="text-white text-sm font-medium tracking-wide opacity-90">
            Octaris: {screenTitles[screen]}
          </span>
        </div>
      </div>

      {/* ── Content row ── */}
      <div className="flex flex-1 gap-3 px-4 pb-4 min-h-0">
        {/* Floating sidebar — hidden when PrintScreen has its own */}
        {screen !== 'print' && (
          <div className="flex flex-col items-center gap-1 py-2 w-14 shrink-0">
            {navItems.map((item) => (
              <div key={item.id} className="flex flex-col items-center gap-0.5 w-full">
                <button
                  onClick={() => handleNavClick(item.id)}
                  className="w-10 h-10 rounded-full flex items-center justify-center transition-all active:scale-90 no-drag"
                  style={
                    screen === item.id
                      ? { backgroundColor: '#1A8B8D', color: 'white' }
                      : { color: '#6B7070' }
                  }
                  title={item.label}
                >
                  {item.icon}
                </button>
                <span
                  className="text-[8px] tracking-widest uppercase font-medium"
                  style={{ color: screen === item.id ? '#1A8B8D' : '#4D5252' }}
                >
                  {item.label}
                </span>
              </div>
            ))}
            <div className="flex-1" />
            <button
              className="w-10 h-10 rounded-full flex items-center justify-center no-drag"
              style={{ color: '#4D5252' }}
              title="Settings"
            >
              <GearIcon />
            </button>
          </div>
        )}

        {/* Main content card */}
        <div
          className="flex-1 rounded-2xl overflow-hidden min-h-0 flex flex-col"
          style={{ backgroundColor: '#F5F1E6' }}
        >
          {screen === 'setup' ? (
            <SetupScreen
              printerConnected={printerConnected}
              onConnect={handleConnect}
              onDisconnect={handleDisconnect}
              onStartPrint={handleStartPrint}
            />
          ) : screen === 'print' ? (
            <PrintScreen
              status={ws.status}
              linesSent={ws.linesSent}
              linesTotal={ws.linesTotal}
              timeRemainingS={ws.timeRemainingS}
              extrusionRate={ws.extrusionRate}
              filename={filename}
              onBack={handleBack}
              onTakeOver={handleTakeOver}
              printerConnected={printerConnected}
            />
          ) : (
            <TakeOverScreen
              printerConnected={printerConnected}
              serialLog={ws.serialLog}
              onClearLog={ws.clearSerialLog}
              onSetLog={ws.setSerialLog}
              onBack={handleTakeOverBack}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default App
