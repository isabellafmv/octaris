import { contextBridge } from 'electron'
import { electronAPI } from '@electron-toolkit/preload'

// Custom APIs for renderer
const api = {}

// Per-launch auth token passed by the main process via
// webPreferences.additionalArguments, e.g. --octaris-token=<hex>.
const tokenArg = process.argv.find((arg) => arg.startsWith('--octaris-token='))
const token = tokenArg ? tokenArg.slice('--octaris-token='.length) : null
const octaris = { token }

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
    contextBridge.exposeInMainWorld('octaris', octaris)
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
  // @ts-ignore (define in dts)
  window.octaris = octaris
}
