import { GcodePreview } from '../../components/GcodePreview'
import { GcodeUpload } from '../../components/GcodeUpload'
import { STLPreview } from '../../components/STLPreview'
import { STLUpload } from '../../components/STLUpload'
import { usePrintSettings } from '../../stores/printSettings'
import type { Upload, UploadMode } from './useUpload'

const MODES: UploadMode[] = ['stl', 'gcode']

const CHANGE_OPTIONS = [
  { needsChanges: false, label: "Doesn't need changes" },
  { needsChanges: true, label: 'Needs changes' }
]

const segmentClass = (active: boolean): string =>
  `flex-1 py-1.5 rounded-lg text-xs font-semibold transition-all ${
    active ? 'bg-white text-primary shadow-[0_1px_3px_rgba(0,0,0,0.08)]' : 'text-text-muted'
  }`

// Left column: STL / G-code toggle and the file picker
export function UploadSection({
  upload,
  onError
}: {
  upload: Upload
  onError: (msg: string) => void
}): React.JSX.Element {
  const dualMode = usePrintSettings((s) => s.syringeMode === 'both')

  return (
    <>
      <div className="flex rounded-xl p-1 gap-1 bg-surface-card">
        {MODES.map((mode) => (
          <button
            key={mode}
            onClick={() => upload.changeMode(mode)}
            className={segmentClass(upload.mode === mode)}
          >
            {mode === 'stl' ? 'STL' : 'G-Code File'}
          </button>
        ))}
      </div>

      {upload.mode === 'stl' ? (
        <STLUpload
          file={upload.stlFile}
          onFile={upload.selectStl}
          onError={onError}
          dualMode={dualMode}
        />
      ) : (
        <>
          <div className="flex flex-col gap-1">
            <div className="flex rounded-xl p-1 gap-1 bg-surface-card">
              {CHANGE_OPTIONS.map(({ needsChanges, label }) => (
                <button
                  key={label}
                  onClick={() => upload.setNeedsChanges(needsChanges)}
                  disabled={upload.slicing}
                  aria-pressed={upload.needsChanges === needsChanges}
                  className={segmentClass(upload.needsChanges === needsChanges)}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="text-[9px] text-text-subtle px-1">
              {upload.needsChanges
                ? 'Converted for the printer: E to the plungers, pressurize and retracts added, made relative (G91).'
                : 'Sent exactly as uploaded. Only checked; anything unusual is shown as a warning.'}
            </span>
          </div>
          <GcodeUpload
            file={upload.gcodeFile}
            loading={upload.slicing}
            needsChanges={upload.needsChanges}
            onFile={upload.uploadGcode}
            onError={onError}
          />
        </>
      )}
    </>
  )
}

// Right column: model preview and slice button before slicing, G-code preview after
export function UploadPreview({ upload }: { upload: Upload }): React.JSX.Element {
  const { mode, stlFile, gcodeFile, slicing, result, canSlice } = upload
  const awaitingSlice = mode === 'stl' && stlFile !== null && !result

  return (
    <>
      {awaitingSlice && (
        <div className="rounded-2xl overflow-hidden shrink-0 h-[180px] bg-surface-card">
          <STLPreview file={stlFile} />
        </div>
      )}

      {awaitingSlice && (
        <button
          onClick={upload.slice}
          disabled={!canSlice}
          className="w-full flex items-center justify-center gap-2 py-3 rounded-2xl font-semibold text-sm transition-all active:scale-[0.98] disabled:opacity-40 bg-surface-card text-primary border-[1.5px] border-primary"
        >
          {slicing ? (
            <>
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="w-4 h-4 animate-spin"
              >
                <path strokeLinecap="round" d="M12 3a9 9 0 1 0 9 9" />
              </svg>
              Slicing…
            </>
          ) : (
            'Click to Slice'
          )}
        </button>
      )}

      {result && result.warnings && result.warnings.length > 0 && (
        <div className="rounded-2xl px-4 py-3 text-xs bg-[#F6E4DC] text-warning">
          <p className="font-semibold mb-1">
            {result.warnings.length === 1 ? 'Warning' : `${result.warnings.length} warnings`}
          </p>
          <ul className="list-disc pl-4 flex flex-col gap-0.5">
            {result.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {result && <GcodePreview result={result} />}

      {result && (
        <button
          onClick={mode === 'stl' ? upload.slice : upload.reprocessGcode}
          disabled={mode === 'stl' ? !canSlice : !gcodeFile || slicing}
          className="w-full py-2 rounded-xl text-xs font-medium transition-opacity active:opacity-60 disabled:opacity-40 bg-surface-card text-text-muted"
        >
          {mode === 'stl' ? 'Re-slice' : upload.needsChanges ? 'Re-process' : 'Reload'}
        </button>
      )}
    </>
  )
}
