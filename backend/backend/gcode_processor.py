from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from backend.printer import Axes, NozzleSpec

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
# Highest feed rate sent to the printer (mm/min): the default for
# max_feed_mm_min in config.json, which is what the app uses
MAX_FEED = 400.0

# Each nozzle has its own height motor and plunger, and its own place on
# the head: see printer.py. The processing steps take the nozzles a print
# uses (a profile's selection, left to right): X/Y are zeroed at the first,
# so a print isn't shifted by the nozzles' offsets, and several nozzles all
# print the same path, each in place. The offsets only apply to the second
# nozzle's sections (T1) of a two-material 3MF, see substitute_extrusion.


class GcodeValidationError(Exception):
    pass


@dataclass(frozen=True)
class MachineState:
    """Logical machine state (what M114 reports) between two G-code lines.

    `pos` has the axes that are tracked: a printer profile's (Axes.all).
    An axis is None until the program sets it with an absolute move or G92;
    before that its position depends on where the printer happened to be.
    """

    pos: dict[str, float | None]
    relative: bool = False  # G91 in effect
    feed: float | None = None  # modal F

    @classmethod
    def unknown(cls, axes: Iterable[str], relative: bool = False) -> MachineState:
        """Every one of `axes` unknown."""
        return cls(pos=dict.fromkeys(axes), relative=relative)


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
    # The height motors of the nozzle(s) the print uses: what lifts and
    # lowers on a resume or a return after a pause.
    height_axes: tuple[str, ...] = ()
    # Where the relative moves assume the print starts: the zero point, for
    # a converted file, which the head travels back to if it isn't there.
    # None: wherever the head is (a lab G91 file, sent as it is).
    start_position: dict[str, float] | None = None
    # What a file sent as uploaded may do differently than intended
    warnings: list[str] = field(default_factory=list)


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
    line by line, as it's sent (see QueueWorker._track_send). Only the axes
    of `state` are tracked.
    """
    words = parse_words(line)
    if not words or words[0][0] != "G":
        return state

    code = words[0][1]
    params = dict(words[1:])
    pos = dict(state.pos)

    if code in (0, 1):
        for ax in state.pos:
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
        for ax in state.pos:
            if ax in params:
                pos[ax] = params[ax]
        return MachineState(pos, state.relative, state.feed)
    if code == 28:
        # Homed axes end up at the home position, which we don't know
        homed = [ax for ax in state.pos if ax in params] or list(state.pos)
        for ax in homed:
            pos[ax] = None
        return MachineState(pos, state.relative, state.feed)
    return state


def simulate_states(lines: list[str], start: MachineState) -> tuple[list[MachineState], list[MachineState]]:
    """Track G90/G91, G92, F and the positions of `start`'s axes through
    `lines`, from `start`."""
    state = start
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
        return self.letter + (self.text if self.text is not None else _number(self.value))


def _number(value: float) -> str:
    """A computed value as G-code text: fixed point (Marlin reads no
    exponents), to 6 decimals, more than any axis is written with."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


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


Nozzles = Sequence[NozzleSpec]


def _extrusion_axes(nozzles: Nozzles) -> list[str]:
    """The plungers of the nozzles in use."""
    return [n.plunger_axis for n in nozzles]


def _z_axis(nozzles: Nozzles) -> str:
    """The height axis that marks a layer change: the first nozzle's. With
    several nozzles every move carries all their heights together (see
    map_height_axes)."""
    return nozzles[0].height_axis


def height_axes(nozzles: Nozzles) -> tuple[str, ...]:
    """The height motors of the nozzles in use."""
    return tuple(n.height_axis for n in nozzles)


def trim_to_print(cmds: list[Command]) -> list[Command]:
    """Drop the slicer's start and end code: everything before the first
    travel move (G0) and everything after the last move of X, Y or Z.

    The start is the first G0 rather than the first move because start code
    often moves with G1 (a Z lift, a purge line, a filament prime). Our Cura
    profile's machine_start_gcode and machine_end_gcode are empty, but an
    uploaded .gcode may come from any profile.

    The positioning (G90/G91) and extrusion (M82/M83) modes in effect at the
    start are kept, at the front: Cura writes its M83 before the first G0.
    Without them, Marlin's power-on defaults (G90, M82) apply.
    """
    first = next((i for i, c in enumerate(cmds) if c.code == "G0"), 0)
    last = max(
        (i for i, c in enumerate(cmds) if c.is_move and any(c.has(a) for a in "XYZ")),
        default=len(cmds) - 1,
    )
    positioning, extrusion = Command("G90"), Command("M82")
    for cmd in cmds[:first]:
        if cmd.code in ("G90", "G91"):
            positioning = cmd
        elif cmd.code in ("M82", "M83"):
            extrusion = cmd
    return [positioning, extrusion, *cmds[first : max(first, last) + 1]]


