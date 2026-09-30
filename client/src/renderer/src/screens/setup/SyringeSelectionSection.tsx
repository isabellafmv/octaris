import { useEffect, useRef, useState } from 'react'
import { SyringeModuleViz } from '../../components/SyringeSelector'
import { usePrintSettings } from '../../stores/printSettings'

export function SyringeSelectionSection(): React.JSX.Element {
  const syringeMode = usePrintSettings((s) => s.syringeMode)
  const setSyringeMode = usePrintSettings((s) => s.setSyringeMode)
  // Drop the syringe drawing when the card gets too short for it
  const [compact, setCompact] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = cardRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      setCompact(entry.contentRect.height < 240)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <div
      ref={cardRef}
      className="flex-1 rounded-2xl flex flex-col items-center justify-center p-6 min-h-0 bg-surface-card"
    >
      <SyringeModuleViz selected={syringeMode} onSelect={setSyringeMode} compact={compact} />
    </div>
  )
}
