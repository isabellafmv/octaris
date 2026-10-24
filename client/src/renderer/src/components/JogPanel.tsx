import { useState } from 'react'
import { api } from '../api'
import { usePrinter } from '../stores/printer'
import { usePrintSettings } from '../stores/printSettings'
import type { NozzleSpec } from '../types'
import {
  MAX_FEED,
  MAX_STEP,
  MIN_FEED,
  MIN_STEP,
  jogNozzles,
  parseFeed,
  parseStep,
  toggleJogNozzle
} from './jogControls'

interface JogPanelProps {
  disabled: boolean
}

const STEPS: number[] = [0.1, 1, 5]

function ArrowButton({
  label,
  axisLabel,
  axes,
  direction,
  step,
  feed,
  disabled,
  children
}: {
  label: string
  axisLabel: string
  axes: string[]
  direction: 1 | -1
  step: number
  // mm/min
  feed: number
  disabled: boolean
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <button
      onClick={() => !disabled && api.jog(axes, step * direction, feed)}
      disabled={disabled}
      aria-label={label}
      className="w-16 h-16 rounded-full flex flex-col items-center justify-center gap-0.5 text-lg font-light transition-all active:scale-90 disabled:opacity-30 bg-surface-sunken text-text"
    >
      {children}
      <span className="text-[10px] font-semibold tracking-wider text-text-muted">{axisLabel}</span>
    </button>
  )
}

// One up/down column for the axes of the syringes chosen with its chips
// (see jogControls.ts): a press moves them all together, in one G1 block.
// `axisOf` picks each syringe's axis: its height or its plunger.
function AxisColumn({
  nozzles,
  chosen,
  onChoose,
  axisOf,
  caption,
  upName,
  downName,
  upLabel,
  downLabel,
  lastChipTitle,
  step,
  feed,
  disabled
}: {
  nozzles: NozzleSpec[]
  // Ids of the chosen syringes, left to right
  chosen: string[]
  onChoose: (ids: string[]) => void
  axisOf: (n: NozzleSpec) => string
  caption: string
  // What the buttons do, for screen readers ("Up")
  upName: string
  downName: string
  // What the buttons say, from the axes they move ("Z+")
  upLabel: (axes: string[]) => string
  downLabel: (axes: string[]) => string
  lastChipTitle: string
  step: number
  feed: number
  disabled: boolean
}): React.JSX.Element {
  const axes = nozzles.filter((n) => chosen.includes(n.id)).map(axisOf)
  const label = axes.join(' ')
  return (
    <div className="flex flex-col items-center gap-2">
      <div className="flex flex-wrap justify-center gap-1 max-w-[9rem]">
        {nozzles.map((n) => {
          const active = chosen.includes(n.id)
          return (
            <button
              key={n.id}
              onClick={() => onChoose(toggleJogNozzle(chosen, n.id))}
              aria-pressed={active}
              title={active && chosen.length === 1 ? lastChipTitle : undefined}
              className={`px-2 py-0.5 rounded-full text-[10px] font-semibold transition-all ${
                active ? 'bg-primary text-white' : 'bg-surface-sunken text-text-muted'
              }`}
            >
              {axisOf(n)} <span className="font-normal lowercase">{n.name}</span>
            </button>
          )
        })}
      </div>
      <div className="flex flex-col items-center gap-2">
        <ArrowButton
          label={`${upName}: ${label}`}
          axisLabel={upLabel(axes)}
          axes={axes}
          direction={1}
          step={step}
          feed={feed}
          disabled={disabled}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="w-6 h-6"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
          </svg>
        </ArrowButton>

        <div className="w-16 h-16 rounded-full flex flex-col items-center justify-center text-text-muted">
          <span className="text-xs font-semibold tracking-widest text-center leading-tight">
            {label}
          </span>
          <span className="text-[9px] lowercase">{caption}</span>
        </div>

        <ArrowButton
          label={`${downName}: ${label}`}
          axisLabel={downLabel(axes)}
          axes={axes}
          direction={-1}
          step={step}
          feed={feed}
          disabled={disabled}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            className="w-6 h-6"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
          </svg>
        </ArrowButton>
      </div>
    </div>
  )
}

// "Z+" for one or two height axes, "3+" for more
const heightLabel =
  (sign: '+' | '-') =>
  (axes: string[]): string =>
    axes.length > 2 ? `${axes.length}${sign}` : `${axes.join('')}${sign}`