# The tool changes of a two-material 3MF, sliced with one extruder per nozzle
_TOOLS = ("T0", "T1")


def substitute_extrusion(cmds: list[Command], nozzles: Nozzles) -> list[Command]:
    """E → the nozzle's plunger axis, negated: the plungers extrude in the
    negative direction. X/Y are zeroed at the first nozzle, so its moves
    are not shifted.

    With two nozzles, a file with T0/T1 tool changes (multi-material 3MF)
    sends each section to its own nozzle; the second nozzle's (T1) moves
    are shifted by its offset from the first. Otherwise (a single STL, or
    any other number of nozzles), every E drives every nozzle's plunger, so
    all the syringes print the same path.
    """
    plungers = _extrusion_axes(nozzles)
    two_tools = len(nozzles) == 2 and any(c.code in _TOOLS for c in cmds)
    if len(nozzles) > 1 and not two_tools:
        return [_mirror_e(c, plungers) for c in cmds]

    first, second = nozzles[0], nozzles[-1]
    dx, dy = second.offset_x - first.offset_x, second.offset_y - first.offset_y
    tool = "T0"
    result = []
    for cmd in cmds:
        if two_tools and cmd.code in _TOOLS:
            tool = cmd.code
        else:
            cmd = _rename_e(cmd, (second if tool == "T1" else first).plunger_axis)
            if tool == "T1" and cmd.is_move:
                cmd = cmd.map_axis("X", lambda x: x + dx)
                if dy:
                    cmd = cmd.map_axis("Y", lambda y: y + dy)
        result.append(cmd)
    return result


def map_height_axes(cmds: list[Command], nozzles: Nozzles) -> list[Command]:
    """Cura writes one height axis, Z; each nozzle has its own motor. Every
    Z becomes the nozzles' height axes, with the same value, so they all
    rise together (a lone Z nozzle keeps the file as it is). G92 is mapped
    the same way, so the coordinates stay consistent. Runs before E is
    substituted, so only Cura's Z is mapped, even when a profile's plunger
    is called Z."""
    heights = height_axes(nozzles)
    if heights == ("Z",):
        return cmds
    result = []
    for cmd in cmds:
        if cmd.has("Z"):
            words: list[Word] = []
            for w in cmd.words:
                if w.letter != "Z":
                    words.append(w)
                else:
                    words += [w if axis == "Z" else Word(axis, w.value, w.text) for axis in heights]
            cmd = cmd.with_words(words)
        result.append(cmd)
    return result


def mirror_to_nozzles(cmds: list[Command], nozzles: Nozzles) -> tuple[list[Command], str | None]:
    """Several nozzles, and a file written for one of them (it drives one
    of their plungers, not several): copy that plunger's words to the other
    nozzles' plungers on every move and G92, and the height words of the
    one height axis it moves to the other nozzles' heights, so all the
    nozzles print the same path. Heights the file already moves on more
    than one nozzle are left alone.

    Returns the commands and a note saying what was copied, or the commands
    unchanged and None for a file that isn't for one nozzle.
    """
    used = {w.letter for c in cmds if c.is_move or c.code == "G92" for w in c.words}
    plungers, heights = _extrusion_axes(nozzles), height_axes(nozzles)
    pushed = [ax for ax in plungers if ax in used]
    if len(nozzles) < 2 or len(pushed) != 1:
        return cmds, None  # drives several plungers already, or none
    copies = {pushed[0]: [ax for ax in plungers if ax != pushed[0]]}
    lifted = [ax for ax in heights if ax in used]
    if len(lifted) == 1:
        copies[lifted[0]] = [ax for ax in heights if ax != lifted[0]]

    result = []
    for cmd in cmds:
        if (cmd.is_move or cmd.code == "G92") and any(cmd.has(a) for a in copies):
            words: list[Word] = []
            for w in cmd.words:
                words.append(w)
                words += [Word(axis, w.value, w.text) for axis in copies.get(w.letter, ())]
            cmd = cmd.with_words(words)
        result.append(cmd)
    copied = " and ".join(f"{a} to {', '.join(targets)}" for a, targets in copies.items())
    every = "both nozzles" if len(nozzles) == 2 else f"all {len(nozzles)} nozzles"
    return result, f"Copied {copied} on every line, so {every} print the same path"


