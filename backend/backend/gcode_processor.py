from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Literal

SyringeMode = Literal["left", "right", "both"]

# Pressurization distance (mm) to prime/deprime the syringe
PRESSURIZE_MM = 0.2
PRESSURIZE_FEED = 400
# Multiplier applied to PRESSURIZE_MM for in-layer travel retract/prime moves.
# Lets travel retraction be tuned (e.g. pulled back harder to stop oozing)
# independently of startup/layer pressurization. 1.0 = same as pressurize.
TRAVEL_RETRACT_MULTIPLIER = 3.0
# Nozzle clearance height (mm) to raise before returning to origin
CLEARANCE_Z_MM = 5
TRAVEL_FEED = 300
# Highest feed rate sent to the printer (mm/min)
MAX_FEED = 400

# Distance between left (B) and right (C) nozzle tips in mm.
# The right nozzle is this far in the +X direction from the left nozzle.
# Always zero/calibrate at the LEFT nozzle — the software applies this offset
# automatically when using the right nozzle or both nozzles.
NOZZLE_OFFSET_X = 31.0  # TODO: measure and update


class GcodeValidationError(Exception):
    pass


AXES = ("X", "Y", "Z", "A", "B", "C")


@dataclass(frozen=True)
class MachineState:
    """Logical machine state (what M114 reports) between two G-code lines.

    An axis is None until the program sets it with an absolute move or G92;
    before that its position depends on where the printer happened to be.
    """

    pos: dict[str, float | None]
    relative: bool = False  # G91 in effect
    feed: float | None = None  # modal F


@dataclass
class ProcessedGcode:
    lines: list[str]
    time_estimate_s: int | None = None
    feed_log: list[str] = field(default_factory=list)
    # state_before[i] / state_after[i]: machine state around lines[i]
    state_before: list[MachineState] = field(default_factory=list)
    state_after: list[MachineState] = field(default_factory=list)
    # Plunger axes this file extrudes with, and its pressurization distance
    extrusion_axes: tuple[str, ...] = ()
    pressurize_mm: float = 0.0


# A letter and its number. The number may lack a leading zero (Cura writes
# "X-.82"), so the digits before the point are optional.
_WORD = re.compile(r"([A-Z])\s*(-?\d*\.?\d+)")


def parse_words(line: str) -> list[tuple[str, float]]:
    """Letter/number words of a G-code line, ignoring any ; comment."""
    code = line.split(";", 1)[0].upper()
    return [(letter, float(num)) for letter, num in _WORD.findall(code)]


def step(state: MachineState, line: str) -> MachineState:
    """Apply one line to `state`, returning the resulting state.

    Pure function of (state, line) — used both to plan a file ahead of time
    (simulate_states) and to track what was actually sent to the printer,
    line by line, as it's sent (see QueueWorker._track_send).
    """
    words = parse_words(line)
    if not words or words[0][0] != "G":
        return state

    code = words[0][1]
    params = dict(words[1:])
    pos = dict(state.pos)

    if code in (0, 1):
        for ax in AXES:
            if ax not in params:
                continue
            if state.relative:
                current = pos[ax]
                pos[ax] = None if current is None else current + params[ax]
            else:
                pos[ax] = params[ax]
        return MachineState(pos, state.relative, params.get("F", state.feed))
    if code == 90:
        return MachineState(pos, False, state.feed)
    if code == 91:
        return MachineState(pos, True, state.feed)
    if code == 92:
        for ax in AXES:
            if ax in params:
                pos[ax] = params[ax]
        return MachineState(pos, state.relative, state.feed)
    if code == 28:
        # Homed axes end up at the home position, which we don't know
        homed = [ax for ax in AXES if ax in params] or list(AXES)
        for ax in homed:
            pos[ax] = None
        return MachineState(pos, state.relative, state.feed)
    return state


def simulate_states(lines: list[str]) -> tuple[list[MachineState], list[MachineState]]:
    """Track G90/G91, G92, F and the X/Y/Z/A/B/C positions through `lines`."""
    state = MachineState(pos={ax: None for ax in AXES})
    before: list[MachineState] = []
    after: list[MachineState] = []

    for line in lines:
        before.append(state)
        state = step(state, line)
        after.append(state)

    return before, after


