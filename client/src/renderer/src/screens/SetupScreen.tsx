import { useCallback, useState } from 'react'
import type { SyringeMode, UploadResult } from '../types'
import { api } from '../api'
import { PortSelector } from '../components/PortSelector'
import { JogPanel } from '../components/JogPanel'
import { STLUpload } from '../components/STLUpload'
import { STLPreview } from '../components/STLPreview'
import { GcodePreview } from '../components/GcodePreview'
import { SyringeModuleViz } from '../components/SyringeSelector'

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
  onStartPrint,
}: SetupScreenProps) {
  const [syringeMode, setSyringeMode] = useState<SyringeMode>('left')
  const [stlFile, setStlFile] = useState<File | null>(null)
  const [slicing, setSlicing] = useState(false)
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleFile = (f: File): void => {
    setStlFile(f)
    setUploadResult(null)
  }

  const handleSlice = useCallback(async () => {
    if (!stlFile) return
    setSlicing(true)
    setUploadResult(null)
    setError(null)
    try {
      const result = await api.upload(stlFile, syringeMode)
      setUploadResult(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Slicing failed. Check that CuraEngine is installed.')
    } finally {
      setSlicing(false)
    }
  }, [stlFile, syringeMode])

  const canSlice = stlFile !== null && !slicing
  const canStartPrint = uploadResult !== null && !slicing

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ color: '#2D3333' }}>
      {/* ── Header ── */}
      <div className="flex items-start justify-between px-8 pt-7 pb-4 shrink-0">
        <div>
          <h1 className="text-3xl font-bold tracking-tight" style={{ color: '#1A8B8D' }}>
            Setup &amp; Configuration
          </h1>
          <p className="text-xs tracking-widest uppercase mt-1" style={{ color: '#8B9090' }}>
            Bioprinting System // Octaris
          </p>
        </div>
        <div className="mt-1">
          <PortSelector
            connected={printerConnected}
            onConnect={onConnect}
            onDisconnect={onDisconnect}
            onError={setError}
          />
        </div>
      </div>

      {error && (
        <div
          className="mx-8 mb-3 px-4 py-2 rounded-lg text-sm flex items-center justify-between"
          style={{ backgroundColor: '#FDEAEA', color: '#B54040' }}
        >
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-3 font-bold text-lg leading-none">&times;</button>
        </div>
      )}

      {/* ── Two-column body ── */}
      <div className="flex flex-1 min-h-0 px-8 pb-6 gap-6">
        {/* Left column — syringe viz + STL upload */}
        <div className="flex flex-col gap-4 w-[48%]">
          <div
            className="flex-1 rounded-2xl flex flex-col items-center justify-center p-6 min-h-0"
            style={{ backgroundColor: '#EDE9DC' }}
          >
            <SyringeModuleViz selected={syringeMode} onSelect={setSyringeMode} />
          </div>
          <STLUpload file={stlFile} onFile={handleFile} onError={setError} />
        </div>

        {/* Right column — nav + STL preview + gcode preview + CTA */}
        <div className="flex flex-col gap-4 flex-1 min-h-0 overflow-y-auto">
          {/* Manual Navigation — shrinks when file loaded */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold tracking-widest uppercase" style={{ color: '#8B9090' }}>
                Manual Navigation
              </span>
              <button
                className="text-xs underline disabled:opacity-40"
                style={{ color: '#1A8B8D' }}
                disabled={!printerConnected}
                onClick={() => api.jog('X', 0)}
              >
                Reset Origin
              </button>
            </div>
            <div className={stlFile ? 'scale-75 origin-top-left' : ''}>
              <JogPanel syringeMode={syringeMode} disabled={!printerConnected} />
            </div>
          </div>

          {/* STL 3D Preview */}
          {stlFile && (
            <div className="rounded-2xl overflow-hidden shrink-0" style={{ height: '200px', backgroundColor: '#EDE9DC' }}>
              <STLPreview file={stlFile} />
            </div>
          )}

          {/* Slice Now button */}
          {stlFile && !uploadResult && (
            <button
              onClick={handleSlice}
              disabled={!canSlice}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-2xl font-semibold text-sm transition-all active:scale-[0.98] disabled:opacity-40"
              style={{ backgroundColor: '#EDE9DC', color: '#1A8B8D', border: '1.5px solid #1A8B8D' }}
            >
              {slicing ? (
                <>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-4 h-4 animate-spin">
                    <path strokeLinecap="round" d="M12 3a9 9 0 1 0 9 9" />
                  </svg>
                  Slicing…
                </>
              ) : (
                <>
                  {/* <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-4 h-4">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 0 1 0 1.972l-11.54 6.347a1.125 1.125 0 0 1-1.667-.986V5.653Z" />
                  </svg> */}
                  Click to Slice
                </>
              )}
            </button>
          )}

          {/* G-code preview */}
          {uploadResult && <GcodePreview result={uploadResult} />}

          {/* Re-slice button after result */}
          {uploadResult && (
            <button
              onClick={handleSlice}
              disabled={!canSlice}
              className="w-full py-2 rounded-xl text-xs font-medium transition-opacity active:opacity-60 disabled:opacity-40"
              style={{ backgroundColor: '#EDE9DC', color: '#8B9090' }}
            >
              Re-slice
            </button>
          )}

          <div className="flex-1" />

          {/* Proceed CTA */}
          <button
            onClick={onStartPrint}
            disabled={!canStartPrint}
            className="w-full flex items-center justify-center gap-2 py-3.5 rounded-2xl text-white font-semibold text-base transition-all active:scale-[0.98] disabled:opacity-40 shrink-0"
            style={{ backgroundColor: '#1A8B8D' }}
          >
            <span>Proceed to Preview</span>
            <span className="text-lg">→</span>
          </button>
          <p className="text-center text-xs -mt-2" style={{ color: '#A0A8A8' }}>
            {uploadResult ? 'Ready to print' : stlFile ? 'Slice the file to continue' : 'Upload an STL to get started'}
          </p>
        </div>
      </div>
    </div>
  )
}
