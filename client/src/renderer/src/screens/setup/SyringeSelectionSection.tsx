import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import { SyringeSelector } from '../../components/SyringeSelector'
import { usePrinter } from '../../stores/printer'
import { usePrintSettings } from '../../stores/printSettings'
import type { PrintStatus, PrinterProfile } from '../../types'
import {
  BED_AXES,
  CUSTOM_PROFILE_ID,
  MAX_SYRINGES,
  bedDraftFrom,
  canAdd,
  draftsFrom,
  isChanged,
  isValid,
  newDraft,
  toRequest,
  validateBed,
  validateDrafts,
  type BedDraft,
  type BedErrors,
  type DraftErrors,
  type DraftField,
  type SyringeDraft
} from './printerSetup'

const errorMessage = (e: unknown, fallback: string): string =>
  e instanceof Error ? e.message : fallback

export function SyringeSelectionSection({
  printStatus,
  onError
}: {
  printStatus: PrintStatus
  onError: (msg: string) => void
}): React.JSX.Element {
  const profile = usePrinter((s) => s.profile)
  const selected = usePrintSettings((s) => s.selectedNozzles)
  const toggleNozzle = usePrintSettings((s) => s.toggleNozzle)
  // Drop the syringe drawing when the card gets too short for it
  const [compact, setCompact] = useState(false)
  const cardRef = useRef<HTMLDivElement>(null)
  const [editing, setEditing] = useState(false)
  // The backend refuses profile changes during a print, too
  const printing = printStatus === 'printing' || printStatus === 'paused'

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
      className={`relative flex-1 rounded-2xl flex flex-col items-center p-6 min-h-0 bg-surface-card ${
        editing ? 'justify-start pt-12 overflow-y-auto' : 'justify-center'
      }`}
    >
      <ProfileSelect
        profile={profile}
        disabled={printing || editing}
        onNewCustom={() => setEditing(true)}
        onError={onError}
      />
      {profile && !editing && (
        <button
          onClick={() => setEditing(true)}
          disabled={printing}
          title={printing ? 'Stop the print to change the printer setup' : undefined}
          className="absolute top-3 right-4 text-[10px] font-semibold tracking-widest uppercase text-primary disabled:opacity-40"
        >
          Edit setup
        </button>
      )}
      {profile && editing ? (
        <SetupEditor
          profile={profile}
          compact={compact}
          printing={printing}
          onDone={() => setEditing(false)}
          onError={onError}
        />
      ) : (
        profile && (
          <SyringeSelector
            nozzles={profile.nozzles}
            selected={selected}
            onToggle={toggleNozzle}
            compact={compact}
          />
        )
      )}
    </div>
  )
}