// A feed rate in mm/min, kept in the store (for the session) while valid
function FeedField({
  label,
  feed,
  onFeed,
  onValid
}: {
  label: string
  feed: number
  onFeed: (feed: number) => void
  // Whether the typed text is a feed rate, so the buttons using it can be disabled
  onValid: (valid: boolean) => void
}): React.JSX.Element {
  const [text, setText] = useState(() => String(feed))
  const invalid = Number.isNaN(parseFeed(text))
  return (
    <label className="flex items-center gap-1.5 text-[10px] font-semibold tracking-widest uppercase text-text-muted">
      {label}
      <span className="flex items-center gap-1 px-2 py-1 rounded-md bg-surface-sunken text-xs normal-case tracking-normal">
        <input
          type="number"
          min={MIN_FEED}
          max={MAX_FEED}
          step="1"
          value={text}
          onChange={(e) => {
            setText(e.target.value)
            const value = parseFeed(e.target.value)
            onValid(!Number.isNaN(value))
            if (!Number.isNaN(value)) onFeed(value)
          }}
          aria-label={`${label} in mm/min`}
          aria-invalid={invalid}
          className={`w-12 bg-transparent outline-none text-right ${invalid ? 'text-danger' : 'text-text'}`}
        />
        <span className="text-text-muted">mm/min</span>
      </span>
    </label>
  )
}

