interface StatusBarProps {
  printerConnected: boolean
  port: string | null
  status: string
  onTakeOver?: () => void
  showTakeOver?: boolean
}

export function StatusBar({ printerConnected, port, status, onTakeOver, showTakeOver = true }: StatusBarProps) {
  return (
    <div className="flex items-center justify-between px-4 py-2 bg-gray-900 text-white text-sm">
      <div className="flex items-center gap-2">
        <span
          className={`inline-block w-3 h-3 rounded-full ${printerConnected ? 'bg-green-500' : 'bg-red-500'}`}
        />
        <span>{printerConnected ? `Connected: ${port}` : 'Not connected'}</span>
      </div>
      <div className="flex items-center gap-3">
        <span className="uppercase tracking-wide text-gray-400">{status}</span>
        {showTakeOver && printerConnected && (
          <button
            onClick={onTakeOver}
            className="flex items-center gap-1 px-3 py-1 rounded bg-gray-700 text-gray-300
              hover:bg-gray-600 active:bg-gray-500 text-xs font-medium uppercase tracking-wide"
            title="Manual Take Over — send G-code commands directly"
          >
            <span className="text-base">⚙</span>
            <span>Take Over</span>
          </button>
        )}
      </div>
    </div>
  )
}
