interface StartPrintButtonProps {
  disabled: boolean
  onClick: () => void
}

export function StartPrintButton({ disabled, onClick }: StartPrintButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="min-h-[56px] w-full py-4 rounded-xl bg-green-600 text-white text-xl font-bold active:scale-[0.98] transition-transform disabled:opacity-40 disabled:cursor-not-allowed"
    >
      Start Print
    </button>
  )
}