export function JogPanel({ disabled: panelDisabled }: JogPanelProps): React.JSX.Element {
  const profile = usePrinter((s) => s.profile)
  const step = usePrintSettings((s) => s.jogStep)
  const setStep = usePrintSettings((s) => s.setJogStep)
  const xyFeed = usePrintSettings((s) => s.jogXyFeed)
  const setXyFeed = usePrintSettings((s) => s.setJogXyFeed)
  const zFeed = usePrintSettings((s) => s.jogZFeed)
  const setZFeed = usePrintSettings((s) => s.setJogZFeed)
  const printSelection = usePrintSettings((s) => s.selectedNozzles)
  const manualHeights = usePrintSettings((s) => s.jogHeightNozzles)
  const setManualHeights = usePrintSettings((s) => s.setJogHeightNozzles)
  const manualPlungers = usePrintSettings((s) => s.jogPlungerNozzles)
  const setManualPlungers = usePrintSettings((s) => s.setJogPlungerNozzles)
  const nozzles = profile?.nozzles ?? []
  const chosenHeights = jogNozzles(nozzles, printSelection, manualHeights).map((n) => n.id)
  const chosenPlungers = jogNozzles(nozzles, printSelection, manualPlungers).map((n) => n.id)
  // Raw text of the custom field; pre-filled when the stored step is a custom one
  const [customText, setCustomText] = useState(() => (STEPS.includes(step) ? '' : String(step)))
  // Whether the feed fields hold a feed rate (the store keeps the last valid one)
  const [xyFeedValid, setXyFeedValid] = useState(true)
  const [zFeedValid, setZFeedValid] = useState(true)

  const customValue = parseStep(customText)
  const customValid = !Number.isNaN(customValue)
  const customInvalid = customText !== '' && !customValid
  const customActive = customText !== '' && customValid && step === customValue
  // Don't jog by a stale step or feed rate while a field holds a bad value
  const disabled = panelDisabled || customInvalid
  const xyDisabled = disabled || !xyFeedValid
  const zDisabled = disabled || !zFeedValid

  const handleCustomChange = (text: string): void => {
    setCustomText(text)
    const value = parseStep(text)
    if (!Number.isNaN(value)) setStep(value)
  }

  return (
    <div className="flex flex-col items-center gap-3">
      {/* Step size selector */}
      <div className="flex flex-col items-center gap-1">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold tracking-widest uppercase text-text-muted">
            Step
          </span>
          <div className="flex rounded-lg p-0.5 bg-surface-sunken">
            {STEPS.map((s) => (
              <button
                key={s}
                onClick={() => {
                  setStep(s)
                  setCustomText('')
                }}
                className={`px-3 py-1 rounded-md text-xs font-semibold transition-all ${
                  step === s && !customActive
                    ? 'bg-white text-primary shadow-[0_1px_3px_rgba(0,0,0,0.08)]'
                    : 'text-text-muted'
                }`}
              >
                {s} mm
              </button>
            ))}
            <label
              className={`flex items-center gap-1 px-2 rounded-md text-xs font-semibold transition-all ${
                customActive
                  ? 'bg-white text-primary shadow-[0_1px_3px_rgba(0,0,0,0.08)]'
                  : 'text-text-muted'
              }`}
            >
              <input
                type="number"
                min={MIN_STEP}
                max={MAX_STEP}
                step="0.01"
                placeholder="custom"
                value={customText}
                onChange={(e) => handleCustomChange(e.target.value)}
                aria-label="Custom step size in mm"
                aria-invalid={customInvalid}
                className={`w-16 bg-transparent outline-none text-right placeholder:text-text-subtle placeholder:font-normal ${
                  customInvalid ? 'text-danger' : ''
                }`}
              />
              mm
            </label>
          </div>
        </div>
        {customInvalid && (
          <p className="text-[10px] text-danger">
            Step must be between {MIN_STEP} and {MAX_STEP} mm
          </p>
        )}
      </div>

      {/* Feed rates: X/Y, and the heights and plungers (Go to Origin uses them too) */}
      <div className="flex flex-col items-center gap-1">
        <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
          <FeedField label="XY feed" feed={xyFeed} onFeed={setXyFeed} onValid={setXyFeedValid} />
          <FeedField
            label="Z / extruder feed"
            feed={zFeed}
            onFeed={setZFeed}
            onValid={setZFeedValid}
          />
        </div>
        {(!xyFeedValid || !zFeedValid) && (
          <p className="text-[10px] text-danger">
            Feed must be between {MIN_FEED} and {MAX_FEED} mm/min
          </p>
        )}
      </div>

      <div className="flex flex-wrap gap-4 items-center justify-center">
        {/* XY cross */}
        <div className="flex flex-col items-center gap-2">
          <ArrowButton
            label="Y+"
            axisLabel="Y+"
            axes={['Y']}
            direction={1}
            step={step}
            feed={xyFeed}
            disabled={xyDisabled}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="w-6 h-6"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
            </svg>
          </ArrowButton>

          <div className="flex items-center gap-2">
            <ArrowButton
              label="X-"
              axisLabel="X-"
              axes={['X']}
              direction={-1}
              step={step}
              feed={xyFeed}
              disabled={xyDisabled}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="w-6 h-6"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M15.75 19.5L8.25 12l7.5-7.5"
                />
              </svg>
            </ArrowButton>

            <div className="w-16 h-16 rounded-full flex items-center justify-center text-xs font-semibold tracking-widest text-text-muted">
              XY
            </div>

            <ArrowButton
              label="X+"
              axisLabel="X+"
              axes={['X']}
              direction={1}
              step={step}
              feed={xyFeed}
              disabled={xyDisabled}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="w-6 h-6"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
              </svg>
            </ArrowButton>
          </div>

          <ArrowButton
            label="Y-"
            axisLabel="Y-"
            axes={['Y']}
            direction={-1}
            step={step}
            feed={xyFeed}
            disabled={xyDisabled}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              className="w-6 h-6"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
            </svg>
          </ArrowButton>
        </div>

        {/* The height axes and the plungers to move, each chosen with its
            chips: the syringes selected for printing until one is tapped */}
        {nozzles.length > 0 && (
          <>
            <AxisColumn
              nozzles={nozzles}
              chosen={chosenHeights}
              onChoose={setManualHeights}
              axisOf={(n) => n.height_axis}
              caption="height"
              upName="Up"
              downName="Down"
              upLabel={heightLabel('+')}
              downLabel={heightLabel('-')}
              lastChipTitle="At least one height moves"
              step={step}
              feed={zFeed}
              disabled={zDisabled}
            />
            {/* Plungers extrude in the negative direction: up retracts */}
            <AxisColumn
              nozzles={nozzles}
              chosen={chosenPlungers}
              onChoose={setManualPlungers}
              axisOf={(n) => n.plunger_axis}
              caption="plunger"
              upName="Retract"
              downName="Extrude"
              upLabel={() => 'Retract'}
              downLabel={() => 'Extrude'}
              lastChipTitle="At least one plunger moves"
              step={step}
              feed={zFeed}
              disabled={zDisabled}
            />
          </>
        )}
      </div>
    </div>
  )
}
