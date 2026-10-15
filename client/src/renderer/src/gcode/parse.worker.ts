// Parses G-code off the main thread: receives the text and the printer's
// nozzles, posts progress messages, then the preview data with its buffers
// transferred, not copied.
import { buffers, groupByCategory, type GcodePreviewData } from './categories'
import { parseGcode, type PreviewNozzle } from './parse'

export type WorkerRequest = { text: string; nozzles: PreviewNozzle[] }

export type WorkerMessage =
  | { type: 'progress'; fraction: number }
  | { type: 'done'; data: GcodePreviewData }
  | { type: 'error'; message: string }

const post = (message: WorkerMessage, transfer: Transferable[] = []): void =>
  self.postMessage(message, { transfer })

self.onmessage = (event: MessageEvent<WorkerRequest>) => {
  try {
    const { text, nozzles } = event.data
    const parsed = parseGcode(text, nozzles, (fraction) => post({ type: 'progress', fraction }))
    const data = groupByCategory(parsed)
    post({ type: 'done', data }, buffers(data))
  } catch (e) {
    post({ type: 'error', message: e instanceof Error ? e.message : String(e) })
  }
}