def extract_time_metadata(raw: str) -> int | None:
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(";TIME:"):
            try:
                return int(float(stripped.split(":", 1)[1]))
            except (ValueError, IndexError):
                pass
    return None


# --- command model ------------------------------------------------------------
#
# process_gcode parses every line once into a Command, runs each step below as
# a function over the list, and writes the text back out in one place
# (render). A command no step changed is written back exactly as it was read.


@dataclass(frozen=True)
class Word:
    letter: str
    value: float
    text: str | None = None  # as written in the file; None for a computed value

    def __str__(self) -> str:
        return self.letter + (self.text if self.text is not None else f"{self.value:g}")


@dataclass(frozen=True)
class Command:
    """One G-code line: its code ("G1", "M82", "T0"; "" for a blank or
    comment-only line), its parameter words in order, and its ; comment."""

    code: str
    words: tuple[Word, ...] = ()
    comment: str = ""
    source: str | None = None  # the line as read, while unchanged

    def get(self, letter: str) -> float | None:
        return next((w.value for w in self.words if w.letter == letter), None)

    def has(self, letter: str) -> bool:
        return any(w.letter == letter for w in self.words)

    @property
    def is_move(self) -> bool:
        return self.code in ("G0", "G1")

    def with_words(self, words: Iterable[Word]) -> Command:
        return replace(self, words=tuple(words), source=None)

    def map_axis(self, letters: str, fn: Callable[[float], float]) -> Command:
        """Replace the value of every word whose letter is in `letters` by
        fn(value). Returns the command itself if no value changes."""
        words = []
        for w in self.words:
            value = fn(w.value) if w.letter in letters else w.value
            words.append(w if value == w.value else Word(w.letter, value))
        return self if words == list(self.words) else self.with_words(words)


def parse_line(line: str) -> Command:
    code_text, sep, comment = line.partition(";")
    words = [Word(letter, float(num), num) for letter, num in _WORD.findall(code_text.upper())]
    comment = (sep + comment).strip()
    if not words:
        return Command("", comment=comment, source=line)
    head, *params = words
    return Command(f"{head.letter}{head.text}", tuple(params), comment, source=line)


def render_line(cmd: Command) -> str:
    if cmd.source is not None:
        return cmd.source
    text = " ".join([cmd.code, *map(str, cmd.words)]) if cmd.code else ""
    if cmd.comment:
        text = f"{text} {cmd.comment}" if text else cmd.comment
    return text


def parse(lines: Iterable[str]) -> list[Command]:
    return [parse_line(line) for line in lines]


def render(cmds: Iterable[Command]) -> list[str]:
    return [render_line(cmd) for cmd in cmds]


# --- steps ----------------------------------------------------------------------


def _extrusion_axes(mode: SyringeMode) -> list[str]:
    """Return the extrusion axis/axes for the given mode."""
    return {"left": ["B"], "right": ["C"], "both": ["B", "C"]}[mode]


def _z_axis(mode: SyringeMode) -> str:
    return {"left": "Z", "right": "A", "both": "Z"}[mode]


def trim_to_print(cmds: list[Command]) -> list[Command]:
    """Drop the slicer's start and end code: everything before the first
    travel move (G0) and everything after the last move of X, Y or Z.

    The start is the first G0 rather than the first move because start code
    often moves with G1 (a Z lift, a purge line, a filament prime). Our Cura
    profile's machine_start_gcode and machine_end_gcode are empty, but an
    uploaded .gcode may come from any profile.
    """
    first = next((i for i, c in enumerate(cmds) if c.code == "G0"), 0)
    last = max(
        (i for i, c in enumerate(cmds) if c.is_move and any(c.has(a) for a in "XYZ")),
        default=len(cmds) - 1,
    )
    return cmds[first : max(first, last) + 1]


