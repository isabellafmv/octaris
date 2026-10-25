import { describe, expect, it } from 'vitest'
import type { ChannelInfo, PrinterProfile, TemperatureStatus } from '../types'
import {
  channelCommand,
  syringeLabel,
  usedChannels,
  useOptions,
  withUse
} from './temperatureChannels'

const PRINTESS_1: PrinterProfile = {
  id: 'printess-1',
  name: 'Printess 1.0',
  nozzles: [
    { id: 'left', name: 'Left', height_axis: 'Z', plunger_axis: 'B', offset_x: 0, offset_y: 0 },
    {
      id: 'right',
      name: 'Syringe 2',
      height_axis: 'A',
      plunger_axis: 'C',
      offset_x: 30,
      offset_y: 0
    }
  ],
  nozzle_offset_measured: false
}

function channel(index: number, use: string | null): ChannelInfo {
  return { sensor: `I${index}`, index, use, name: `I${index}`, min: 4, max: 50 }
}

const CHANNELS = [channel(0, 'syringe:left'), channel(1, null), channel(2, 'bed'), channel(3, null)]

describe('lab firmware channels', () => {
  it('offers not used, the bed and the syringes by name', () => {
    expect(useOptions(PRINTESS_1)).toEqual([
      { value: 'unused', label: 'Not used' },
      { value: 'bed', label: 'Bed' },
      { value: 'syringe:left', label: 'Left syringe' },
      { value: 'syringe:right', label: 'Syringe 2' }
    ])
    expect(useOptions(null).map((o) => o.value)).toEqual(['unused', 'bed'])
    expect(syringeLabel('Right')).toBe('Right syringe')
  })

  it('changes one channel, keeping the others', () => {
    expect(withUse(CHANNELS, 'I1', 'syringe:right')).toEqual({
      I0: 'syringe:left',
      I1: 'syringe:right',
      I2: 'bed'
    })
    expect(withUse(CHANNELS, 'I0', 'unused')).toEqual({ I2: 'bed' })
  })

  it('lists only the channels in use', () => {
    const status = { channels: CHANNELS } as TemperatureStatus
    expect(usedChannels(status).map((c) => c.sensor)).toEqual(['I0', 'I2'])
    expect(usedChannels(null)).toEqual([])
  })

  it('shows the commands the quick command sends', () => {
    expect(channelCommand(1, 'heat', 30)).toBe('M104 I1 T30 H')
    expect(channelCommand(1, 'cool', 12.5)).toBe('M104 I1 T12.5 C')
    expect(channelCommand(1, 'off')).toBe('M104 I1 D')
    expect(channelCommand(0, 'heat')).toBe('M104 I0 T<°C> H')
  })
})
