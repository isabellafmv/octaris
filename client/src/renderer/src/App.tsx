import { useCallback, useState } from 'react'
import { SetupScreen } from './screens/SetupScreen'
import { PrintScreen } from './screens/PrintScreen'
import { TakeOverScreen } from './screens/TakeOverScreen'
import { useWebSocket } from './hooks/useWebSocket'
import { useScreenNavigation, type Screen } from './hooks/useScreenNavigation'
import { api } from './api'

const screenTitles: Record<Screen, string> = {
  setup: 'Setup',
  print: 'Monitoring',
  takeover: 'Manual Control'
}

const navItems: { id: Screen; icon: React.JSX.Element; label: string }[] = [
  { id: 'setup', icon: <SetupIcon />, label: 'SETUP' },
  { id: 'print', icon: <MonitorIcon />, label: 'MONITOR' },
  { id: 'takeover', icon: <LibraryIcon />, label: 'LOGS' }
]

function SetupIcon(): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className="w-5 h-5"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75"
      />
    </svg>
  )
}

function MonitorIcon(): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className="w-5 h-5"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"
      />
    </svg>
  )
}

function LibraryIcon(): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className="w-5 h-5"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
      />
    </svg>
  )
}

function OctarisLogo(): React.JSX.Element {
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
  const { screen, navigate, back } = useScreenNavigation()
  const [filename, setFilename] = useState<string | null>(null)
  const ws = useWebSocket()
  const { resetPrintState } = ws

  const printerConnected = ws.printerConnected

  const connectionLabel = !ws.wsConnected
    ? 'Backend unreachable'
    : printerConnected
      ? 'Printer connected'
      : 'Printer disconnected'
  const connectionDot = !ws.wsConnected
    ? 'bg-danger'
    : printerConnected
      ? 'bg-primary'
      : 'bg-warning'

  const [startError, setStartError] = useState<string | null>(null)

  const handleStartPrint = useCallback(async () => {
    setStartError(null)
    try {
      await api.printStart()
      navigate('print')
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to start print'
      setStartError(msg)
    }
  }, [navigate])

  const handleBack = useCallback(() => {
    navigate('setup')
    setFilename(null)
    resetPrintState()
  }, [navigate, resetPrintState])

  const handleRestart = useCallback(async () => {
    setStartError(null)
    try {
      await api.printStart()
      resetPrintState()
    } catch (e) {
      setStartError(e instanceof Error ? e.message : 'Failed to restart print')
    }
  }, [resetPrintState])

  const handleClearStartError = useCallback(() => setStartError(null), [])

  return (
    <div className="dot-grid h-screen flex flex-col select-none">
      {/* ── Title bar ── */}
      <div className="drag-region flex items-center gap-3 px-5 py-3 shrink-0">
        <div className="no-drag flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 bg-primary">
            <OctarisLogo />
          </div>
          <span className="text-white text-sm font-medium tracking-wide opacity-90">
            {screenTitles[screen]}
          </span>
        </div>
        <div className="no-drag flex items-center gap-1.5 ml-auto">
          <div className={`w-1.5 h-1.5 rounded-full ${connectionDot}`} />
          <span className="text-[10px] font-medium tracking-wide opacity-80 text-white">
            {connectionLabel}
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
                  onClick={() => navigate(item.id)}
                  className={`w-10 h-10 rounded-full flex items-center justify-center transition-all active:scale-90 no-drag ${
                    screen === item.id ? 'bg-primary text-white' : 'text-chrome-icon'
                  }`}
                  title={item.label}
                >
                  {item.icon}
                </button>
                <span
                  className={`text-[8px] tracking-widest uppercase font-medium ${
                    screen === item.id ? 'text-primary' : 'text-chrome-label'
                  }`}
                >
                  {item.label}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Main content card */}
        <div className="flex-1 rounded-2xl overflow-hidden min-h-0 flex flex-col bg-surface">
          {screen === 'setup' ? (
            <SetupScreen
              printerConnected={printerConnected}
              port={ws.port}
              calibrated={ws.calibrated}
              printStatus={ws.status}
              onStartPrint={handleStartPrint}
              externalError={startError}
              onClearExternalError={handleClearStartError}
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
              onRestart={handleRestart}
              onTakeOver={() => navigate('takeover')}
              printerConnected={printerConnected}
              printError={ws.lastError}
              stopInfo={ws.stopInfo}
            />
          ) : (
            <TakeOverScreen
              printerConnected={printerConnected}
              printStatus={ws.status}
              onBack={back}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default App