def substitute_extrusion(cmds: list[Command], mode: SyringeMode) -> list[Command]:
    """E → plunger axis (B left, C right), negated: the plungers extrude in
    the negative direction. The right nozzle's X is shifted by
    NOZZLE_OFFSET_X, since the zero is always set at the left nozzle.

    In "both" mode, a file with T0/T1 tool changes (multi-material 3MF) sends
    each section to its own nozzle. Without them (single STL), every E drives
    both plungers, so both syringes print the same path.
    """
    if mode == "both" and not any(c.code in ("T0", "T1") for c in cmds):
        return [_mirror_e(c) for c in cmds]

    tool = "T1" if mode == "right" else "T0"
    result = []
    for cmd in cmds:
        if mode == "both" and cmd.code in ("T0", "T1"):
            tool = cmd.code
        else:
            cmd = _rename_e(cmd, "C" if tool == "T1" else "B")
            if tool == "T1" and cmd.is_move:
                cmd = cmd.map_axis("X", lambda x: x + NOZZLE_OFFSET_X)
        result.append(cmd)
    return result


def _rename_e(cmd: Command, axis: str) -> Command:
    if not cmd.has("E"):
        return cmd
    return cmd.with_words(Word(axis, -w.value) if w.letter == "E" else w for w in cmd.words)


def _mirror_e(cmd: Command) -> Command:
    e = cmd.get("E")
    if e is None:
        return cmd
    renamed = _rename_e(cmd, "B")
    return renamed.with_words([*renamed.words, Word("C", -e)])


def apply_flow_multiplier(cmds: list[Command], multiplier: float) -> list[Command]:
    """Scale all B/C extrusion values by the given multiplier."""
    if multiplier == 1.0:
        return cmds
    return [cmd.map_axis("BC", lambda v: v * multiplier) for cmd in cmds]


def scale_flow(line: str, multiplier: float) -> str:
    """apply_flow_multiplier for a single line of text, e.g. the runtime flow
    override applied to each line as it is sent."""
    if multiplier == 1.0:
        return line
    return render_line(parse_line(line).map_axis("BC", lambda v: v * multiplier))


def clamp_feed_rates(cmds: list[Command], max_f: float = MAX_FEED) -> tuple[list[Command], list[str]]:
    result = []
    log_entries = []
    for i, cmd in enumerate(cmds):
        for w in cmd.words:
            if w.letter == "F" and w.value > max_f:
                log_entries.append(f"Line {i + 1}: F{w.value} clamped to F{max_f}")
        result.append(cmd.map_axis("F", lambda f: min(f, max_f)))
    return result, log_entries


# Retraction is inserted here rather than with Cura's retraction settings
# (retraction_enable stays off in octaris_settings.json):
# - uploaded .gcode files never pass through Cura, and still need it;
# - Cura has one retraction_amount, while layer changes pull back
#   pressurize_mm and in-layer travel pressurize_mm × travel_retract_multiplier;
# - Cura skips retraction on combed or short travel, and writes absolute E
#   moves, which the flow multiplier would then scale.


def _plunger_moves(axes: list[str], pull_back_mm: float, label: str = "") -> list[Command]:
    """Move each plunger `pull_back_mm` away from its extrude direction
    (negative pushes), as one relative G91 … G90 block."""
    moves = [
        f"G1 {ax}{pull_back_mm:g} F{PRESSURIZE_FEED}" + (f" ; {label} {ax}" if label else "") for ax in axes
    ]
    return parse(["G91", *moves, "G90"])


def insert_layer_depressurize(
    cmds: list[Command], mode: SyringeMode, pressurize_mm: float = PRESSURIZE_MM
) -> list[Command]:
    """Wrap layer-change travel moves (G0 with Z/A) with depressurize/repressurize.

    Skips the very first G0-with-Z since that's the initial positioning before
    any extrusion has happened (the preamble handles the first pressurization).
    """
    axes = _extrusion_axes(mode)
    z_axis = _z_axis(mode)
    result: list[Command] = []
    seen_first = False
    for cmd in cmds:
        if cmd.code == "G0" and cmd.has(z_axis):
            if seen_first:
                result += [
                    parse_line("; layer change — depressurize"),
                    *_plunger_moves(axes, pressurize_mm),
                    cmd,
                    parse_line("; repressurize"),
                    *_plunger_moves(axes, -pressurize_mm),
                ]
                continue
            seen_first = True
        result.append(cmd)
    return result


