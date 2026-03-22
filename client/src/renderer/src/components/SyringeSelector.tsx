import type { SyringeMode } from '../types'

interface SyringeModuleVizProps {
  selected: SyringeMode
  onSelect: (mode: SyringeMode) => void
}

function SyringeModule({
  label,
  mode,
  selected,
  fillLevel,
  onSelect
}: {
  label: string
  mode: SyringeMode
  selected: SyringeMode
  fillLevel: number
  onSelect: (m: SyringeMode) => void
}) {
  const isActive = selected === mode || selected === 'both'

  return (
    <button
      onClick={() => onSelect(selected === mode ? 'both' : mode)}
      className="flex flex-col items-center gap-2 transition-all active:scale-95 group"
    >
      {/* Syringe SVG */}
      <svg viewBox="0 0 52 120" className="w-10 h-24">
        {/* Outer tube */}
        <rect
          x="10" y="10" width="32" height="88"
          rx="16"
          fill={isActive ? '#1A8B8D' : 'none'}
          stroke={isActive ? '#1A8B8D' : '#C8C3B8'}
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
            fill={isActive ? '#127A7C' : '#D8D3C8'}
          />
        )}
        {!isActive && (
          <>
            {/* Empty tube outline interior */}
            <rect x="12" y="12" width="28" height="84" rx="14" fill="#F0EBE0" />
            {/* Partial fill to show it has some content */}
            <rect
              x="12"
              y={12 + (1 - fillLevel) * 70}
              width="28"
              height={fillLevel * 70}
              rx="10"
              fill="#D8D3C8"
            />
          </>
        )}
        {/* Tip */}
        <rect
          x="20" y="98" width="12" height="14"
          rx="6"
          fill={isActive ? '#1A8B8D' : '#C8C3B8'}
        />
        {/* Cap ring */}
        <rect
          x="8" y="8" width="36" height="8"
          rx="4"
          fill={isActive ? '#127A7C' : '#B8B3A8'}
        />
      </svg>

      {/* Dot indicator */}
      <div
        className="w-2 h-2 rounded-full transition-all"
        style={{ backgroundColor: isActive ? '#1A8B8D' : '#C8C3B8' }}
      />

      {/* Label */}
      <span
        className="text-[10px] font-semibold tracking-widest uppercase"
        style={{ color: isActive ? '#1A8B8D' : '#8B9090' }}
      >
        {label} Syringe
      </span>
    </button>
  )
}

export function SyringeModuleViz({ selected, onSelect }: SyringeModuleVizProps) {
  return (
    <div className="flex flex-col items-center gap-4 w-full">
      {/* Modules row */}
      <div className="flex items-end justify-center gap-12">
        <SyringeModule
          label="Left"
          mode="left"
          selected={selected}
          fillLevel={0.75}
          onSelect={onSelect}
        />

        {/* Platform bar */}
        {/* <div className="absolute h-0.5 w-28 rounded-full" style={{ backgroundColor: '#C8C3B8' }} /> */}

        <SyringeModule
          label="Right"
          mode="right"
          selected={selected}
          fillLevel={0.35}
          onSelect={onSelect}
        />
      </div>

      {/* Platform rail */}
      <div className="flex items-center gap-3 mt-1">
        <div className="h-0.5 w-32 rounded-full" style={{ backgroundColor: '#C8C3B8' }} />
      </div>

      {/* Toggle hint */}
      <p className="text-center text-xs leading-relaxed max-w-50" style={{ color: '#8B9090' }}>
        Tap a module to toggle{' '}
        <span className="font-semibold" style={{ color: '#2D3333' }}>dual-ink mode</span>
        {' '}or single extrusion.
      </p>

      {/* Mode indicator pills */}
      <div className="flex gap-2">
        {(['left', 'right', 'both'] as SyringeMode[]).map((m) => (
          <button
            key={m}
            onClick={() => onSelect(m)}
            className="px-3 py-1 rounded-full text-[10px] font-semibold tracking-wide uppercase transition-all"
            style={
              selected === m
                ? { backgroundColor: '#1A8B8D', color: 'white' }
                : { backgroundColor: '#D8D3C8', color: '#8B9090' }
            }
          >
            {m}
          </button>
        ))}
      </div>
    </div>
  )
}

// Keep backward-compatible export
export function SyringeSelector({
  selected,
  onSelect
}: {
  selected: SyringeMode
  onSelect: (m: SyringeMode) => void
}) {
  return <SyringeModuleViz selected={selected} onSelect={onSelect} />
}
