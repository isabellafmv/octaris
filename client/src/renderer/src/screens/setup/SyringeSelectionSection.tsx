import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { SyringeSelector } from '../../components/SyringeSelector'
import { usePrinter } from '../../stores/printer'
import { usePrintSettings } from '../../stores/printSettings'
import type { PrinterProfile } from '../../types'

export function SyringeSelectionSection({
  onError
}: {
  onError: (msg: string) => void
}): React.JSX.Element {
  const profile = usePrinter((s) => s.profile)
  const selected = usePrintSettings((s) => s.selectedNozzles)
  const toggleNozzle = usePrintSettings((s) => s.toggleNozzle)
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
      className="relative flex-1 rounded-2xl flex flex-col items-center justify-center p-6 min-h-0 bg-surface-card"
    >
      <ProfileSelect profile={profile} onError={onError} />
      {profile && (
        <SyringeSelector
          nozzles={profile.nozzles}
          selected={selected}
          onToggle={toggleNozzle}
          compact={compact}
        />
      )}
    </div>
  )
}

// The printer profile. Switching resets the calibration and unloads the
// loaded print (the backend does both and tells every client).
function ProfileSelect({
  profile,
  onError
}: {
  profile: PrinterProfile | null
  onError: (msg: string) => void
}): React.JSX.Element {
  const [profiles, setProfiles] = useState<PrinterProfile[]>([])

  useEffect(() => {
    api
      .getPrinterProfiles()
      .then((r) => setProfiles(r.profiles))
      .catch(() => {}) // the dropdown then only shows the current profile
  }, [])

  const options = profiles.length > 0 ? profiles : profile ? [profile] : []

  const select = async (id: string): Promise<void> => {
    try {
      // The printer_profile event updates the store
      await api.selectPrinterProfile(id)
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Could not switch the printer profile')
    }
  }

  return (
    <label className="absolute top-3 left-4 flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-muted">
      Printer
      <select
        value={profile?.id ?? ''}
        onChange={(e) => select(e.target.value)}
        disabled={options.length < 2}
        className="text-xs font-semibold normal-case tracking-normal rounded-lg px-2 py-1 bg-surface-sunken text-text disabled:opacity-100"
      >
        {options.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  )
}
