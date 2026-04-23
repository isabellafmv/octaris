import { useRef } from 'react'

interface GcodeUploadProps {
  file: File | null
  loading: boolean
  onFile: (f: File) => void
  onError: (msg: string) => void
}

export function GcodeUpload({ file, loading, onFile, onError }: GcodeUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".gcode,.gco"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (!f) return
          if (!f.name.toLowerCase().match(/\.(gcode|gco)$/)) {
            onError('Only .gcode files are accepted')
            return
          }
          onFile(f)
          e.target.value = ''
        }}
      />
      <button
        onClick={() => inputRef.current?.click()}
        disabled={loading}
        className="w-full flex items-center gap-4 px-5 py-4 rounded-2xl bg-white transition-all active:scale-[0.98] group disabled:opacity-60"
        style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.07)' }}
      >
        <div
          className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
          style={{ backgroundColor: '#EDE9DC' }}
        >
          {loading ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="#1A8B8D" strokeWidth="1.6" className="w-5 h-5 animate-spin">
              <path strokeLinecap="round" d="M12 3a9 9 0 1 0 9 9" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="#1A8B8D" strokeWidth="1.6" className="w-5 h-5">
              <path strokeLinecap="round" strokeLinejoin="round"
                d="M6.75 7.5l3 2.25-3 2.25m4.5 0h3m-9 8.25h13.5A2.25 2.25 0 0 0 21 18V6a2.25 2.25 0 0 0-2.25-2.25H5.25A2.25 2.25 0 0 0 3 6v12a2.25 2.25 0 0 0 2.25 2.25Z" />
            </svg>
          )}
        </div>
        <div className="flex-1 text-left">
          <p className="font-semibold text-sm" style={{ color: '#2D3333' }}>
            {loading ? 'Processing…' : file ? file.name : 'Upload G-Code'}
          </p>
          <p className="text-xs mt-0.5" style={{ color: '#8B9090' }}>
            {loading ? 'Applying extrusion substitution' : file ? 'Click to replace file' : 'Select a pre-sliced .gcode file'}
          </p>
        </div>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="#B8B3A8"
          strokeWidth="2"
          className="w-4 h-4 shrink-0 group-hover:translate-x-0.5 transition-transform"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
        </svg>
      </button>
    </>
  )
}
