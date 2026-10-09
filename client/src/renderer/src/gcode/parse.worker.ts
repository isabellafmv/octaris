// Parses G-code off the main thread: receives the text, posts progress
// messages, then the preview data with its buffers transferred, not copied.
import { CATEGORIES, groupByCategory, type GcodePreviewData } from './categories'
import { parseGcode } from './parse'

export type WorkerMessage =
  | { type: 'progress'; fraction: number }
  | { type: 'done'; data: GcodePreviewData }
  | { type: 'error'; message: string }

const post = (message: WorkerMessage, transfer: Transferable[] = []): void =>
  self.postMessage(message, { transfer })

self.onmessage = (event: MessageEvent<string>) => {
  try {
    const parsed = parseGcode(event.data, (fraction) => post({ type: 'progress', fraction }))
    const data = groupByCategory(parsed)
    const transfer = CATEGORIES.flatMap((c) => [
      data.categories[c].positions.buffer,
      data.categories[c].layerStart.buffer
    ])
    post({ type: 'done', data }, transfer)
  } catch (e) {
    post({ type: 'error', message: e instanceof Error ? e.message : String(e) })
  }
}
