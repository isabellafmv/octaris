import { useCallback, useRef, useState } from 'react'
import { api } from '../api'

interface ExtrusionSliderProps {
  currentRate: number
}

export function ExtrusionSlider({ currentRate }: ExtrusionSliderProps): React.JSX.Element {
  const [value, setValue] = useState(currentRate)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null)

  const handleChange = useCallback((newValue: number) => {
    setValue(newValue)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      api.setExtrusion(newValue)
    }, 200)
  }, [])

  const pct = ((value - 50) / (150 - 50)) * 100

  return (
    <div className="relative">
      {/* Track */}
      <div className="h-1 rounded-full bg-border">
        <div
          className="h-full rounded-full bg-primary"
          style={{ width: `${pct}%` }}
        />
      </div>
      {/* Native input (invisible, layered on top for interaction) */}
      <input
        type="range"
        min={50}
        max={150}
        step={1}
        value={value}
        onChange={(e) => handleChange(Number(e.target.value))}
        className="slider-flow absolute inset-0 w-full opacity-0 cursor-pointer h-5 top-[-8px]"
      />
      {/* Custom thumb */}
      <div
        className="absolute top-1/2 -translate-y-1/2 w-5 h-5 rounded-full pointer-events-none bg-primary border-[3px] border-white shadow-[0_2px_6px_rgba(0,0,0,0.18)]"
        style={{ left: `calc(${pct}% - 10px)` }}
      />
    </div>
  )
}
