import { useCallback, useState } from 'react'

export type Screen = 'setup' | 'print' | 'temperature' | 'takeover'

interface Navigation {
  screen: Screen
  navigate: (to: Screen) => void
  // Return to the screen shown before the current one (Setup if none).
  back: () => void
}

export function useScreenNavigation(initial: Screen = 'setup'): Navigation {
  const [{ screen }, setState] = useState<{ screen: Screen; previous: Screen | null }>({
    screen: initial,
    previous: null
  })

  const navigate = useCallback((to: Screen) => {
    setState((s) => (s.screen === to ? s : { screen: to, previous: s.screen }))
  }, [])

  const back = useCallback(() => {
    setState((s) => ({ screen: s.previous ?? 'setup', previous: s.screen }))
  }, [])

  return { screen, navigate, back }
}
