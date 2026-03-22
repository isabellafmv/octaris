import type { PrintStatus } from '../types'

interface PrintOverlayProps {
  status: PrintStatus
  onResume: () => void
  onBack: () => void
}

export function PrintOverlay({ status, onResume, onBack }: PrintOverlayProps) {
  const isCompleted = status === 'completed'

  return (
    <div className="fixed inset-0 flex items-center justify-center z-50" style={{ backgroundColor: 'rgba(40,43,43,0.7)' }}>
      <div
        className="rounded-3xl p-8 text-center mx-4"
        style={{ backgroundColor: '#F5F1E6', minWidth: '280px', boxShadow: '0 24px 60px rgba(0,0,0,0.3)' }}
      >
        {/* Icon */}
        <div
          className="w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-5"
          style={{ backgroundColor: isCompleted ? '#D4EAE9' : '#FDEAEA' }}
        >
          {isCompleted ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="#1A8B8D" strokeWidth="2" className="w-8 h-8">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="#B54040" strokeWidth="2" className="w-8 h-8">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 7.5A2.25 2.25 0 0 1 7.5 5.25h9a2.25 2.25 0 0 1 2.25 2.25v9a2.25 2.25 0 0 1-2.25 2.25h-9a2.25 2.25 0 0 1-2.25-2.25v-9Z" />
            </svg>
          )}
        </div>

        <h2 className="text-xl font-bold mb-1" style={{ color: '#2D3333' }}>
          {isCompleted ? 'Print Complete!' : 'Print Stopped'}
        </h2>
        <p className="text-sm mb-7" style={{ color: '#8B9090' }}>
          {isCompleted
            ? 'Your bioprint has finished successfully.'
            : 'The print job was interrupted.'}
        </p>

        <div className="flex gap-3 justify-center">
          {!isCompleted && (
            <button
              onClick={onResume}
              className="flex-1 py-3 rounded-2xl text-white font-semibold text-sm transition-all active:scale-95"
              style={{ backgroundColor: '#1A8B8D' }}
            >
              Resume
            </button>
          )}
          <button
            onClick={onBack}
            className="flex-1 py-3 rounded-2xl font-semibold text-sm transition-all active:scale-95"
            style={{ backgroundColor: '#E8E3D8', color: '#5A6060' }}
          >
            Back to Setup
          </button>
        </div>
      </div>
    </div>
  )
}
