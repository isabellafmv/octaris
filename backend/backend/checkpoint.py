"""Locating where an e-stopped print halted, and resuming it from there.

After M410 the printer reports its position with M114. The stop point lies on
the path of one of the recently sent lines; on resume, the rest of that line
is sent as a relative move from the stop point and the print continues after
it. Every move sent here is relative (G91), like the prints themselves: an
absolute move needs the print's coordinates, which a relative print doesn't
keep, and a G90 left behind would turn its next lines absolute.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from backend.gcode_processor import (
    AXES,
    CLEARANCE_Z_MM,
    PRESSURIZE_FEED,
    TRAVEL_FEED,
    MachineState,
    parse_words,
)

# How far (per axis) the reported position may be from a line's path
POSITION_TOLERANCE_MM = 0.05
# How far an axis may be from where a relative print needs it before it's
# moved back: under M114's 0.01 mm resolution, since in a relative print
# any offset carries on to every later move.
MOVED_TOLERANCE_MM = 0.005

_M114_AXIS = re.compile(r"\b([XYZABC]):\s*(-?\d+(?:\.\d+)?)")


@dataclass
class Checkpoint:
    line: int  # index of the line the printer stopped on
    position: dict[str, float]  # logical position reported by M114
    after: MachineState  # as-sent machine state right after the stopped line
    retract: dict[str, float] = field(default_factory=dict)  # plunger retract after the stop


def parse_m114(response: str) -> dict[str, float] | None:
    """Parse "X:1.00 Y:2.00 Z:0.30 A:0.00 B:-1.20 C:0.00 Count X:..." into a dict.

    Only the part before "Count" is used: the counts are raw stepper positions.
    """
    for line in response.splitlines():
        logical = line.split("Count", 1)[0]
        pos = {axis: float(value) for axis, value in _M114_AXIS.findall(logical)}
        if "X" in pos and "Y" in pos:
            return pos
    return None


def segment_contains(
    before: MachineState,
    after: MachineState,
    point: dict[str, float],
    tol: float = POSITION_TOLERANCE_MM,
) -> bool:
    """Whether the straight move before→after passes within `tol` of `point`
    on every axis, at one common fraction t of the move.

    X and Y must be known at both ends; otherwise there's nothing to match.
    """
    for axis in ("X", "Y"):
        if before.pos[axis] is None or after.pos[axis] is None or axis not in point:
            return False
    lo, hi = 0.0, 1.0
    for axis in AXES:
        start, end = before.pos[axis], after.pos[axis]
        if end is None:
            continue  # axis never set by the program; nothing to compare
        if start is None:
            return False  # move from an unknown position can't be checked
        p = point.get(axis)
        if p is None:
            continue
        delta = end - start
        if abs(delta) < 1e-9:
            if abs(p - start) > tol:
                return False
            continue
        t1 = (p - tol - start) / delta
        t2 = (p + tol - start) / delta
        lo = max(lo, min(t1, t2))
        hi = min(hi, max(t1, t2))
        if lo > hi:
            return False
    return True


def _is_g92(line: str) -> bool:
    words = parse_words(line)
    return bool(words) and words[0] == ("G", 92)


def locate_line(
    segments: Sequence[tuple[int, MachineState, MachineState]],
    lines: Sequence[str],
    point: dict[str, float],
    tol: float = POSITION_TOLERANCE_MM,
) -> tuple[int, MachineState] | None:
    """The earliest of `segments` (index, as-sent before, as-sent after —
    oldest first) whose move contains `point`. Returns the matched line's
    index and its as-sent after-state, or None.

    `segments` reflects what was actually written to the printer — flow-rate
    scaling and any other runtime transform included — not the planned path,
    so a stop under a non-100% flow override still locates correctly.

    Earliest, because going over a stretch that already finished again only
    re-traces it, while skipping one that hadn't would leave part of the
    print out. Either way the print carries on from the matched line's
    as-sent end, so its later relative moves land where they should.
    G92 lines are skipped: they change coordinates without moving.
    """
    seen: set[int] = set()
    for index, before, after in segments:
        if index in seen:
            continue
        seen.add(index)
        if _is_g92(lines[index]):
            continue
        if segment_contains(before, after, point, tol):
            return index, after
    return None


def fmt(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def moved(actual: Mapping[str, float], target: Mapping[str, float]) -> bool:
    """Whether any axis of `target` is away from it in `actual` (M114)."""
    return any(abs(actual[ax] - value) > MOVED_TOLERANCE_MM for ax, value in target.items() if ax in actual)


def _distances(actual: Mapping[str, float], target: Mapping[str, float]) -> str:
    """The non-zero distances from `actual` to `target`, as G-code words."""
    words = []
    for axis, value in target.items():
        if axis in actual and (distance := fmt(value - actual[axis])) != "0":
            words.append(f"{axis}{distance}")
    return " ".join(words)


def travel_moves(actual: Mapping[str, float], target: Mapping[str, float]) -> list[str]:
    """Relative (G91) moves from `actual` to `target` on the stage: lift Z/A
    clear of the print, travel X/Y, lower onto the target. Only the axes of
    `target` move, and the printer is left in G91."""
    lift_axes = [ax for ax in ("Z", "A") if ax in target and ax in actual]
    commands = ["G91"]
    if lift_axes:
        lift = " ".join(f"{ax}{fmt(CLEARANCE_Z_MM)}" for ax in lift_axes)
        commands.append(f"G1 {lift} F{TRAVEL_FEED}")
    xy = _distances(actual, {ax: target[ax] for ax in ("X", "Y") if ax in target})
    if xy:
        commands.append(f"G1 {xy} F{TRAVEL_FEED}")
    if lift_axes:
        lower = " ".join(f"{ax}{fmt(target[ax] - actual[ax] - CLEARANCE_Z_MM)}" for ax in lift_axes)
        commands.append(f"G1 {lower} F{TRAVEL_FEED}")
    return commands


def build_resume_commands(checkpoint: Checkpoint, actual: Mapping[str, float]) -> list[str]:
    """Commands that bring the nozzle from `actual` (M114, now) back to the
    stop point and finish the stopped line, as relative distances, ending
    at the checkpoint's as-sent after-state — not the planned one, so the
    plunger lands where it was actually sent to (e.g. under a flow
    override). The caller continues with the line after it, in G91."""
    stop = checkpoint.position
    line_after = checkpoint.after
    stage: dict[str, float] = {axis: stop[axis] for axis in ("X", "Y", "Z", "A") if axis in stop}
    commands = travel_moves(actual, stage) if moved(actual, stage) else ["G91"]
    plungers = _distances(actual, {axis: stop[axis] for axis in checkpoint.retract if axis in stop})
    if plungers:
        commands.append(f"G1 {plungers} F{PRESSURIZE_FEED}")  # undo the retract

    # Finish the stopped line: the rest of the way to where it was headed,
    # from the stop point. The F also restores the modal feedrate the
    # following lines rely on.
    known = {axis: value for axis in AXES if (value := line_after.pos[axis]) is not None}
    rest = _distances(stop, known)
    feed = f"F{fmt(line_after.feed)}" if line_after.feed is not None else ""
    if rest or feed:
        commands.append(" ".join(word for word in ("G1", rest, feed) if word))
    if not line_after.relative:
        commands.append("G90")  # never for our prints: they're relative throughout
    return commands


def build_return_commands(
    actual: Mapping[str, float],
    paused_at: Mapping[str, float] | None,
    target: MachineState,
    restore_modes: bool = True,
) -> list[str]:
    """Commands that take a paused print back to where it was paused.

    `actual` is the printer's position now (M114), `paused_at` its position
    when the print was paused (M114, or None if it couldn't be read) and
    `target` the as-sent state where the print left off. If the stage was
    moved (a jog, say), it travels back to `paused_at` — lift, X/Y, lower,
    as relative moves — or to `target` without one. A plunger moved by hand
    stays where it is (priming, say) and is re-aligned with G92, so the
    print's coordinates carry on from there. With `restore_modes` (manual
    commands were sent, which may change them without moving anything), or
    after any return move, the print's feedrate and distance mode are
    restored. Returns [] if there is nothing to do.

    Raises ValueError if neither says where the stage should be.
    """
    goal = {axis: value for axis in AXES if (value := target.pos[axis]) is not None}
    if paused_at is None:
        stage = [axis for axis in ("X", "Y", "Z", "A") if axis in actual]
        unknown = [axis for axis in stage if axis not in goal]
        if unknown or "X" not in stage or "Y" not in stage:
            missing = ", ".join(unknown) or "X/Y"
            raise ValueError(f"where the print left off isn't known ({missing})")
        paused_at = goal

    commands: list[str] = []
    stage_goal = {axis: paused_at[axis] for axis in ("X", "Y", "Z", "A") if axis in paused_at}
    if moved(actual, stage_goal):
        commands += travel_moves(actual, stage_goal)
    # Compared with where they were at the pause; re-aligned with the exact
    # as-sent coordinates, which M114 rounds.
    plungers = {
        axis: goal.get(axis, paused_at[axis])
        for axis in ("B", "C")
        if axis in actual and axis in paused_at and moved(actual, {axis: paused_at[axis]})
    }
    if plungers:
        commands.append("G92 " + " ".join(f"{axis}{fmt(value)}" for axis, value in plungers.items()))
    if restore_modes or commands:
        if target.feed is not None:
            commands.append(f"G1 F{fmt(target.feed)}")
        commands.append("G91" if target.relative else "G90")
    return commands