def _rename_e(cmd: Command, axis: str) -> Command:
    if not cmd.has("E"):
        return cmd
    return cmd.with_words(Word(axis, -w.value) if w.letter == "E" else w for w in cmd.words)


def _mirror_e(cmd: Command, plungers: Sequence[str]) -> Command:
    """E → the first plunger, in place, and the same push added for the others."""
    e = cmd.get("E")
    if e is None:
        return cmd
    renamed = _rename_e(cmd, plungers[0])
    return renamed.with_words([*renamed.words, *(Word(ax, -e) for ax in plungers[1:])])


def apply_flow_multiplier(cmds: list[Command], multiplier: float, plungers: Sequence[str]) -> list[Command]:
    """Scale the `plungers`' extrusion values by the given multiplier."""
    if multiplier == 1.0:
        return cmds
    return [cmd.map_axis("".join(plungers), lambda v: v * multiplier) for cmd in cmds]


def scale_flow(line: str, multiplier: float, plungers: Sequence[str]) -> str:
    """The runtime flow override, applied to each line as it is sent: a
    move's distances on `plungers` (the profile's) are scaled (the print is
    relative, so each plunger value is the distance that move pushes).
    Other commands are left alone."""
    if multiplier == 1.0:
        return line
    cmd = parse_line(line)
    if not cmd.is_move:
        return line
    return render_line(cmd.map_axis("".join(plungers), lambda v: v * multiplier))


def clamp_feed_rates(cmds: list[Command], max_f: float = MAX_FEED) -> tuple[list[Command], list[str]]:
    result = []
    log_entries = []
    for i, cmd in enumerate(cmds):
        for w in cmd.words:
            if w.letter == "F" and w.value > max_f:
                log_entries.append(f"Line {i + 1}: F{w.value:g} clamped to F{max_f:g}")
        result.append(cmd.map_axis("F", lambda f: min(f, max_f)))
    return result, log_entries


# Retraction is inserted here rather than with Cura's retraction settings
# (retraction_enable stays off in octaris_settings.json):
# - uploaded .gcode files never pass through Cura, and still need it;
# - Cura has one retraction_amount, while layer changes pull back
#   pressurize_mm and in-layer travel pressurize_mm × travel_retract_multiplier;
# - Cura skips retraction on combed or short travel, and writes its retracts
#   as E moves, which the flow multiplier would then scale.


def _plunger_moves(axes: list[str], pull_back_mm: float, label: str = "") -> list[Command]:
    """Move each plunger `pull_back_mm` away from its extrude direction
    (negative pushes), as one relative G91 … G90 block inside the slicer's
    absolute moves. to_relative turns the whole program relative later, so
    the printer never sees the G90."""
    moves = [
        f"G1 {ax}{pull_back_mm:g} F{PRESSURIZE_FEED}" + (f" ; {label} {ax}" if label else "") for ax in axes
    ]
    return parse(["G91", *moves, "G90"])


def insert_layer_depressurize(
    cmds: list[Command], nozzles: Nozzles, pressurize_mm: float = PRESSURIZE_MM
) -> list[Command]:
    """Wrap layer-change travel moves (G0 with the first nozzle's height
    axis, see _z_axis) with depressurize/repressurize.

    Skips the very first G0-with-Z since that's the initial positioning before
    any extrusion has happened (the preamble handles the first pressurization).
    """
    axes = _extrusion_axes(nozzles)
    z_axis = _z_axis(nozzles)
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
    cmds: list[Command], nozzles: Nozzles, retract_mm: float = PRESSURIZE_MM
) -> list[Command]:
    """Wrap in-layer G0 travel moves with retract/prime to prevent oozing.

    Retracts before the first G0 of a run of travel moves and primes before
    the next command that isn't one, so material doesn't string between
    print segments (e.g. star corners). G0 moves with a height axis are
    layer changes, handled by insert_layer_depressurize.
    """
    axes = _extrusion_axes(nozzles)
    z_axis = _z_axis(nozzles)
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


