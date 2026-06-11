from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

SyringeMode = Literal["left", "right", "both"]

# Exact footer block CuraEngine appends
MARLIN_FOOTER = [
    "M107",
    "M104 S0",
    "M140 S0",
    ";Retract the filament",
    "G92 E1",
    "G1 E-1 F300",
    "G28 X0 Y0",
    "M84",
    "M82 ;absolute extrusion mode",
    "M104 S0",
    ";End of Gcode",
]

_E_PATTERN = re.compile(r"E(-?\d+\.?\d*)")
_F_PATTERN = re.compile(r"F(\d+\.?\d*)")
_Z_IN_G0 = re.compile(r"^G0\b.*[ZA]", re.IGNORECASE)

# Pressurization distance (mm) to prime/deprime the syringe
PRESSURIZE_MM = 0.2
PRESSURIZE_FEED = 400
# Nozzle clearance height (mm) to raise before returning to origin
CLEARANCE_Z_MM = 5
TRAVEL_FEED = 300


class GcodeValidationError(Exception):
    pass


@dataclass
class ProcessedGcode:
    lines: list[str]
    time_estimate_s: int | None = None
    feed_log: list[str] = field(default_factory=list)


def extract_time_metadata(raw: str) -> int | None:
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(";TIME:"):
            try:
                return int(float(stripped.split(":", 1)[1]))
            except (ValueError, IndexError):
                pass
    return None


