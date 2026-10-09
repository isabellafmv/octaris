import type { Screen } from '../hooks/useScreenNavigation'

function SetupIcon(): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className="w-5 h-5"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75"
      />
    </svg>
  )
}

function MonitorIcon(): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className="w-5 h-5"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"
      />
    </svg>
  )
}

function TemperatureIcon(): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className="w-5 h-5"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M14 14.76V4.5a2 2 0 1 0-4 0v10.26a4 4 0 1 0 4 0ZM12 8v9.5"
      />
    </svg>
  )
}

function LogsIcon(): React.JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className="w-5 h-5"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
      />
    </svg>
  )
}

// One entry per screen; adding a screen to the sidebar is one line here.
const NAV_ITEMS: { id: Screen; icon: React.JSX.Element; label: string }[] = [
  { id: 'setup', icon: <SetupIcon />, label: 'SETUP' },
  { id: 'print', icon: <MonitorIcon />, label: 'MONITOR' },
  { id: 'temperature', icon: <TemperatureIcon />, label: 'TEMP' },
  { id: 'takeover', icon: <LogsIcon />, label: 'LOGS' }
]

interface SidebarProps {
  screen: Screen
  onNavigate: (to: Screen) => void
}

export function Sidebar({ screen, onNavigate }: SidebarProps): React.JSX.Element {
  return (
    <nav className="flex flex-col items-center gap-1 py-2 w-14 shrink-0">
      {NAV_ITEMS.map((item) => (
        <div key={item.id} className="flex flex-col items-center gap-0.5 w-full">
          <button
            onClick={() => onNavigate(item.id)}
            className={`w-10 h-10 rounded-full flex items-center justify-center transition-all active:scale-90 no-drag ${
              screen === item.id ? 'bg-primary text-white' : 'text-chrome-icon'
            }`}
            title={item.label}
            aria-current={screen === item.id ? 'page' : undefined}
          >
            {item.icon}
          </button>
          <span
            className={`text-[8px] tracking-widest uppercase font-medium ${
              screen === item.id ? 'text-primary' : 'text-chrome-label'
            }`}
          >
            {item.label}
          </span>
        </div>
      ))}
    </nav>
  )
}