def build_preamble(nozzles: Nozzles, pressurize_mm: float = PRESSURIZE_MM) -> list[str]:
    """G91 relative positioning + syringe pressurization.

    The G92 lets an absolute-extrusion (M82) file count from the pressurized
    plunger; to_relative takes it into account and drops it.
    """
    lines = ["; Octaris — preamble", "G91"]
    for ax in _extrusion_axes(nozzles):
        lines.append(f"G1 {ax}{-pressurize_mm:g} F{PRESSURIZE_FEED} ; pressurize {ax}")
        lines.append(f"G92 {ax}0 ; reset after pressurization")
    return lines


def build_footer(nozzles: Nozzles, pressurize_mm: float = PRESSURIZE_MM) -> list[str]:
    """Depressurize syringe(s), raise nozzle, return to origin. The return
    is written as an absolute move, which to_relative turns into the
    distance back from wherever the print ended."""
    lines = ["; Octaris — footer", "G91"]
    for ax in _extrusion_axes(nozzles):
        lines.append(f"G1 {ax}{pressurize_mm:g} F{PRESSURIZE_FEED} ; depressurize {ax}")
    raise_ = " ".join(f"{ax}{CLEARANCE_Z_MM}" for ax in height_axes(nozzles))
    lines.extend(
        [
            f"G1 {raise_} F{TRAVEL_FEED} ; raise nozzle",
            "G90",
            f"G1 X0 Y0 F{TRAVEL_FEED} ; return to origin",
        ]
    )
    return lines


def add_preamble_and_footer(
    cmds: list[Command], nozzles: Nozzles, pressurize_mm: float = PRESSURIZE_MM
) -> list[Command]:
    return parse(build_preamble(nozzles, pressurize_mm)) + cmds + parse(build_footer(nozzles, pressurize_mm))


def start_position(cmds: list[Command], axes: Axes) -> dict[str, float]:
    """Where a print converted by to_relative starts: the zero point, on the
    stage axes (X, Y, `axes`' heights) the absolute program puts at a
    coordinate."""
    relative = False
    moved: set[str] = set()
    for cmd in cmds:
        if cmd.code in ("G90", "G91"):
            relative = cmd.code == "G91"
        elif cmd.is_move and not relative:
            moved.update(w.letter for w in cmd.words if w.letter in axes.stage)
    return {ax: 0.0 for ax in axes.all if ax in moved}


def _to_units(axes: Axes, axis: str, value: float) -> int:
    return round(value * 10 ** axes.decimals(axis))


def _units_text(axes: Axes, axis: str, units: int) -> str:
    """`units` (of 10^-decimals mm) as exact decimal text, e.g. -1205 on X: "-1.205"."""
    decimals = axes.decimals(axis)
    whole, frac = divmod(abs(units), 10**decimals)
    digits = f"{frac:0{decimals}d}".rstrip("0")
    return ("-" if units < 0 else "") + str(whole) + (f".{digits}" if digits else "")


def to_relative(cmds: list[Command], axes: Axes, start: Mapping[str, float] | None = None) -> list[Command]:
    """Write every move as a relative distance, behind a single G91.

    Reads the program as the printer would: G90/G91 set the positioning
    mode, M82/M83 that of the plungers (which carry the slicer's E: G91 makes
    them relative too, G90 leaves them as M82/M83 set them), and G92 moves
    the coordinates. All of these are taken into account and dropped, so
    nothing after the G91 switches back to absolute (or renumbers an axis).

    Each axis of `axes` has its absolute target rounded to Axes.decimals
    and kept as an integer count of those units; a move is (rounded target − rounded
    previous target). The distances so add up exactly to the rounded
    targets, and rounding never accumulates over a long print.

    `start` is where the print starts, 0 on any axis it leaves out: the
    zero point the absolute program's coordinates are measured from.
    """
    letters = axes.all
    pos = {ax: 0.0 for ax in letters} | dict(start or {})  # exact, in the printer's coordinates
    shift = {ax: 0.0 for ax in letters}  # printer minus program coordinate, set by G92
    units = {ax: _to_units(axes, ax, pos[ax]) for ax in letters}  # rounded position written so far
    relative = e_relative = False
    result = [parse_line("G91 ; relative positioning for the whole print")]

    for cmd in cmds:
        if cmd.code in ("G90", "G91"):
            relative = cmd.code == "G91"
        elif cmd.code in ("M82", "M83"):
            e_relative = cmd.code == "M83"
        elif cmd.code == "G92":
            for w in cmd.words:
                if w.letter in letters:
                    shift[w.letter] = pos[w.letter] - w.value
        elif cmd.code == "G28":
            raise GcodeValidationError(
                f"Homing ({render_line(cmd).strip()}) inside the print can't be turned into relative moves"
            )
        elif cmd.is_move:
            words = []
            for w in cmd.words:
                ax = w.letter
                if ax not in letters:
                    words.append(w)
                    continue
                if relative or (e_relative and ax in axes.plungers):
                    pos[ax] += w.value
                else:
                    pos[ax] = w.value + shift[ax]
                target = _to_units(axes, ax, pos[ax])
                delta, units[ax] = target - units[ax], target
                if delta:
                    words.append(Word(ax, delta / 10 ** axes.decimals(ax), _units_text(axes, ax, delta)))
            if words:
                result.append(cmd.with_words(words))
            elif cmd.comment:
                result.append(Command("", comment=cmd.comment))
        else:
            result.append(cmd)
    return result


