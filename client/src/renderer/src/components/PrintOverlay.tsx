import type { PrintStatus } from '../types'

interface PrintOverlayProps {
  status: PrintStatus
  onResume: () => void
  onBack: () => void
}

export function PrintOverlay({ status, onResume, onBack }: PrintOverlayProps) {
  const isCompleted = status === 'completed'

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-2xl p-8 text-center max-w-sm mx-4">
        <h2 className="text-2xl font-bold mb-6">
          {isCompleted ? 'Print complete!' : 'Print stopped.'}
        </h2>
        <div className="flex gap-3 justify-center">
          {!isCompleted && (
            <button
              onClick={onResume}
              className="min-h-[48px] px-6 py-3 rounded-lg bg-green-600 text-white text-base font-semibold active:scale-95 transition-transform"
            >
              Resume
            </button>
          )}
          <button
            onClick={onBack}
            className="min-h-[48px] px-6 py-3 rounded-lg bg-gray-600 text-white text-base font-semibold active:scale-95 transition-transform"
          >
            Back
          </button>
        </div>
      </div>
    </div>
  )
}
