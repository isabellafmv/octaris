import { useCallback, useRef, useState } from 'react'
import { api } from '../api'

interface ExtrusionSliderProps {
  currentRate: number
}

export function ExtrusionSlider({ currentRate }: ExtrusionSliderProps) {
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
      <div className="h-1 rounded-full" style={{ backgroundColor: '#D8D3C8' }}>
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, backgroundColor: '#1A8B8D' }}
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
        className="slider-flow absolute inset-0 w-full opacity-0 cursor-pointer h-5"
        style={{ top: '-8px' }}
      />
      {/* Custom thumb */}
      <div
        className="absolute top-1/2 -translate-y-1/2 w-5 h-5 rounded-full pointer-events-none"
        style={{
          left: `calc(${pct}% - 10px)`,
          backgroundColor: '#1A8B8D',
          border: '3px solid white',
          boxShadow: '0 2px 6px rgba(0,0,0,0.18)',
        }}
      />
    </div>
  )
}
