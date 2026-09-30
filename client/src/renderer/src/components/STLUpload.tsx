import { useRef } from 'react'

interface STLUploadProps {
  file: File | null
  onFile: (f: File) => void
  onError: (msg: string) => void
  /** When true, shows hint that 3MF is needed for multi-material. */
  dualMode?: boolean
}

const ACCEPTED_EXTENSIONS = ['.stl', '.3mf']

export function STLUpload({ file, onFile, onError, dualMode }: STLUploadProps): React.JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null)

  const is3mf = file?.name.toLowerCase().endsWith('.3mf')

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".stl,.3mf"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (!f) return
          const ext = f.name.toLowerCase()
          if (!ACCEPTED_EXTENSIONS.some((e) => ext.endsWith(e))) {
            onError('Only .stl and .3mf files are accepted')
            return
          }
          onFile(f)
          e.target.value = ''
        }}
      />
      <button
        onClick={() => inputRef.current?.click()}
        className="w-full flex items-center gap-4 px-5 py-4 rounded-2xl bg-white transition-all active:scale-[0.98] group shadow-[0_1px_4px_rgba(0,0,0,0.07)]"
      >
        <div
          className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${
            is3mf ? 'bg-primary-muted' : 'bg-surface-card'
          }`}
        >
          <svg viewBox="0 0 24 24" fill="none" strokeWidth="1.6" className="w-5 h-5 stroke-primary">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
            />
          </svg>
        </div>
        <div className="flex-1 text-left">
          <p className="font-semibold text-sm text-text">
            {file ? file.name : 'Upload Model'}
          </p>
          <p className="text-xs mt-0.5 text-text-muted">
            {file ? 'Click to replace file' : 'Select a .stl or .3mf file'}
          </p>
        </div>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          strokeWidth="2"
          className="w-4 h-4 shrink-0 group-hover:translate-x-0.5 transition-transform stroke-[#B8B3A8]"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
        </svg>
      </button>
      {dualMode && !file && (
        <p className="text-[9px] mt-1.5 text-text-tinted">
          Upload a .3mf file for multi-material printing (different material per syringe).
          A single .stl will extrude from both syringes simultaneously.
        </p>
      )}
      {dualMode && file && !is3mf && (
        <p className="text-[9px] mt-1.5 text-text-muted">
          Both syringes will extrude the same path. Use a .3mf file to assign
          different regions to each syringe.
        </p>
      )}
    </>
  )
}
