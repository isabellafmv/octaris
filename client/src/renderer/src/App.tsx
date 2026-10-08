import { useCallback, useState } from 'react'
import { SetupScreen } from './screens/SetupScreen'
import { PrintScreen } from './screens/PrintScreen'
import { TakeOverScreen } from './screens/TakeOverScreen'
import { TemperatureScreen } from './screens/TemperatureScreen'
import { useWebSocket } from './hooks/useWebSocket'
import { useScreenNavigation, type Screen } from './hooks/useScreenNavigation'
import { Sidebar } from './components/Sidebar'
import { api } from './api'

const screenTitles: Record<Screen, string> = {
  setup: 'Setup',
  print: 'Monitoring',
  temperature: 'Temperature',
  takeover: 'Manual Control'
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

  // Leaving Monitor for Setup clears the finished print, like the overlay's Back button
  const handleNavigate = useCallback(
    (to: Screen) => (screen === 'print' && to === 'setup' ? handleBack() : navigate(to)),
    [screen, handleBack, navigate]
  )

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
        <Sidebar screen={screen} onNavigate={handleNavigate} />

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
              printerConnected={printerConnected}
              printError={ws.lastError}
              stopInfo={ws.stopInfo}
            />
          ) : screen === 'temperature' ? (
            <TemperatureScreen printerConnected={printerConnected} lastError={ws.lastError} />
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