def validate(cmds: list[Command], max_feed: float = MAX_FEED) -> None:
    numbered = [(i, cmd) for i, cmd in enumerate(cmds) if cmd.code]
    if not numbered:
        raise GcodeValidationError("Empty G-code")

    if numbered[0][1].code != "G91":
        raise GcodeValidationError("G-code must start with G91 relative positioning")

    for i, cmd in numbered:
        if cmd.code in ("G90", "G53"):
            raise GcodeValidationError(
                f"Line {i + 1}: {cmd.code} would make the following moves absolute; "
                f"the print must stay relative (G91)"
            )
        if cmd.has("E"):
            raise GcodeValidationError(
                f"Line {i + 1}: Unsubstituted E command found: {render_line(cmd).strip()}"
            )
        for w in cmd.words:
            if w.letter == "F" and w.value > max_feed:
                raise GcodeValidationError(f"Line {i + 1}: F value {str(w)[1:]} exceeds {max_feed:g}")


def check_as_uploaded(cmds: list[Command], axes: Axes, max_feed: float = MAX_FEED) -> list[str]:
    """Check a file that is sent exactly as uploaded to a printer with
    `axes`: raise GcodeValidationError if there is nothing to print, and
    return warnings for what would print differently than intended
    (nothing is changed)."""
    numbered = [(i, cmd) for i, cmd in enumerate(cmds) if cmd.code]
    if not numbered:
        raise GcodeValidationError("Empty G-code")
    if not any(cmd.is_move and any(cmd.has(ax) for ax in axes.all) for _, cmd in numbered):
        raise GcodeValidationError("The file has no motion commands (G0/G1 moves)")

    warnings = []
    fast = [(i, cmd.get("F")) for i, cmd in numbered if (cmd.get("F") or 0) > max_feed]
    if fast:
        top = max(f for _, f in fast if f is not None)
        warnings.append(
            f"{len(fast)} line(s) set a feed rate above the {max_feed:g} mm/min limit "
            f"(first on line {fast[0][0] + 1}, up to F{top:g}); they are sent unchanged"
        )
    with_e = [i for i, cmd in numbered if cmd.has("E")]
    if with_e:
        plungers = "the plungers are" if len(axes.plungers) > 1 else "the plunger is"
        warnings.append(
            f"{len(with_e)} line(s) use E (first on line {with_e[0] + 1}); {plungers} "
            f'{axes.describe_plungers()}, so E moves no syringe. Choose "Needs changes" to convert them'
        )
    first_move = next((i for i, cmd in numbered if cmd.is_move), len(cmds))
    if not any(cmd.code == "G91" for i, cmd in numbered if i < first_move):
        warnings.append(
            "No G91 before the first move: this is an absolute file, so its moves go to "
            "coordinates from the zero point instead of distances from where the head is"
        )
    else:
        absolute = [i for i, cmd in numbered if cmd.code in ("G90", "G53")]
        if absolute:
            line = absolute[0]
            warnings.append(
                f"Line {line + 1}: {cmds[line].code} switches the print back to absolute positioning"
            )
    return warnings


def is_relative_program(cmds: list[Command]) -> bool:
    """A lab file: relative (G91) before its first move and never absolute
    again, driving the plungers directly (no E)."""
    relative = False
    for cmd in cmds:
        if cmd.code == "G91":
            relative = True
        elif cmd.code in ("G90", "G53") or cmd.has("E") or (cmd.is_move and not relative):
            return False
    return relative


