import { PortSelector } from '../../components/PortSelector'

interface ConnectionSectionProps {
  printerConnected: boolean
  port: string | null
  onError: (msg: string) => void
}

// Screen title with the serial port picker on the right
export function ConnectionSection({
  printerConnected,
  port,
  onError
}: ConnectionSectionProps): React.JSX.Element {
  return (
    <div className="flex items-start justify-between px-8 pt-7 pb-4 shrink-0">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-primary">Setup &amp; Configuration</h1>
        <p className="text-xs tracking-widest uppercase mt-1 text-text-muted">
          Bioprinting System // Octaris
        </p>
      </div>
      <div className="mt-1">
        <PortSelector connected={printerConnected} port={port} onError={onError} />
      </div>
    </div>
  )
}
