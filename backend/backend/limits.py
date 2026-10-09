"""Host-side movement limits: the bed box and the syringe plunger travel.

The printer has no endstops, so nothing on the machine stops a move that
leaves the bed or runs a plunger into the end of its syringe. These checks
are the only guard; they work in the coordinate system set by the G92 zero.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from backend.checkpoint import fmt
from backend.config import AxisRange, BedLimits
from backend.gcode_processor import AXES, MachineState, parse_words, step

PLUNGER_AXES = ("B", "C")
# Warn when a plunger has less than this fraction of its travel left
LOW_TRAVEL_FRACTION = 0.10


class LimitError(Exception):
    pass


def _ranges(bed: BedLimits) -> dict[str, AxisRange]:
    """A is the right nozzle's height motor: the same range as Z."""
    return {"X": bed.x, "Y": bed.y, "Z": bed.z, "A": bed.z}


def _describe(axis: str, value: float, allowed: AxisRange) -> str:
    return f"{axis} to {fmt(value)} mm, outside the bed ({fmt(allowed.min)} to {fmt(allowed.max)} mm)"


def check_jog(bed: BedLimits, axis: str, current: float, distance: float) -> None:
    """Raise LimitError if jogging `axis` by `distance` from `current` leaves the bed."""
    allowed = _ranges(bed).get(axis)
    if allowed is None:
        return
    target = current + distance
    if not allowed.min <= target <= allowed.max:
        raise LimitError(f"This jog would move {_describe(axis, target, allowed)}")


def start_state(position: dict[str, float] | None) -> MachineState:
    """A MachineState at `position` (as parsed from M114), absolute mode."""
    position = position or {}
    return MachineState(pos={ax: position.get(ax) for ax in AXES})


def check_path(
    bed: BedLimits,
    lines: Sequence[str],
    start: MachineState | None = None,
    height_axes: Sequence[str] = ("Z",),
) -> None:
    """Raise LimitError if any move in `lines` ends outside the bed.

    X, Y and `height_axes` are checked: the height motors of the nozzle(s)
    the print uses (Z left, A right). The other nozzle's height isn't zeroed
    for this print, so its coordinate means nothing.

    Every move is a straight line and the bed is a box, so a path stays on
    the bed if all its end points do. An axis whose position is unknown (a
    relative move before the program or `start` set it) can't be checked;
    pass the printer's current position as `start` to cover those.
    """
    ranges = {axis: allowed for axis, allowed in _ranges(bed).items() if axis in ("X", "Y", *height_axes)}
    state = start or start_state(None)
    for number, line in enumerate(lines, start=1):
        state = step(state, line)
        for axis, allowed in ranges.items():
            value = state.pos[axis]
            if value is not None and not allowed.min <= value <= allowed.max:
                raise LimitError(
                    f"The print leaves the bed: line {number} ({line.strip()}) moves "
                    f"{_describe(axis, value, allowed)}. Move the model, or check the "
                    f"zero point and the bed limits in config.json."
                )


def _is_g92(line: str) -> bool:
    words = parse_words(line)
    return bool(words) and words[0] == ("G", 92)


def plunger_step(before: MachineState, after: MachineState, line: str) -> dict[str, float]:
    """How far each plunger physically moved on `line` (negative = pushed).

    G92 changes the coordinates without moving anything, and a move from an
    unknown position can't be measured, so both count as 0.
    """
    if _is_g92(line):
        return {}
    moved = {}
    for axis in PLUNGER_AXES:
        old, new = before.pos[axis], after.pos[axis]
        if old is not None and new is not None and new != old:
            moved[axis] = new - old
    return moved


def plunger_travel_needed(lines: Iterable[str], start: MachineState | None = None) -> dict[str, float]:
    """The furthest each plunger gets pushed from where it starts, in mm.

    Plungers extrude in the negative direction. Retracts pull back and the
    following prime pushes forward again, so the peak displacement, not the
    sum of all pushes, is the travel the syringe must have.
    """
    state = start or start_state(None)
    displacement = {axis: 0.0 for axis in PLUNGER_AXES}
    peak = {axis: 0.0 for axis in PLUNGER_AXES}
    for line in lines:
        after = step(state, line)
        for axis, delta in plunger_step(state, after, line).items():
            displacement[axis] += delta
            peak[axis] = max(peak[axis], -displacement[axis])
        state = after
    return peak


def check_plunger_travel(needed: dict[str, float], available_mm: float) -> None:
    for axis in PLUNGER_AXES:
        if needed.get(axis, 0.0) > available_mm:
            side = "left" if axis == "B" else "right"
            raise LimitError(
                f"This print needs {fmt(needed[axis])} mm of {side} plunger ({axis}) "
                f"travel, but a full syringe only has {fmt(available_mm)} mm "
                f"(syringe_travel_mm in config.json). Reduce the print, or lower "
                f"the flow rate or flow multiplier."
            )
