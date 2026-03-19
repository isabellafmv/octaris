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

export function TimeRemaining({ seconds, linesSent, linesTotal }: TimeRemainingProps) {
  const [remaining, setRemaining] = useState<number>(seconds ?? 0)

  useEffect(() => {
    if (seconds === null || linesTotal === 0) return
    const fraction = 1 - linesSent / linesTotal
    setRemaining(Math.max(0, Math.round(seconds * fraction)))
  }, [seconds, linesSent, linesTotal])

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