// The printer profile. Switching resets the calibration and unloads the
// loaded print (the backend does both and tells every client).
function ProfileSelect({
  profile,
  disabled,
  onNewCustom,
  onError
}: {
  profile: PrinterProfile | null
  disabled: boolean
  // Picking "Custom" before one has been saved opens the editor; saving there creates it
  onNewCustom: () => void
  onError: (msg: string) => void
}): React.JSX.Element {
  const profiles = usePrinter((s) => s.profiles)
  const setProfiles = usePrinter((s) => s.setProfiles)

  useEffect(() => {
    // printer_profiles events keep the list up to date after this
    api
      .getPrinterProfiles()
      .then((r) => setProfiles(r.profiles))
      .catch(() => {}) // the dropdown then only shows the current profile
  }, [setProfiles])

  const options = profiles.length > 0 ? profiles : profile ? [profile] : []
  // Custom is always on the list, so a new printer can be set up from here
  const hasCustom = options.some((p) => p.id === CUSTOM_PROFILE_ID)

  const select = async (id: string): Promise<void> => {
    if (id === CUSTOM_PROFILE_ID && !hasCustom) {
      onNewCustom()
      return
    }
    try {
      // The printer_profile event updates the store
      await api.selectPrinterProfile(id)
    } catch (e) {
      onError(errorMessage(e, 'Could not switch the printer profile'))
    }
  }

  return (
    <label className="absolute top-3 left-4 flex items-center gap-2 text-[10px] font-semibold tracking-widest uppercase text-text-muted">
      Printer
      <select
        value={profile?.id ?? ''}
        onChange={(e) => select(e.target.value)}
        disabled={disabled}
        className="text-xs font-semibold normal-case tracking-normal rounded-lg px-2 py-1 bg-surface-sunken text-text disabled:opacity-100"
      >
        {options.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
        {!hasCustom && <option value={CUSTOM_PROFILE_ID}>Custom (set up…)</option>}
      </select>
    </label>
  )
}

// Each syringe's name, axes and offset, typed under it, and the bed limits. Saved as the
// Custom profile (a built-in profile being edited is copied into it), which
// is then selected: that resets the calibration and unloads the print.
function SetupEditor({
  profile,
  compact,
  printing,
  onDone,
  onError
}: {
  profile: PrinterProfile
  compact: boolean
  printing: boolean
  onDone: () => void
  onError: (msg: string) => void
}): React.JSX.Element {
  const profiles = usePrinter((s) => s.profiles)
  const [drafts, setDrafts] = useState<SyringeDraft[]>(() => draftsFrom(profile))
  const [offsetsMeasured, setOffsetsMeasured] = useState(profile.nozzle_offset_measured ?? false)
  const [bed, setBed] = useState<BedDraft>(() => bedDraftFrom(profile))
  const [saving, setSaving] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)

  const errors = useMemo(() => validateDrafts(drafts), [drafts])
  const bedErrors = useMemo(() => validateBed(bed), [bed])
  const valid = isValid(drafts, errors) && Object.keys(bedErrors).length === 0
  const changed = isChanged(drafts, offsetsMeasured, profile, bed)
  const hasCustom =
    profile.id === CUSTOM_PROFILE_ID || profiles.some((p) => p.id === CUSTOM_PROFILE_ID)

  // Another window changed the profile: start over from it, unless there
  // are edits here to keep
  const changedRef = useRef(changed)
  changedRef.current = changed
  useEffect(() => {
    if (changedRef.current) return
    setDrafts(draftsFrom(profile))
    setOffsetsMeasured(profile.nozzle_offset_measured ?? false)
    setBed(bedDraftFrom(profile))
  }, [profile])

  const update = (index: number, field: DraftField, value: string): void =>
    setDrafts((ds) => ds.map((d, i) => (i === index ? { ...d, [field]: value } : d)))

  const save = async (): Promise<void> => {
    setSaving(true)
    try {
      // The printer_profile and printer_profiles events update the stores
      await api.saveCustomProfile(toRequest(drafts, offsetsMeasured, profile.id, bed))
      onDone()
    } catch (e) {
      onError(errorMessage(e, 'Could not save the printer setup'))
    } finally {
      setSaving(false)
    }
  }

  const reset = async (): Promise<void> => {
    setConfirmReset(false)
    setSaving(true)
    try {
      await api.resetCustomProfile()
      onDone()
    } catch (e) {
      onError(errorMessage(e, 'Could not reset the printer setup'))
    } finally {
      setSaving(false)
    }
  }

  const locked = printing || saving
  const buttonClass =
    'px-3 py-1.5 rounded-lg text-xs font-semibold transition-all active:scale-95 disabled:opacity-40'

  return (
    <div className="flex flex-col items-center gap-3 w-full">
      <div className="max-w-full overflow-x-auto pb-1">
        <SyringeSelector
          nozzles={drafts.map((d) => ({ id: d.id, name: d.name || '—' }))}
          selected={[]}
          onToggle={() => {}}
          compact={compact}
          below={(i) => (
            <SyringeFields
              draft={drafts[i]}
              errors={errors[i]}
              disabled={locked}
              canRemove={drafts.length > 1}
              onChange={(field, value) => update(i, field, value)}
              onRemove={() => setDrafts((ds) => ds.filter((_, j) => j !== i))}
            />
          )}
        />
      </div>

      <div className="flex flex-wrap items-center justify-center gap-2">
        <button
          onClick={() => setDrafts((ds) => [...ds, newDraft(ds)])}
          disabled={locked || !canAdd(drafts)}
          title={
            canAdd(drafts)
              ? undefined
              : drafts.length >= MAX_SYRINGES
                ? `At most ${MAX_SYRINGES} syringes`
                : 'Every axis letter is in use'
          }
          className={`${buttonClass} bg-surface-sunken text-text`}
        >
          + Add syringe
        </button>
        <label className="flex items-center gap-1.5 text-xs text-text-secondary">
          <input
            type="checkbox"
            checked={offsetsMeasured}
            onChange={(e) => setOffsetsMeasured(e.target.checked)}
            disabled={locked}
          />
          Offsets measured
        </label>
      </div>

      <BedFields
        bed={bed}
        errors={bedErrors}
        disabled={locked}
        onChange={(axis, end, value) =>
          setBed((b) => ({ ...b, [axis]: { ...b[axis], [end]: value } }))
        }
      />

      <p className="text-[10px] text-center text-text-subtle max-w-sm">
        {printing
          ? 'Stop the print to change the printer setup.'
          : profile.id === CUSTOM_PROFILE_ID
            ? 'Saving resets the calibration and unloads the loaded print.'
            : `Saving copies the ${profile.name} into the Custom profile and selects it. It resets the calibration and unloads the loaded print.`}
      </p>

      <div className="flex flex-wrap items-center justify-center gap-2">
        {hasCustom &&
          (confirmReset ? (
            <button
              onClick={reset}
              disabled={locked}
              className={`${buttonClass} bg-danger text-white`}
            >
              Delete Custom and use Printess 1.0?
            </button>
          ) : (
            <button
              onClick={() => setConfirmReset(true)}
              disabled={locked}
              className={`${buttonClass} bg-danger-muted text-danger`}
            >
              Reset to Printess 1.0
            </button>
          ))}
        <button
          onClick={onDone}
          disabled={saving}
          className={`${buttonClass} bg-surface-sunken text-text`}
        >
          Cancel
        </button>
        <button
          onClick={save}
          disabled={locked || !valid || !changed}
          title={!valid ? 'Fix the fields marked in red first' : undefined}
          className={`${buttonClass} bg-primary text-white`}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}

function SyringeFields({
  draft,
  errors,
  disabled,
  canRemove,
  onChange,
  onRemove
}: {
  draft: SyringeDraft
  errors: DraftErrors
  disabled: boolean
  canRemove: boolean
  onChange: (field: DraftField, value: string) => void
  onRemove: () => void
}): React.JSX.Element {
  const input = (
    field: DraftField,
    label: string,
    className: string,
    upper = false
  ): React.JSX.Element => (
    <input
      value={draft[field]}
      onChange={(e) => onChange(field, upper ? e.target.value.toUpperCase() : e.target.value)}
      disabled={disabled}
      aria-label={`${draft.name || 'Syringe'} ${label}`}
      aria-invalid={errors[field] !== undefined}
      title={errors[field]}
      inputMode={field === 'offsetX' || field === 'offsetY' ? 'decimal' : undefined}
      maxLength={upper ? 1 : undefined}
      className={`${className} px-1.5 py-1 rounded-md text-xs text-center outline-none bg-surface text-text border ${
        errors[field] ? 'border-danger text-danger' : 'border-border'
      } disabled:opacity-50`}
    />
  )
  const messages = [...new Set(Object.values(errors))]

  return (
    <div className="flex flex-col items-center gap-1 w-28">
      {input('name', 'name', 'w-full')}
      <div className="flex items-center gap-1 text-[10px] text-text-muted">
        <span title="Height axis">H</span>
        {input('heightAxis', 'height axis', 'w-7', true)}
        <span title="Plunger axis">P</span>
        {input('plungerAxis', 'plunger axis', 'w-7', true)}
      </div>
      <div
        className="flex items-center gap-1 text-[10px] text-text-muted"
        title="Offset from the first syringe, in mm"
      >
        <span>X</span>
        {input('offsetX', 'offset X in mm', 'w-10')}
        <span>Y</span>
        {input('offsetY', 'offset Y in mm', 'w-10')}
      </div>
      {messages.map((m) => (
        <p key={m} className="text-[10px] leading-tight text-center text-danger">
          {m}
        </p>
      ))}
      {canRemove && (
        <button
          onClick={onRemove}
          disabled={disabled}
          className="text-[10px] font-semibold text-text-muted hover:text-danger disabled:opacity-40"
        >
          Remove
        </button>
      )}
    </div>
  )
}

// Where the nozzles may go, min and max per axis, in mm from the zero point.
// Jogs (once zeroed), Go to Origin and prints that would leave it are refused.
function BedFields({
  bed,
  errors,
  disabled,
  onChange
}: {
  bed: BedDraft
  errors: BedErrors
  disabled: boolean
  onChange: (axis: keyof BedDraft, end: 'min' | 'max', value: string) => void
}): React.JSX.Element {
  const messages = [...new Set(Object.values(errors))]

  return (
    <fieldset className="flex flex-col items-center gap-1.5">
      <legend className="mb-1.5 text-center text-[10px] font-semibold tracking-widest uppercase text-text-muted">
        Bed limits (mm)
      </legend>
      <div className="flex flex-wrap justify-center gap-x-4 gap-y-1.5">
        {BED_AXES.map((axis) => (
          <div key={axis} className="flex items-center gap-1 text-[10px] text-text-muted">
            <span className="w-3 font-semibold">{axis.toUpperCase()}</span>
            {(['min', 'max'] as const).map((end, i) => (
              <span key={end} className="flex items-center gap-1">
                {i === 1 && <span>to</span>}
                <input
                  value={bed[axis][end]}
                  onChange={(e) => onChange(axis, end, e.target.value)}
                  disabled={disabled}
                  inputMode="decimal"
                  aria-label={`Bed ${axis.toUpperCase()} ${end} in mm`}
                  aria-invalid={errors[axis] !== undefined}
                  title={errors[axis]}
                  className={`w-12 px-1.5 py-1 rounded-md text-xs text-center outline-none bg-surface text-text border ${
                    errors[axis] ? 'border-danger text-danger' : 'border-border'
                  } disabled:opacity-50`}
                />
              </span>
            ))}
          </div>
        ))}
      </div>
      {messages.map((m) => (
        <p key={m} className="text-[10px] leading-tight text-center text-danger">
          {m}
        </p>
      ))}
      <p className="text-[10px] text-center text-text-subtle max-w-sm">
        Relative to where you zero; e.g. −45 to 45 for a 90 mm dish zeroed in the centre.
      </p>
    </fieldset>
  )
}