def build_print(
    cmds: list[Command],
    nozzles: Nozzles,
    pressurize_mm: float = PRESSURIZE_MM,
    flow_multiplier: float = 1.0,
    travel_retract_multiplier: float = TRAVEL_RETRACT_MULTIPLIER,
    max_feed: float = MAX_FEED,
) -> tuple[list[Command], list[str]]:
    """The print in the slicer's own (absolute) coordinates, with the
    preamble, footer and retracts added; process_gcode makes it relative.
    Also returns the feed rate clamping log."""
    cmds = trim_to_print(cmds)
    # Cura's Z first: a profile may use the letter Z for a plunger, which E
    # becomes next
    cmds = map_height_axes(cmds, nozzles)
    cmds = substitute_extrusion(cmds, nozzles)
    cmds = apply_flow_multiplier(cmds, flow_multiplier, _extrusion_axes(nozzles))
    cmds = insert_layer_depressurize(cmds, nozzles, pressurize_mm)
    cmds = insert_travel_retract(cmds, nozzles, pressurize_mm * travel_retract_multiplier)
    cmds = add_preamble_and_footer(cmds, nozzles, pressurize_mm)
    # Last, so the pressurize and retract moves added above keep to it too
    return clamp_feed_rates(cmds, max_feed)


def moved_axes(cmds: Iterable[Command], axes: Axes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The plungers and the height motors of `axes` that a file kept as it
    is moves. A file that moves no height motor gets the first nozzle's."""
    moved = {w.letter for c in cmds if c.is_move for w in c.words}
    plungers = tuple(ax for ax in axes.plungers if ax in moved)
    heights = tuple(ax for ax in axes.heights if ax in moved) or axes.heights[:1]
    return plungers, heights


def as_uploaded(
    raw: str, axes: Axes, pressurize_mm: float = PRESSURIZE_MM, warnings: list[str] | None = None
) -> ProcessedGcode:
    """The file exactly as it is, starting wherever the head is: its states
    planned from unknown positions, its plungers and height motors those of
    `axes` (the printer's) it moves. `pressurize_mm` is what an e-stop pulls
    the plungers back."""
    lines = raw.splitlines()
    state_before, state_after = simulate_states(lines, MachineState.unknown(axes.all))
    plungers, heights = moved_axes(parse(lines), axes)
    return ProcessedGcode(
        lines=lines,
        time_estimate_s=extract_time_metadata(raw),
        state_before=state_before,
        state_after=state_after,
        extrusion_axes=plungers,
        pressurize_mm=pressurize_mm,
        height_axes=heights,
        warnings=warnings or [],
    )


def process_gcode(
    raw: str,
    nozzles: Nozzles,
    pressurize_mm: float = PRESSURIZE_MM,
    flow_multiplier: float = 1.0,
    travel_retract_multiplier: float = TRAVEL_RETRACT_MULTIPLIER,
    max_feed: float = MAX_FEED,
    axes: Axes | None = None,
) -> ProcessedGcode:
    """Turn sliced G-code into the relative (G91) program sent to the printer.

    A lab file that is relative already (see is_relative_program) is kept
    exactly as it is, and starts wherever the head is. `axes` are the
    printer profile's (default: the `nozzles`'), which such a file may move.
    """
    axes = axes or Axes.of(nozzles)
    cmds = parse(raw.splitlines())
    if is_relative_program(cmds):
        return as_uploaded(raw, axes, pressurize_mm)

    cmds, feed_log = build_print(
        cmds, nozzles, pressurize_mm, flow_multiplier, travel_retract_multiplier, max_feed
    )
    start = start_position(cmds, axes)
    cmds = to_relative(cmds, axes, start)
    validate(cmds, max_feed)
    lines = render(cmds)
    # The plungers count from 0 at the start, like the other axes the print moves
    planned_start = MachineState(
        pos={ax: start.get(ax, 0.0 if ax in axes.plungers else None) for ax in axes.all}
    )
    state_before, state_after = simulate_states(lines, planned_start)
    return ProcessedGcode(
        lines=lines,
        time_estimate_s=extract_time_metadata(raw),
        feed_log=feed_log,
        state_before=state_before,
        state_after=state_after,
        extrusion_axes=tuple(_extrusion_axes(nozzles)),
        pressurize_mm=pressurize_mm,
        height_axes=height_axes(nozzles),
        start_position=start,
    )
