import { useEffect, useRef, useState } from 'react'
import { NORDSON_TIPS, tipLabel } from '../data/nordsonTips'
import { usePrintSettings } from '../stores/printSettings'

function Swatch({ color }: { color: string }): React.JSX.Element {
  return (
    <span
      className="w-3 h-3 rounded-full shrink-0 border border-border-strong"
      style={{ backgroundColor: color }}
    />
  )
}

// A native <select> can't show the color swatches, hence the small custom listbox.
export function NordsonTipSelect(): React.JSX.Element {
  const gauge = usePrintSettings((s) => s.nordsonGauge)
  const selectTip = usePrintSettings((s) => s.selectNordsonTip)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  const selected = NORDSON_TIPS.find((t) => t.gauge === gauge) ?? null

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent): void => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  return (
    <div
      ref={rootRef}
      className="relative"
      onKeyDown={(e) => {
        if (e.key === 'Escape') setOpen(false)
      }}
    >
      <span className="text-[10px] block mb-1 text-text-muted">Nordson tip</span>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs text-left outline-none bg-surface text-text border border-border"
      >
        {selected ? (
          <>
            <Swatch color={selected.swatch} />
            <span className="flex-1">{tipLabel(selected)}</span>
          </>
        ) : (
          <span className="flex-1 text-text-subtle">Custom (enter nozzle ⌀ below)</span>
        )}
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="w-3 h-3 text-text-muted"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      {open && (
        <ul
          role="listbox"
          className="absolute z-10 left-0 right-0 mt-1 max-h-60 overflow-y-auto rounded-lg py-1 bg-surface border border-border shadow-[0_4px_12px_rgba(0,0,0,0.12)]"
        >
          {NORDSON_TIPS.map((tip) => (
            <li key={tip.gauge} role="option" aria-selected={tip.gauge === gauge}>
              <button
                type="button"
                onClick={() => {
                  selectTip(tip)
                  setOpen(false)
                }}
                className={`w-full flex items-center gap-2 px-2 py-1.5 text-xs text-left hover:bg-surface-sunken ${
                  tip.gauge === gauge ? 'text-primary font-semibold' : 'text-text'
                }`}
              >
                <Swatch color={tip.swatch} />
                {tipLabel(tip)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
