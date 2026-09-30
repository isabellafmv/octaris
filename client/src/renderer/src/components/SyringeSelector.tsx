import type { SyringeMode } from "../types";

interface SyringeModuleVizProps {
  selected: SyringeMode;
  onSelect: (mode: SyringeMode) => void;
  compact?: boolean;
}

function SyringeModule({
  label,
  mode,
  selected,
  fillLevel,
  onSelect,
}: {
  label: string;
  mode: SyringeMode;
  selected: SyringeMode;
  fillLevel: number;
  onSelect: (m: SyringeMode) => void;
}): React.JSX.Element {
  const isActive = selected === mode || selected === "both";

  return (
    <button
      onClick={() => onSelect(selected === mode ? "both" : mode)}
      className="flex flex-col items-center gap-2 transition-all active:scale-95 group"
    >
      {/* Syringe SVG */}
      <svg viewBox="0 0 52 120" className="w-10 h-24">
        {/* Outer tube */}
        <rect
          x="10"
          y="10"
          width="32"
          height="88"
          rx="16"
          className={
            isActive ? "fill-primary stroke-primary" : "fill-none stroke-border-strong"
          }
          strokeWidth="2"
        />
        {/* Inner fill */}
        {isActive && (
          <rect
            x="12"
            y={10 + (1 - fillLevel) * 80}
            width="28"
            height={fillLevel * 80}
            rx="14"
            className={isActive ? "fill-primary-strong" : "fill-border"}
          />
        )}
        {!isActive && (
          <>
            {/* Empty tube outline interior */}
            <rect
              x="12"
              y="12"
              width="28"
              height="84"
              rx="14"
              className="fill-[#F0EBE0]"
            />
            {/* Partial fill to show it has some content */}
            <rect
              x="12"
              y={12 + (1 - fillLevel) * 70}
              width="28"
              height={fillLevel * 70}
              rx="10"
              className="fill-border"
            />
          </>
        )}
        {/* Tip */}
        <rect
          x="20"
          y="98"
          width="12"
          height="14"
          rx="6"
          className={isActive ? "fill-primary" : "fill-border-strong"}
        />
        {/* Cap ring */}
        <rect
          x="8"
          y="8"
          width="36"
          height="8"
          rx="4"
          className={isActive ? "fill-primary-strong" : "fill-[#B8B3A8]"}
        />
      </svg>

      {/* Dot indicator */}
      <div
        className={`w-2 h-2 rounded-full transition-all ${
          isActive ? "bg-primary" : "bg-border-strong"
        }`}
      />

      {/* Label */}
      <span
        className={`text-[10px] font-semibold tracking-widest uppercase ${
          isActive ? "text-primary" : "text-text-muted"
        }`}
      >
        {label} Syringe
      </span>
    </button>
  );
}

export function SyringeModuleViz({
  selected,
  onSelect,
  compact = false,
}: SyringeModuleVizProps): React.JSX.Element {
  return (
    <div className="flex flex-col items-center gap-4 w-full">
      {!compact && (
        <>
          {/* Modules row */}
          <div className="flex items-end justify-center gap-12">
            <SyringeModule
              label="Left"
              mode="left"
              selected={selected}
              fillLevel={0.75}
              onSelect={onSelect}
            />
            <SyringeModule
              label="Right"
              mode="right"
              selected={selected}
              fillLevel={0.35}
              onSelect={onSelect}
            />
          </div>

          {/* Platform rail */}
          <div className="flex items-center gap-3 mt-1">
            <div
              className="h-0.5 w-32 rounded-full bg-border-strong"
            />
          </div>
        </>
      )}

      {/* Mode indicator pills + hint — always visible */}
      <div className="flex flex-col items-center gap-2">
        <div className="flex gap-2">
          {(["left", "right", "both"] as SyringeMode[]).map((m) => (
            <button
              key={m}
              onClick={() => onSelect(m)}
              className={`px-3 py-1 rounded-full text-[10px] font-semibold tracking-wide uppercase transition-all ${
                selected === m
                  ? "bg-primary text-white"
                  : "bg-border text-text-muted"
              }`}
            >
              {m}
            </button>
          ))}
        </div>
        <p className="text-center text-xs leading-relaxed text-text-muted">
          Tap to toggle{" "}
          <span className="font-semibold text-text">
            dual-ink mode
          </span>{" "}
          or single extrusion.
        </p>
      </div>
    </div>
  );
}

// Keep backward-compatible export
export function SyringeSelector({
  selected,
  onSelect,
}: {
  selected: SyringeMode;
  onSelect: (m: SyringeMode) => void;
}): React.JSX.Element {
  return <SyringeModuleViz selected={selected} onSelect={onSelect} />;
}
