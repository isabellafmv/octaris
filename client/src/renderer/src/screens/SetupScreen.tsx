import { useState } from 'react'
import type { SyringeMode, UploadResult } from '../types'
import { PortSelector } from '../components/PortSelector'
import { SyringeSelector } from '../components/SyringeSelector'
import { JogPanel } from '../components/JogPanel'
import { STLUpload } from '../components/STLUpload'
import { StartPrintButton } from '../components/StartPrintButton'

interface SetupScreenProps {
  printerConnected: boolean
  onConnect: (port: string) => void
  onDisconnect: () => void
  onStartPrint: () => void
}

export function SetupScreen({
  printerConnected,
  onConnect,
  onDisconnect,
  onStartPrint
}: SetupScreenProps) {
  const [syringeMode, setSyringeMode] = useState<SyringeMode>('left')
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null)
  const [slicing, setSlicing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canStartPrint = printerConnected && uploadResult !== null && !slicing

  return (
    <div className="flex flex-col gap-6 p-6 max-w-4xl mx-auto">
      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">
          {error}
          <button className="float-right font-bold" onClick={() => setError(null)}>
            &times;
          </button>
        </div>
      )}

      <section>
        <h2 className="text-lg font-semibold mb-3">Connection</h2>
        <PortSelector
          connected={printerConnected}
          onConnect={onConnect}
          onDisconnect={onDisconnect}
          onError={setError}
        />
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Syringe Configuration</h2>
        <SyringeSelector selected={syringeMode} onSelect={setSyringeMode} />
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Axis Jog</h2>
        <JogPanel syringeMode={syringeMode} disabled={!printerConnected} />
      </section>

      <section>
        <h2 className="text-lg font-semibold mb-3">Print File</h2>
        <STLUpload
          syringeMode={syringeMode}
          slicing={slicing}
          onSlicingChange={setSlicing}
          onResult={setUploadResult}
          onError={setError}
        />
      </section>

      <StartPrintButton disabled={!canStartPrint} onClick={onStartPrint} />
    </div>
  )
}
