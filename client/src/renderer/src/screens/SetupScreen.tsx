import { useCallback, useEffect, useState } from 'react'
import type { PrintStatus } from '../types'
import { CalibrationSection } from './setup/CalibrationSection'
import { ConnectionSection } from './setup/ConnectionSection'
import { PrintParametersSection } from './setup/PrintParametersSection'
import { SyringeSelectionSection } from './setup/SyringeSelectionSection'
import { UploadPreview, UploadSection } from './setup/UploadSection'
import { useUpload, type Upload } from './setup/useUpload'

interface SetupScreenProps {
  printerConnected: boolean
  port: string | null
  calibrated: boolean
  printStatus: PrintStatus
  onStartPrint: () => void
  externalError?: string | null
  onClearExternalError?: () => void
}

function nextStepHint(upload: Upload, calibrated: boolean): string {
  if (upload.result)
    return calibrated ? 'Ready to print' : 'Jog to position and set origin to continue'
  if (upload.mode === 'stl')
    return upload.stlFile ? 'Slice the file to continue' : 'Upload an STL to get started'
  return upload.gcodeFile ? 'Processing…' : 'Upload a pre-sliced G-code file'
}

export function SetupScreen({
  printerConnected,
  port,
  calibrated: calibratedFromEvents,
  printStatus,
  onStartPrint,
  externalError,
  onClearExternalError
}: SetupScreenProps): React.JSX.Element {
  const [error, setError] = useState<string | null>(null)
  const [calibrated, setCalibrated] = useState(calibratedFromEvents)
  const upload = useUpload(setError)

  // Merge external errors (from failed print start) into local error state
  useEffect(() => {
    if (externalError) {
      setError(externalError)
      onClearExternalError?.()
    }
  }, [externalError, onClearExternalError])

  // Calibration state is pushed live over the websocket (snapshot on connect,
  // then calibration events), not polled.
  useEffect(() => {
    setCalibrated(calibratedFromEvents)
  }, [calibratedFromEvents])

  const handleCalibrated = useCallback(() => setCalibrated(true), [])

  const canStartPrint = upload.result !== null && !upload.slicing && calibrated

  return (
    <div className="flex flex-col h-full overflow-hidden text-text">
      <ConnectionSection printerConnected={printerConnected} port={port} onError={setError} />

      {error && (
        <div className="mx-8 mb-3 px-4 py-2 rounded-lg text-sm flex items-start justify-between gap-3 bg-danger-muted text-danger">
          <pre className="whitespace-pre-wrap break-all font-sans overflow-y-auto max-h-40 flex-1">
            {error}
          </pre>
          <button
            onClick={() => setError(null)}
            className="font-bold text-lg leading-none shrink-0"
          >
            &times;
          </button>
        </div>
      )}

      {/* ── Two-column body ── */}
      <div className="flex flex-1 min-h-0 px-8 pb-6 gap-6">
        {/* Left column — syringe viz + parameters + upload */}
        <div className="flex flex-col gap-4 w-[48%]">
          <SyringeSelectionSection />
          <PrintParametersSection />
          <UploadSection upload={upload} onError={setError} />
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-4 flex-1 min-h-0 overflow-y-auto">
          <CalibrationSection
            printerConnected={printerConnected}
            printStatus={printStatus}
            calibrated={calibrated}
            hasUpload={upload.result !== null}
            onCalibrated={handleCalibrated}
            onError={setError}
          />

          <UploadPreview upload={upload} />

          <div className="flex-1" />

          {/* Proceed CTA */}
          <button
            onClick={onStartPrint}
            disabled={!canStartPrint}
            className="w-full flex items-center justify-center gap-2 py-3.5 rounded-2xl text-white font-semibold text-base transition-all active:scale-[0.98] disabled:opacity-40 shrink-0 bg-primary"
          >
            <span>Proceed to Preview</span>
            <span className="text-lg">→</span>
          </button>
          <p className="text-center text-xs -mt-2 text-text-subtle">
            {nextStepHint(upload, calibrated)}
          </p>
        </div>
      </div>
    </div>
  )
}
