import { useCallback, useState } from 'react'
import { api } from '../../api'
import { sliceOptions, usePrintSettings } from '../../stores/printSettings'
import type { UploadResult } from '../../types'

export type UploadMode = 'stl' | 'gcode'

export interface Upload {
  mode: UploadMode
  stlFile: File | null
  gcodeFile: File | null
  // True while slicing an STL or processing a G-code file
  slicing: boolean
  result: UploadResult | null
  // G-code only: post-process the file, or send it as uploaded
  needsChanges: boolean
  canSlice: boolean
  changeMode: (mode: UploadMode) => void
  selectStl: (file: File) => void
  slice: () => Promise<void>
  uploadGcode: (file: File) => Promise<void>
  reprocessGcode: () => Promise<void>
  setNeedsChanges: (needsChanges: boolean) => void
}

// File selection, slicing and the processed result for the Setup screen.
// Print settings are read when slicing starts, not subscribed to.
export function useUpload(setError: (msg: string | null) => void): Upload {
  const [mode, setMode] = useState<UploadMode>('stl')
  const [stlFile, setStlFile] = useState<File | null>(null)
  const [gcodeFile, setGcodeFile] = useState<File | null>(null)
  const [slicing, setSlicing] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [needsChanges, setNeedsChangesState] = useState(false)

  const changeMode = (next: UploadMode): void => {
    setMode(next)
    setResult(null)
    setError(null)
    setStlFile(null)
    setGcodeFile(null)
  }

  const selectStl = (file: File): void => {
    setStlFile(file)
    setResult(null)
  }

  const slice = useCallback(async () => {
    if (!stlFile) return
    setSlicing(true)
    setResult(null)
    setError(null)
    try {
      const settings = usePrintSettings.getState()
      setResult(await api.upload(stlFile, settings.syringeMode, sliceOptions(settings)))
    } catch (e) {
      setError(
        e instanceof Error ? e.message : 'Slicing failed. Check that CuraEngine is installed.'
      )
    } finally {
      setSlicing(false)
    }
  }, [stlFile, setError])

  const sendGcode = useCallback(
    async (file: File, changes: boolean) => {
      setGcodeFile(file)
      setResult(null)
      setError(null)
      setSlicing(true)
      try {
        setResult(await api.uploadGcode(file, usePrintSettings.getState().syringeMode, changes))
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Processing failed.')
      } finally {
        setSlicing(false)
      }
    },
    [setError]
  )

  const uploadGcode = useCallback(
    (file: File) => sendGcode(file, needsChanges),
    [sendGcode, needsChanges]
  )

  const reprocessGcode = useCallback(async () => {
    if (!gcodeFile) return
    await uploadGcode(gcodeFile)
  }, [gcodeFile, uploadGcode])

  // A file already picked is loaded again the new way
  const setNeedsChanges = (next: boolean): void => {
    setNeedsChangesState(next)
    if (gcodeFile && !slicing) void sendGcode(gcodeFile, next)
  }

  return {
    mode,
    stlFile,
    gcodeFile,
    slicing,
    result,
    needsChanges,
    canSlice: stlFile !== null && !slicing,
    changeMode,
    selectStl,
    slice,
    uploadGcode,
    reprocessGcode,
    setNeedsChanges
  }
}
