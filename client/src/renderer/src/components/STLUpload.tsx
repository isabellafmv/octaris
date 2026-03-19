import { useCallback, useRef } from 'react'
import { api } from '../api'
import type { SyringeMode, UploadResult } from '../types'

interface STLUploadProps {
  syringeMode: SyringeMode
  slicing: boolean
  onSlicingChange: (v: boolean) => void
  onResult: (r: UploadResult | null) => void
  onError: (msg: string) => void
}

export function STLUpload({ syringeMode, slicing, onSlicingChange, onResult, onError }: STLUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.name.toLowerCase().endsWith('.stl')) {
        onError('Only .stl files are accepted')
        return
      }
      onSlicingChange(true)
      onResult(null)
      try {
        const result = await api.upload(file, syringeMode)
        onResult(result)
      } catch (e) {
        onError(e instanceof Error ? e.message : 'Something went wrong during slicing. Try a different file.')
      } finally {
        onSlicingChange(false)
      }
    },
    [syringeMode, onSlicingChange, onResult, onError]
  )

  return (
    <div className="flex items-center gap-4">
      <input
        ref={inputRef}
        type="file"
        accept=".stl"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) handleFile(file)
        }}
      />
      <button
        onClick={() => inputRef.current?.click()}
        disabled={slicing}
        className="min-h-[48px] px-6 py-3 rounded-lg bg-blue-600 text-white text-base font-semibold active:scale-95 transition-transform disabled:opacity-50"
      >
        {slicing ? 'Slicing...' : 'Upload STL'}
      </button>
    </div>
  )
}
