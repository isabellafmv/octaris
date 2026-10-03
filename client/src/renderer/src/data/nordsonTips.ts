// Nordson EFD dispense tips, keyed by gauge. `swatch` approximates the hub color.
export interface NordsonTip {
  gauge: number
  innerDiameterMm: number
  color: string
  swatch: string
}

export const NORDSON_TIPS: readonly NordsonTip[] = [
  { gauge: 14, innerDiameterMm: 1.54, color: 'Olive', swatch: '#7A7A3A' },
  { gauge: 15, innerDiameterMm: 1.36, color: 'Amber', swatch: '#D9902F' },
  { gauge: 18, innerDiameterMm: 0.84, color: 'Green', swatch: '#3E9B4F' },
  { gauge: 20, innerDiameterMm: 0.61, color: 'Pink', swatch: '#E48FB0' },
  { gauge: 21, innerDiameterMm: 0.51, color: 'Purple', swatch: '#7E4FA8' },
  { gauge: 22, innerDiameterMm: 0.41, color: 'Blue', swatch: '#3C6FC4' },
  { gauge: 23, innerDiameterMm: 0.33, color: 'Orange', swatch: '#EE7F2D' },
  { gauge: 25, innerDiameterMm: 0.25, color: 'Red', swatch: '#D0413A' },
  { gauge: 27, innerDiameterMm: 0.2, color: 'Clear', swatch: 'transparent' },
  { gauge: 30, innerDiameterMm: 0.15, color: 'Lavender', swatch: '#B9A6D9' },
  { gauge: 32, innerDiameterMm: 0.1, color: 'Yellow', swatch: '#F2D23C' }
]

export function tipLabel(tip: NordsonTip): string {
  return `${tip.gauge} G · ${tip.innerDiameterMm.toFixed(2)} mm · ${tip.color}`
}
