import { useRef } from 'react'

interface STLUploadProps {
  file: File | null
  onFile: (f: File) => void
  onError: (msg: string) => void
}

export function STLUpload({ file, onFile, onError }: STLUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".stl"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (!f) return
          if (!f.name.toLowerCase().endsWith('.stl')) {
            onError('Only .stl files are accepted')
            return
          }
          onFile(f)
          // reset so same file can be re-selected
          e.target.value = ''
        }}
      />
      <button
        onClick={() => inputRef.current?.click()}
        className="w-full flex items-center gap-4 px-5 py-4 rounded-2xl bg-white transition-all active:scale-[0.98] group"
        style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.07)' }}
      >
        <div
          className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
          style={{ backgroundColor: '#EDE9DC' }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="#1A8B8D" strokeWidth="1.6" className="w-5 h-5">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
            />
          </svg>
        </div>
        <div className="flex-1 text-left">
          <p className="font-semibold text-sm" style={{ color: '#2D3333' }}>
            {file ? file.name : 'Upload Your STL'}
          </p>
          <p className="text-xs mt-0.5" style={{ color: '#8B9090' }}>
            {file ? 'Click to replace file' : 'Select a .stl file to preview & slice'}
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
