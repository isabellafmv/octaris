// StatusBar is no longer rendered in the new design.
// Navigation and status are handled inline in App.tsx and each screen.
export function StatusBar(_props: {
  printerConnected: boolean
  port: string | null
  status: string
  onTakeOver?: () => void
  showTakeOver?: boolean
}) {
  return null
}
