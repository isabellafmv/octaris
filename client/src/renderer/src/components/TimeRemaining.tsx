import { useEffect, useState } from 'react'

interface TimeRemainingProps {
  seconds: number | null
  linesSent: number
  linesTotal: number
}

function formatTime(s: number): string {
  if (s <= 0) return '0 min 0 sec'
  const min = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${min} min ${sec} sec`
}

export function TimeRemaining({
  seconds,
  linesSent,
  linesTotal
}: TimeRemainingProps): React.JSX.Element {
  const estimate =
    seconds === null || linesTotal === 0
      ? null
      : Math.max(0, Math.round(seconds * (1 - linesSent / linesTotal)))
  const [remaining, setRemaining] = useState<number>(estimate ?? 0)
  // Restart the countdown from each new estimate
  const [lastEstimate, setLastEstimate] = useState(estimate)
  if (estimate !== lastEstimate) {
    setLastEstimate(estimate)
    if (estimate !== null) setRemaining(estimate)
  }

  useEffect(() => {
    const timer = setInterval(() => {
      setRemaining((r) => Math.max(0, r - 1))
    }, 1000)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="text-center bg-gray-50 rounded-xl py-4 px-6">
      <div className="text-sm text-gray-500">Time Remaining</div>
      <div className="text-2xl font-semibold mt-1">{formatTime(remaining)}</div>
    </div>
  )
}