def insert_travel_retract(
    cmds: list[Command], mode: SyringeMode, retract_mm: float = PRESSURIZE_MM
) -> list[Command]:
    """Wrap in-layer G0 travel moves with retract/prime to prevent oozing.

    Retracts before the first G0 of a run of travel moves and primes before
    the next command that isn't one, so material doesn't string between
    print segments (e.g. star corners). G0 moves with Z/A are layer changes,
    handled by insert_layer_depressurize.
    """
    axes = _extrusion_axes(mode)
    z_axis = _z_axis(mode)
    result: list[Command] = []
    retracted = False
    for cmd in cmds:
        if cmd.code:
            is_g0 = cmd.code == "G0"
            if is_g0 and not cmd.has(z_axis) and not retracted:
                result += _plunger_moves(axes, retract_mm, "retract")
                retracted = True
            elif retracted and not is_g0:
                result += _plunger_moves(axes, -retract_mm, "prime")
                retracted = False
        result.append(cmd)
    return result


def build_preamble(mode: SyringeMode, pressurize_mm: float = PRESSURIZE_MM) -> list[str]:
    """G90 absolute positioning + syringe pressurization."""
    lines = ["; Octaris — preamble", "G90"]
    for ax in _extrusion_axes(mode):
        lines.append(f"G1 {ax}{-pressurize_mm:g} F{PRESSURIZE_FEED} ; pressurize {ax}")
        lines.append(f"G92 {ax}0 ; reset after pressurization")
    return lines


def build_footer(mode: SyringeMode, pressurize_mm: float = PRESSURIZE_MM) -> list[str]:
    """Depressurize syringe(s), raise nozzle, return to origin."""
    lines = ["; Octaris — footer", "G91"]
    for ax in _extrusion_axes(mode):
        lines.append(f"G1 {ax}{pressurize_mm:g} F{PRESSURIZE_FEED} ; depressurize {ax}")
    lines.extend(
        [
            f"G1 {_z_axis(mode)}{CLEARANCE_Z_MM} F{TRAVEL_FEED} ; raise nozzle",
            "G90",
            f"G1 X0 Y0 F{TRAVEL_FEED} ; return to origin",
        ]
    )
    return lines


def add_preamble_and_footer(
    cmds: list[Command], mode: SyringeMode, pressurize_mm: float = PRESSURIZE_MM
) -> list[Command]:
    return parse(build_preamble(mode, pressurize_mm)) + cmds + parse(build_footer(mode, pressurize_mm))


def validate(cmds: list[Command]) -> None:
    numbered = [(i, cmd) for i, cmd in enumerate(cmds) if cmd.code]
    if not numbered:
        raise GcodeValidationError("Empty G-code")

    if numbered[0][1].code != "G90":
        raise GcodeValidationError("G-code must start with G90 absolute positioning")

    for i, cmd in numbered:
        if cmd.has("E"):
            raise GcodeValidationError(
                f"Line {i + 1}: Unsubstituted E command found: {render_line(cmd).strip()}"
            )
        for w in cmd.words:
            if w.letter == "F" and w.value > MAX_FEED:
                raise GcodeValidationError(f"Line {i + 1}: F value {str(w)[1:]} exceeds {MAX_FEED}")


def process_gcode(
    raw: str,
    mode: SyringeMode,
    pressurize_mm: float = PRESSURIZE_MM,
    flow_multiplier: float = 1.0,
    travel_retract_multiplier: float = TRAVEL_RETRACT_MULTIPLIER,
) -> ProcessedGcode:
    cmds = trim_to_print(parse(raw.splitlines()))
    cmds = substitute_extrusion(cmds, mode)
    cmds = apply_flow_multiplier(cmds, flow_multiplier)
    cmds, feed_log = clamp_feed_rates(cmds)
    cmds = insert_layer_depressurize(cmds, mode, pressurize_mm)
    cmds = insert_travel_retract(cmds, mode, pressurize_mm * travel_retract_multiplier)
    cmds = add_preamble_and_footer(cmds, mode, pressurize_mm)
    validate(cmds)
    lines = render(cmds)
    state_before, state_after = simulate_states(lines)
    return ProcessedGcode(
        lines=lines,
        time_estimate_s=extract_time_metadata(raw),
        feed_log=feed_log,
        state_before=state_before,
        state_after=state_after,
        extrusion_axes=tuple(_extrusion_axes(mode)),
        pressurize_mm=pressurize_mm,
    )
