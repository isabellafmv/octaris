"""Host-side movement limits: the bed box and the syringe plunger travel.

The printer has no endstops, so nothing on the machine stops a move that
leaves the bed or runs a plunger into the end of its syringe. These checks
are the only guard; they work in the coordinate system set by the G92 zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from backend.checkpoint import fmt
from backend.gcode_processor import MachineState, parse_words, step
from backend.printer import Axes, AxisRange, BedLimits, NozzleSpec

# Warn when a plunger has less than this fraction of its travel left
LOW_TRAVEL_FRACTION = 0.10


class LimitError(Exception):
    """A move leaves the bed or empties a syringe. `line` is the 1-based
    line of the program it happens on, if it is one line's."""

    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


@dataclass(frozen=True)
class Copy:
    """One selected nozzle's copy of the print. Several selected nozzles
    move together on one carriage, X/Y zeroed at the first: each prints the
    same path, `dx`/`dy` mm from the first's, and has its own height axis."""

    name: str
    height_axis: str
    dx: float = 0.0
    dy: float = 0.0


def copies_of(nozzles: Sequence[NozzleSpec]) -> tuple[Copy, ...]:
    """The copies `nozzles` (a selection, left to right) print: each one
    shifted by its offset minus the first's."""
    first = nozzles[0]
    return tuple(
        Copy(n.name.lower(), n.height_axis, n.offset_x - first.offset_x, n.offset_y - first.offset_y)
        for n in nozzles
    )


def _ranges(bed: BedLimits, height_axes: Iterable[str]) -> dict[str, AxisRange]:
    """Every nozzle's height motor has the z range."""
    return {"X": bed.x, "Y": bed.y, **dict.fromkeys(height_axes, bed.z)}


def _describe(axis: str, value: float, allowed: AxisRange) -> str:
    return f"{axis} to {fmt(value)} mm, outside the bed ({fmt(allowed.min)} to {fmt(allowed.max)} mm)"


def _mm(value: float) -> str:
    """A length in a message about a nozzle's copy, with a real minus sign."""
    return fmt(value).replace("-", "\u2212")


def _span(allowed: AxisRange) -> str:
    return f"{_mm(allowed.min)} to {_mm(allowed.max)} mm"


def _off_bed(
    ranges: Mapping[str, AxisRange], copies: Sequence[Copy], pos: Mapping[str, float | None]
) -> tuple[Copy | None, str, float, AxisRange] | None:
    """The first copy (left to right) that `pos` (the first copy's
    position) puts off the bed: the copy, the axis, its value and range.
    The copy is None for a height axis no copy has."""
    shifts = {"X": "dx", "Y": "dy"}
    for copy in copies:
        for axis in ("X", "Y", copy.height_axis):
            value, allowed = pos.get(axis), ranges.get(axis)
            if value is None or allowed is None:
                continue
            value += getattr(copy, shifts[axis]) if axis in shifts else 0.0
            if not allowed.min <= value <= allowed.max:
                return copy, axis, value, allowed
    own = {"X", "Y", *(c.height_axis for c in copies)}
    for axis, allowed in ranges.items():
        value = pos.get(axis)
        if axis not in own and value is not None and not allowed.min <= value <= allowed.max:
            return None, axis, value, allowed
    return None


def check_jog(
    bed: BedLimits,
    height_axes: Iterable[str],
    axis: str,
    current: float,
    distance: float,
    copies: Sequence[Copy] = (),
) -> None:
    """Raise LimitError if jogging `axis` by `distance` from `current` leaves
    the bed. `height_axes` (the profile's) have the bed's z range. With
    several `copies` (the selected nozzles), X/Y are checked for each."""
    allowed = _ranges(bed, height_axes).get(axis)
    if allowed is None:
        return
    target = current + distance
    if len(copies) > 1 and axis in ("X", "Y"):
        for copy in copies:
            value = target + (copy.dx if axis == "X" else copy.dy)
            if not allowed.min <= value <= allowed.max:
                raise LimitError(
                    f"This jog would move the {copy.name} syringe to {axis} {_mm(value)} mm, "
                    f"off the bed ({_span(allowed)})"
                )
        return
    if not allowed.min <= target <= allowed.max:
        raise LimitError(f"This jog would move {_describe(axis, target, allowed)}")


def check_move_to(
    bed: BedLimits, height_axes: Iterable[str], axis: str, target: float, copies: Sequence[Copy] = ()
) -> None:
    """Raise LimitError if moving `axis` to the absolute `target` leaves the
    bed. With several `copies` (the selected nozzles), X/Y are checked for
    each."""
    allowed = _ranges(bed, height_axes).get(axis)
    if allowed is None:
        return
    if len(copies) > 1 and axis in ("X", "Y"):
        for copy in copies:
            value = target + (copy.dx if axis == "X" else copy.dy)
            if not allowed.min <= value <= allowed.max:
                raise LimitError(
                    f"This move would take the {copy.name} syringe to {axis} {_mm(value)} mm, "
                    f"off the bed ({_span(allowed)})"
                )
        return
    if not allowed.min <= target <= allowed.max:
        raise LimitError(f"This move would go {_describe(axis, target, allowed)}")


def start_state(position: Mapping[str, float] | None, axes: Iterable[str]) -> MachineState:
    """A MachineState of `axes` (a profile's Axes.all) at `position` (as
    parsed from M114), absolute mode."""
    position = position or {}
    return MachineState(pos={ax: position.get(ax) for ax in axes})


