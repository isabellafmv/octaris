import type { ChannelInfo, PrinterProfile, TemperatureStatus } from '../types'

// The lab firmware's heater channels (protocol "printess"): what each heats
// (the temperature_channels setting), and the commands that set them.

// A channel's use in the Setup dropdown: "unused", "bed" or "syringe:<nozzle id>"
export const UNUSED = 'unused'

export interface UseOption {
  value: string
  label: string
}

// What a channel heating this syringe is called (as the backend names it)
export function syringeLabel(name: string): string {
  return name.toLowerCase().includes('syringe') ? name : `${name} syringe`
}

// The choices for a channel: not used, the bed, or one of the profile's syringes
export function useOptions(profile: PrinterProfile | null): UseOption[] {
  return [
    { value: UNUSED, label: 'Not used' },
    { value: 'bed', label: 'Bed' },
    ...(profile?.nozzles ?? []).map((n) => ({
      value: `syringe:${n.id}`,
      label: syringeLabel(n.name)
    }))
  ]
}

// The temperature_channels setting with `sensor` set to `use`
export function withUse(
  channels: ChannelInfo[],
  sensor: string,
  use: string
): Record<string, string> {
  const result: Record<string, string> = {}
  for (const c of channels) {
    const value = c.sensor === sensor ? use : c.use
    if (value && value !== UNUSED) result[c.sensor] = value
  }
  return result
}

// The channels assigned to something; the others are hidden
export function usedChannels(status: TemperatureStatus | null): ChannelInfo[] {
  return (status?.channels ?? []).filter((c) => c.use !== null)
}

export type ChannelAction = 'heat' | 'cool' | 'off'

// The command the backend sends for `action` (Take Over's quick command shows it)
export function channelCommand(index: number, action: ChannelAction, target?: number): string {
  if (action === 'off') return `M104 I${index} D`
  return `M104 I${index} T${target ?? '<°C>'} ${action === 'heat' ? 'H' : 'C'}`
}
