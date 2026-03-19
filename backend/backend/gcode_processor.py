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
    result = []
    for line in lines:
        result.append(_replace_e_with(line, axis))
    return result


def _replace_e_with(line: str, axis: str) -> str:
    def replacer(m):
        val = m.group(1)
        return f"{axis}-{val}"
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
        result.append(_replace_e_with(line, current_axis))
    return result


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


def prepend_g92(lines: list[str], mode: SyringeMode) -> list[str]:
    axes = {
        "left": "G92 X0 Y0 Z0 B0",
        "right": "G92 X0 Y0 A0 C0",
        "both": "G92 X0 Y0 Z0 A0 B0 C0",
    }
    return ["; Octaris — axis zero", axes[mode]] + lines


def insert_g91(lines: list[str]) -> list[str]:
    # Insert G91 after the G92 line (which is at index 1, after the comment)
    return lines[:2] + ["G91 ; relative positioning"] + lines[2:]


def validate(lines: list[str]) -> None:
    non_comment = [l for l in lines if not l.strip().startswith(";")]
    if not non_comment:
        raise GcodeValidationError("Empty G-code")

    if not non_comment[0].strip().startswith("G92"):
        raise GcodeValidationError("G-code must start with G92 axis zeroing")

    if len(non_comment) < 2 or not non_comment[1].strip().startswith("G91"):
        raise GcodeValidationError("G91 relative positioning must follow G92")

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


def process_gcode(raw: str, mode: SyringeMode) -> ProcessedGcode:
    time_estimate = extract_time_metadata(raw)
    lines = raw.splitlines()
    lines = strip_header(lines)
    lines = strip_footer(lines)
    lines = substitute_extrusion(lines, mode)
    lines, feed_log = clamp_feed_rates(lines)
    lines = prepend_g92(lines, mode)
    lines = insert_g91(lines)
    validate(lines)
    return ProcessedGcode(lines=lines, time_estimate_s=time_estimate, feed_log=feed_log)
