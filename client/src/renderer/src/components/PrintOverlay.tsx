import type { PrintStatus } from '../types'

interface PrintOverlayProps {
  status: PrintStatus
  onResume: () => void
  onRestart: () => void
  onBack: () => void
}

export function PrintOverlay({ status, onResume, onRestart, onBack }: PrintOverlayProps) {
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

        <div className="flex flex-col gap-2.5" style={{ minWidth: '240px' }}>
          {!isCompleted && (
            <button
              onClick={onResume}
              className="flex items-center justify-center gap-2 w-full py-3.5 rounded-2xl text-white font-semibold text-sm tracking-wide transition-all active:scale-[0.97]"
              style={{ backgroundColor: '#1A8B8D', boxShadow: '0 4px 14px rgba(26,139,141,0.35)' }}
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M4.5 5.653c0-1.427 1.529-2.33 2.779-1.643l11.54 6.347c1.295.712 1.295 2.573 0 3.286L7.28 19.99c-1.25.687-2.779-.217-2.779-1.643V5.653Z" clipRule="evenodd" />
              </svg>
              Resume Print
            </button>
          )}
          <button
            onClick={onRestart}
            className="flex items-center justify-center gap-2 w-full py-3.5 rounded-2xl font-semibold text-sm tracking-wide transition-all active:scale-[0.97]"
            style={
              isCompleted
                ? { backgroundColor: '#1A8B8D', color: 'white', boxShadow: '0 4px 14px rgba(26,139,141,0.35)' }
                : { backgroundColor: 'transparent', color: '#1A8B8D', border: '1.5px solid #1A8B8D' }
            }
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-4 h-4">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.992 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182" />
            </svg>
            Restart
          </button>
          <button
            onClick={onBack}
            className="flex items-center justify-center gap-2 w-full py-3.5 rounded-2xl font-semibold text-sm tracking-wide transition-all active:scale-[0.97]"
            style={{ backgroundColor: '#E8E3D8', color: '#5A6060' }}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-4 h-4">
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
            </svg>
            Back to Setup
          </button>
        </div>
      </div>
    </div>
  )
}
