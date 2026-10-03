import { NordsonTipSelect } from '../../components/NordsonTipSelect'
import { usePrintSettings, type PrintParameter } from '../../stores/printSettings'

interface Field {
  key: PrintParameter
  label: string
  step: string
  placeholder: string
}

// One array per row; null leaves an empty slot so the row keeps its column widths
const ROWS: (Field | null)[][] = [
  [
    { key: 'nozzleDiameter', label: 'Nozzle ⌀ (mm)', step: '0.01', placeholder: 'e.g. 0.41' },
    { key: 'syringeDiameter', label: 'Syringe ⌀ (mm)', step: '0.01', placeholder: 'e.g. 4.7' },
    { key: 'layerHeight', label: 'Layer H (mm)', step: '0.01', placeholder: 'auto' }
  ],
  [
    { key: 'pressurizeMm', label: 'Pressurize (mm)', step: '0.1', placeholder: '0.2' },
    { key: 'flowMultiplier', label: 'Flow multiplier', step: '0.1', placeholder: '1.0' }
  ],
  [
    { key: 'travelRetractMultiplier', label: 'Travel retract ×', step: '0.1', placeholder: '3.0' },
    null
  ]
]

function ParameterInput({ field }: { field: Field }): React.JSX.Element {
  const value = usePrintSettings((s) => s[field.key])
  const setParameter = usePrintSettings((s) => s.setParameter)

  return (
    <label className="flex-1">
      <span className="text-[10px] block mb-1 text-text-muted">{field.label}</span>
      <input
        type="number"
        step={field.step}
        min="0"
        placeholder={field.placeholder}
        value={value}
        onChange={(e) => setParameter(field.key, e.target.value)}
        className="w-full px-2 py-1.5 rounded-lg text-xs outline-none bg-surface text-text border border-border"
      />
    </label>
  )
}

export function PrintParametersSection(): React.JSX.Element {
  return (
    <div className="rounded-2xl p-4 flex flex-col gap-2 bg-surface-card">
      <span className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">
        Print Parameters
      </span>
      {/* Picking a tip fills in the nozzle diameter below */}
      <NordsonTipSelect />
      {ROWS.map((row, i) => (
        <div key={i} className="flex gap-2">
          {row.map((field, j) =>
            field ? (
              <ParameterInput key={field.key} field={field} />
            ) : (
              <div key={j} className="flex-1" />
            )
          )}
        </div>
      ))}
      <span className="text-[9px] text-text-subtle">
        Layer height defaults to 80% of nozzle diameter. Travel retract × scales the pressurize
        distance for travel moves only — raise it to stop oozing between segments.
      </span>
    </div>
  )
}
