import type { SyringeMode } from '../types'

interface SyringeSelectorProps {
  selected: SyringeMode
  onSelect: (mode: SyringeMode) => void
}

const OPTIONS: { mode: SyringeMode; label: string; axes: string }[] = [
  { mode: 'left', label: 'Left', axes: 'Z + B' },
  { mode: 'right', label: 'Right', axes: 'A + C' },
  { mode: 'both', label: 'Both', axes: 'Z + A + B + C' }
]

export function SyringeSelector({ selected, onSelect }: SyringeSelectorProps) {
  return (
    <div className="flex gap-3">
      {OPTIONS.map((opt) => (
        <button
          key={opt.mode}
          onClick={() => onSelect(opt.mode)}
          className={`flex-1 min-h-[80px] px-4 py-3 rounded-xl border-2 text-center transition-all active:scale-95 ${
            selected === opt.mode
              ? 'border-blue-500 bg-blue-50 text-blue-700'
              : 'border-gray-300 bg-white text-gray-700 hover:border-gray-400'
          }`}
        >
          <div className="text-lg font-semibold">{opt.label}</div>
          <div className="text-sm text-gray-500 mt-1">{opt.axes}</div>
        </button>
      ))}
    </div>
  )
}