def strip_header(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        if line.strip().startswith("G0"):
            return lines[i:]
    return lines


def strip_footer(lines: list[str]) -> list[str]:
    footer_len = len(MARLIN_FOOTER)
    if len(lines) < footer_len:
        return lines

    # Search from the end for the footer block
    for start in range(len(lines) - footer_len, max(len(lines) - footer_len - 5, -1), -1):
        if start < 0:
            break
        candidate = [lines[start + j].strip() for j in range(footer_len)]
        if candidate == MARLIN_FOOTER:
            return lines[:start]

    return lines


def substitute_extrusion(lines: list[str], mode: SyringeMode) -> list[str]:
    if mode == "both":
        return _substitute_both(lines)

    axis = "B" if mode == "left" else "C"
    negate = True  # both B and C extrude in negative direction
    result = []
    for line in lines:
        result.append(_replace_e_with(line, axis, negate=negate))
    return result


def _replace_e_with(line: str, axis: str, negate: bool = False) -> str:
    def replacer(m):
        val = m.group(1)
        if negate:
            num = float(val)
            num = -num
            # Format without trailing zeros but keep decimal for non-integers
            formatted = f"{num:g}"
            return f"{axis}{formatted}"
        return f"{axis}{val}"
    return _E_PATTERN.sub(replacer, line)


def _substitute_both(lines: list[str]) -> list[str]:
    result = []
    current_axis = "B"  # default to left extruder (T0)
    for line in lines:
        stripped = line.strip()
        if stripped == "T0":
            current_axis = "B"
            result.append(line)
            continue
        if stripped == "T1":
            current_axis = "C"
            result.append(line)
            continue
        result.append(_replace_e_with(line, current_axis, negate=True))
    return result


_BC_PATTERN = re.compile(r"([BC])(-?\d+\.?\d*)")


def apply_flow_multiplier(lines: list[str], multiplier: float) -> list[str]:
    """Scale all B/C extrusion values by the given multiplier."""
    if multiplier == 1.0:
        return lines

    def scale(m):
        axis = m.group(1)
        val = float(m.group(2)) * multiplier
        return f"{axis}{val:g}"

    return [_BC_PATTERN.sub(scale, line) for line in lines]


def clamp_feed_rates(lines: list[str], max_f: float = 400) -> tuple[list[str], list[str]]:
    result = []
    log_entries = []

    for i, line in enumerate(lines):
        def clamp(m):
            val = float(m.group(1))
            if val > max_f:
                log_entries.append(f"Line {i + 1}: F{val} clamped to F{max_f}")
                return f"F{int(max_f)}"
            return m.group(0)

        result.append(_F_PATTERN.sub(clamp, line))

    return result, log_entries


def _extrude_sign(axis: str) -> int:
    """Return -1 for B/C axes (both extrude in negative direction)."""
    return -1 if axis in ("B", "C") else 1


def build_preamble(mode: SyringeMode, pressurize_mm: float = PRESSURIZE_MM) -> list[str]:
    """G90 absolute positioning + syringe pressurization."""
    extrusion_axis = {"left": "B", "right": "C", "both": "B"}[mode]
    sign = _extrude_sign(extrusion_axis)
    pressurize_val = sign * pressurize_mm
    return [
        "; Octaris — preamble",
        "G90",
        f"G1 {extrusion_axis}{pressurize_val} F{PRESSURIZE_FEED} ; pressurize",
        f"G92 {extrusion_axis}0 ; reset after pressurization",
    ]


def build_footer(mode: SyringeMode, pressurize_mm: float = PRESSURIZE_MM) -> list[str]:
    """Depressurize syringe, raise nozzle, return to origin."""
    extrusion_axis = {"left": "B", "right": "C", "both": "B"}[mode]
    z_axis = {"left": "Z", "right": "A", "both": "Z"}[mode]
    sign = _extrude_sign(extrusion_axis)
    depressurize_val = -sign * pressurize_mm  # opposite of extrusion direction
    return [
        "; Octaris — footer",
        f"G91",
        f"G1 {extrusion_axis}{depressurize_val} F{PRESSURIZE_FEED} ; depressurize",
        f"G1 {z_axis}{CLEARANCE_Z_MM} F{TRAVEL_FEED} ; raise nozzle",
        f"G90",
        f"G1 X0 Y0 F{TRAVEL_FEED} ; return to origin",
    ]


def insert_layer_depressurize(lines: list[str], mode: SyringeMode, pressurize_mm: float = PRESSURIZE_MM) -> list[str]:
    """Wrap layer-change travel moves (G0 with Z/A) with depressurize/repressurize.

    Skips the very first G0-with-Z since that's the initial positioning before
    any extrusion has happened (the preamble handles the first pressurization).
    """
    extrusion_axis = {"left": "B", "right": "C", "both": "B"}[mode]
    z_axis = {"left": "Z", "right": "A", "both": "Z"}[mode]
    pattern = re.compile(rf"^G0\b.*{z_axis}", re.IGNORECASE)

    result: list[str] = []
    seen_first = False

    for line in lines:
        stripped = line.strip()
        if pattern.match(stripped):
            if not seen_first:
                # First layer positioning — no depressurize needed
                seen_first = True
                result.append(line)
            else:
                # Layer change — depressurize before, repressurize after
                result.append(f"; layer change — depressurize")
                result.append(f"G91")
                sign = _extrude_sign(extrusion_axis)
                depressurize_val = -sign * pressurize_mm
                repressurize_val = sign * pressurize_mm
                result.append(f"G1 {extrusion_axis}{depressurize_val} F{PRESSURIZE_FEED}")
                result.append(f"G90")
                result.append(line)
                result.append(f"; repressurize")
                result.append(f"G91")
                result.append(f"G1 {extrusion_axis}{repressurize_val} F{PRESSURIZE_FEED}")
                result.append(f"G90")
        else:
            result.append(line)

    return result


def validate(lines: list[str]) -> None:
    non_comment = [l for l in lines if not l.strip().startswith(";")]
    if not non_comment:
        raise GcodeValidationError("Empty G-code")

    if not non_comment[0].strip().startswith("G90"):
        raise GcodeValidationError("G-code must start with G90 absolute positioning")

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(";"):
            continue
        if _E_PATTERN.search(stripped):
            raise GcodeValidationError(
                f"Line {i + 1}: Unsubstituted E command found: {stripped}"
            )
        for m in _F_PATTERN.finditer(stripped):
            if float(m.group(1)) > 400:
                raise GcodeValidationError(
                    f"Line {i + 1}: F value {m.group(1)} exceeds 400"
                )


def process_gcode(
    raw: str,
    mode: SyringeMode,
    pressurize_mm: float = PRESSURIZE_MM,
    flow_multiplier: float = 1.0,
) -> ProcessedGcode:
    time_estimate = extract_time_metadata(raw)
    lines = raw.splitlines()
    lines = strip_header(lines)
    lines = strip_footer(lines)
    lines = substitute_extrusion(lines, mode)
    if flow_multiplier != 1.0:
        lines = apply_flow_multiplier(lines, flow_multiplier)
    lines, feed_log = clamp_feed_rates(lines)
    lines = insert_layer_depressurize(lines, mode, pressurize_mm=pressurize_mm)
    lines = build_preamble(mode, pressurize_mm=pressurize_mm) + lines + build_footer(mode, pressurize_mm=pressurize_mm)
    validate(lines)
    return ProcessedGcode(lines=lines, time_estimate_s=time_estimate, feed_log=feed_log)
