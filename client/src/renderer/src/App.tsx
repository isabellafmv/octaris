import { useCallback, useRef, useState } from 'react'
import { StatusBar } from './components/StatusBar'
import { SetupScreen } from './screens/SetupScreen'
import { PrintScreen } from './screens/PrintScreen'
import { TakeOverScreen } from './screens/TakeOverScreen'
import { useWebSocket } from './hooks/useWebSocket'
import { api } from './api'

type Screen = 'setup' | 'print' | 'takeover'

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
      setScreen('print')
    } catch (e) {
      console.error('Failed to start print:', e)
    }
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

  return (
    <div className="flex flex-col h-screen bg-gray-50 text-gray-900">
      <StatusBar
        printerConnected={printerConnected}
        port={connectedPort}
        status={ws.status}
        onTakeOver={handleTakeOver}
        showTakeOver={screen !== 'takeover'}
      />
      <main className="flex-1 overflow-auto">
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
      </main>
    </div>
  )
}

export default App
