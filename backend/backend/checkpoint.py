"""Locating where an e-stopped print halted, and resuming it from there.

After M410 the printer reports its position with M114. The stop point lies on
the path of one of the recently sent lines; that line is re-sent as an
absolute move and the print continues after it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

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

    Earliest, because re-sending an absolute move that already finished is a
    no-op, while skipping one that hadn't would leave part of the print out.
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


def build_resume_commands(checkpoint: Checkpoint) -> list[str]:
    """Commands that bring the nozzle back to the stop point and finish the
    stopped line, ending at the checkpoint's as-sent after-state — not the
    planned one, so the plunger lands where it was actually sent to (e.g.
    under a flow override). The caller continues with the line after it."""
    pos = checkpoint.position
    line_after = checkpoint.after
    lift_axes = [axis for axis in ("Z", "A") if axis in pos]

    commands = ["G91"]
    if lift_axes:
        lift = " ".join(f"{axis}{fmt(CLEARANCE_Z_MM)}" for axis in lift_axes)
        commands.append(f"G1 {lift} F{TRAVEL_FEED}")
    commands.append("G90")
    commands.append(f"G1 X{fmt(pos['X'])} Y{fmt(pos['Y'])} F{TRAVEL_FEED}")
    if lift_axes:
        lower = " ".join(f"{axis}{fmt(pos[axis])}" for axis in lift_axes)
        commands.append(f"G1 {lower} F{TRAVEL_FEED}")
    if checkpoint.retract:
        restore = " ".join(f"{axis}{fmt(pos[axis])}" for axis in checkpoint.retract)
        commands.append(f"G1 {restore} F{PRESSURIZE_FEED}")

    # Finish the stopped line as an absolute move to where it was headed, on
    # every known axis so any drift within the match tolerance is corrected.
    # The F also restores the modal feedrate the following lines rely on.
    known = [axis for axis in AXES if line_after.pos[axis] is not None]
    feed = f" F{fmt(line_after.feed)}" if line_after.feed is not None else ""
    if known:
        target = " ".join(f"{axis}{fmt(line_after.pos[axis])}" for axis in known)
        commands.append(f"G1 {target}{feed}")
    elif feed:
        commands.append(f"G1{feed}")
    if line_after.relative:
        commands.append("G91")  # the following lines are in a relative block
    return commands
