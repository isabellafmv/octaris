import type { NozzleSpec } from '../types'

// Decorative fill levels, so the syringes don't all look the same
const FILL_LEVELS = [0.75, 0.35, 0.55, 0.9]

function SyringeModule({
  label,
  isActive,
  fillLevel,
  onToggle
}: {
  label: string
  isActive: boolean
  fillLevel: number
  onToggle: () => void
}): React.JSX.Element {
  return (
    <button
      onClick={onToggle}
      aria-pressed={isActive}
      aria-label={`${label} syringe`}
      className="flex flex-col items-center gap-2 transition-all active:scale-95 group"
    >
      {/* Syringe SVG */}
      <svg viewBox="0 0 52 120" className="w-10 h-24">
        {/* Outer tube */}
        <rect
          x="10"
          y="10"
          width="32"
          height="88"
          rx="16"
          className={isActive ? 'fill-primary stroke-primary' : 'fill-none stroke-border-strong'}
          strokeWidth="2"
        />
        {/* Inner fill */}
        {isActive && (
          <rect
            x="12"
            y={10 + (1 - fillLevel) * 80}
            width="28"
            height={fillLevel * 80}
            rx="14"
            className={isActive ? 'fill-primary-strong' : 'fill-border'}
          />
        )}
        {!isActive && (
          <>
            {/* Empty tube outline interior */}
            <rect x="12" y="12" width="28" height="84" rx="14" className="fill-[#F0EBE0]" />
            {/* Partial fill to show it has some content */}
            <rect
              x="12"
              y={12 + (1 - fillLevel) * 70}
              width="28"
              height={fillLevel * 70}
              rx="10"
              className="fill-border"
            />
          </>
        )}
        {/* Tip */}
        <rect
          x="20"
          y="98"
          width="12"
          height="14"
          rx="6"
          className={isActive ? 'fill-primary' : 'fill-border-strong'}
        />
        {/* Cap ring */}
        <rect
          x="8"
          y="8"
          width="36"
          height="8"
          rx="4"
          className={isActive ? 'fill-primary-strong' : 'fill-[#B8B3A8]'}
        />
      </svg>

      {/* Dot indicator */}
      <div
        className={`w-2 h-2 rounded-full transition-all ${
          isActive ? 'bg-primary' : 'bg-border-strong'
        }`}
      />

      {/* Label */}
      <span
        className={`text-[10px] font-semibold tracking-widest uppercase ${
          isActive ? 'text-primary' : 'text-text-muted'
        }`}
      >
        {label} Syringe
      </span>
    </button>
  )
}

interface SyringeSelectorProps {
  // The printer profile's nozzles, left to right
  nozzles: NozzleSpec[]
  selected: string[]
  onToggle: (id: string) => void
  compact?: boolean
}

// The printer's syringes, left to right: tap one to select or deselect it.
// Any combination, at least one (the store keeps the last one selected).
export function SyringeSelector({
  nozzles,
  selected,
  onToggle,
  compact = false
}: SyringeSelectorProps): React.JSX.Element {
  if (compact) {
    // Too short for the drawing: one chip per syringe
    return (
      <div className="flex gap-2 justify-center">
        {nozzles.map((n) => {
          const active = selected.includes(n.id)
          return (
            <button
              key={n.id}
              onClick={() => onToggle(n.id)}
              aria-pressed={active}
              className={`px-3 py-1 rounded-full text-[10px] font-semibold tracking-wide uppercase transition-all ${
                active ? 'bg-primary text-white' : 'bg-border text-text-muted'
              }`}
            >
              {n.name}
            </button>
          )
        })}
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-4 w-full">
      <div className="flex items-end justify-center gap-12">
        {nozzles.map((n, i) => (
          <SyringeModule
            key={n.id}
            label={n.name}
            isActive={selected.includes(n.id)}
            fillLevel={FILL_LEVELS[i % FILL_LEVELS.length]}
            onToggle={() => onToggle(n.id)}
          />
        ))}
      </div>

      {/* Platform rail */}
      <div className="flex items-center gap-3 mt-1">
        <div className="h-0.5 w-32 rounded-full bg-border-strong" />
      </div>
    </div>
  )
}
