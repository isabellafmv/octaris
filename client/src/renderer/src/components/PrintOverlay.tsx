import type { PrintStatus, StopInfo } from '../types'

interface PrintOverlayProps {
  status: PrintStatus
  stopInfo: StopInfo
  onResume: () => void
  onRestart: () => void
  onBack: () => void
}

function stoppedMessage(stopInfo: StopInfo): string {
  if (stopInfo === null) return 'Checking where the print stopped…'
  if (stopInfo.resumable) return 'The print can continue from where it stopped.'
  if (stopInfo.reason) return `It can't be resumed: ${stopInfo.reason}.`
  return 'The print job was interrupted.'
}

export function PrintOverlay({ status, stopInfo, onResume, onRestart, onBack }: PrintOverlayProps): React.JSX.Element {
  const isCompleted = status === 'completed'
  const canResume = !isCompleted && stopInfo?.resumable === true

  return (
    <div className="fixed inset-0 flex items-center justify-center z-50 bg-[rgba(40,43,43,0.7)]">
      <div
        className="rounded-3xl p-8 text-center mx-4 bg-surface min-w-[280px] shadow-[0_24px_60px_rgba(0,0,0,0.3)]"
      >
        {/* Icon */}
        <div
          className={`w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-5 ${
            isCompleted ? 'bg-primary-muted' : 'bg-danger-muted'
          }`}
        >
          {isCompleted ? (
            <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" className="w-8 h-8 stroke-primary">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" strokeWidth="2" className="w-8 h-8 stroke-danger">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 7.5A2.25 2.25 0 0 1 7.5 5.25h9a2.25 2.25 0 0 1 2.25 2.25v9a2.25 2.25 0 0 1-2.25 2.25h-9a2.25 2.25 0 0 1-2.25-2.25v-9Z" />
            </svg>
          )}
        </div>

        <h2 className="text-xl font-bold mb-1 text-text">
          {isCompleted ? 'Print Complete!' : 'Print Stopped'}
        </h2>
        <p className="text-sm mb-7 text-text-muted">
          {isCompleted ? 'Your bioprint has finished successfully.' : stoppedMessage(stopInfo)}
        </p>

        <div className="flex flex-col gap-2.5 min-w-[240px]">
          {canResume && (
            <button
              onClick={onResume}
              className="flex items-center justify-center gap-2 w-full py-3.5 rounded-2xl text-white font-semibold text-sm tracking-wide transition-all active:scale-[0.97] bg-primary shadow-[0_4px_14px_rgba(26,139,141,0.35)]"
            >
              <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
                <path fillRule="evenodd" d="M4.5 5.653c0-1.427 1.529-2.33 2.779-1.643l11.54 6.347c1.295.712 1.295 2.573 0 3.286L7.28 19.99c-1.25.687-2.779-.217-2.779-1.643V5.653Z" clipRule="evenodd" />
              </svg>
              Resume Print
            </button>
          )}
          <button
            onClick={onRestart}
            className={`flex items-center justify-center gap-2 w-full py-3.5 rounded-2xl font-semibold text-sm tracking-wide transition-all active:scale-[0.97] ${
              isCompleted
                ? 'bg-primary text-white shadow-[0_4px_14px_rgba(26,139,141,0.35)]'
                : 'bg-transparent text-primary border-[1.5px] border-primary'
            }`}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-4 h-4">
              <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.992 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182" />
            </svg>
            Restart
          </button>
          <button
            onClick={onBack}
            className="flex items-center justify-center gap-2 w-full py-3.5 rounded-2xl font-semibold text-sm tracking-wide transition-all active:scale-[0.97] bg-surface-sunken text-text-secondary"
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