def check_path(
    bed: BedLimits,
    lines: Sequence[str],
    start: MachineState | None,
    height_axes: Sequence[str],
    copies: Sequence[Copy] = (),
) -> None:
    """Raise LimitError if any move in `lines` ends outside the bed.

    X, Y and `height_axes` are checked: the height motors of the nozzles
    the print uses. The other nozzles' heights aren't zeroed for this
    print, so their coordinates mean nothing.

    With several `copies` (the selected nozzles, see copies_of), each
    one's copy of the path is checked too: X/Y shifted by its offset from
    the first, and its own height. The message then names the nozzle.

    Every move is a straight line and the bed is a box, so a path stays on
    the bed if all its end points do. An axis whose position is unknown (a
    relative move before the program or `start` set it) can't be checked;
    pass the printer's current position as `start` to cover those.
    """
    ranges = _ranges(bed, height_axes)
    state = start or MachineState.unknown(ranges)
    several = len(copies) > 1
    for number, line in enumerate(lines, start=1):
        state = step(state, line)
        if several:
            off = _off_bed(ranges, copies, state.pos)
            if off is None:
                continue
            copy, axis, off_value, allowed = off
            who = f"The {copy.name} syringe's copy" if copy is not None else "The print"
            raise LimitError(
                f"{who} leaves the bed: line {number} moves it to {axis} {_mm(off_value)} mm "
                f"(bed {_span(allowed)}). Move the model, select fewer syringes, or check the "
                f"zero point, the syringe offsets and the bed limits (Edit setup on the Setup screen).",
                number,
            )
        for axis, allowed in ranges.items():
            value = state.pos.get(axis)
            if value is not None and not allowed.min <= value <= allowed.max:
                raise LimitError(
                    f"The print leaves the bed: line {number} ({line.strip()}) moves "
                    f"{_describe(axis, value, allowed)}. Move the model, or check the "
                    f"zero point and the bed limits (Edit setup on the Setup screen).",
                    number,
                )


def check_fits_bed(size: tuple[float, float], nozzles: Sequence[NozzleSpec], bed: BedLimits) -> None:
    """Raise LimitError if a model `size` (X, Y footprint in mm) printed by
    each of several `nozzles` (a selection) can't fit on the bed: the
    footprint plus the spread of their offsets is wider than the bed.
    Checked before slicing; where the copies end up is checked after."""
    if len(nozzles) < 2:
        return
    names = [n.name.lower() for n in nozzles]
    syringes = f"{', '.join(names[:-1])} and {names[-1]}"
    for axis, footprint, allowed, offsets in (
        ("X", size[0], bed.x, [n.offset_x for n in nozzles]),
        ("Y", size[1], bed.y, [n.offset_y for n in nozzles]),
    ):
        spread = max(offsets) - min(offsets)
        width = allowed.max - allowed.min
        if footprint + spread > width:
            raise LimitError(
                f"The model doesn't fit on the bed with the {syringes} syringes: it is "
                f"{footprint:.1f} mm across in {axis} and the syringes are {fmt(spread)} mm apart, "
                f"{footprint + spread:.1f} mm in all, but the bed is {fmt(width)} mm across in {axis} "
                f"({_span(allowed)}). Select fewer syringes or use a smaller model."
            )


def _is_g92(line: str) -> bool:
    words = parse_words(line)
    return bool(words) and words[0] == ("G", 92)


def plunger_step(
    before: MachineState, after: MachineState, line: str, plungers: Iterable[str]
) -> dict[str, float]:
    """How far each of `plungers` physically moved on `line` (negative = pushed).

    G92 changes the coordinates without moving anything, and a move from an
    unknown position can't be measured, so both count as 0.
    """
    if _is_g92(line):
        return {}
    moved = {}
    for axis in plungers:
        old, new = before.pos.get(axis), after.pos.get(axis)
        if old is not None and new is not None and new != old:
            moved[axis] = new - old
    return moved


def plunger_travel_needed(
    lines: Iterable[str], start: MachineState, plungers: Sequence[str]
) -> dict[str, float]:
    """The furthest each of `plungers` gets pushed from where it starts, in mm.

    Plungers extrude in the negative direction. Retracts pull back and the
    following prime pushes forward again, so the peak displacement, not the
    sum of all pushes, is the travel the syringe must have.
    """
    state = start
    displacement = {axis: 0.0 for axis in plungers}
    peak = {axis: 0.0 for axis in plungers}
    for line in lines:
        after = step(state, line)
        for axis, delta in plunger_step(state, after, line, plungers).items():
            displacement[axis] += delta
            peak[axis] = max(peak[axis], -displacement[axis])
        state = after
    return peak


def check_plunger_travel(needed: dict[str, float], available_mm: float, axes: Axes) -> None:
    """Raise LimitError if a plunger of `axes` needs more than `available_mm`."""
    for axis in axes.plungers:
        if needed.get(axis, 0.0) > available_mm:
            raise LimitError(
                f"This print needs {fmt(needed[axis])} mm of {axes.plunger_name(axis)} plunger ({axis}) "
                f"travel, but a full syringe only has {fmt(available_mm)} mm "
                f"(syringe_travel_mm in config.json). Reduce the print, or lower "
                f"the flow rate or flow multiplier."
            )
