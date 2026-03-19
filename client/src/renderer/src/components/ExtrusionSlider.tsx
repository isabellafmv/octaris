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

  return (
    <div className="flex items-center gap-4">
      <span className="text-sm text-gray-500 w-8">50%</span>
      <input
        type="range"
        min={50}
        max={150}
        step={1}
        value={value}
        onChange={(e) => handleChange(Number(e.target.value))}
        className="flex-1 h-2 accent-blue-600"
      />
      <span className="text-sm text-gray-500 w-10">150%</span>
      <span className="text-lg font-semibold min-w-[4ch] text-right">{value}%</span>
    </div>
  )
}
