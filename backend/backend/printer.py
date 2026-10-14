"""Printer profiles: the machine's nozzles and their axes, its bed, and the
build volume the slicer works in.

A profile lists its nozzles left to right. Each nozzle has its own height
motor and plunger; plungers extrude in the negative direction. A print
uses a selection of them (a list of nozzle ids, see PrinterProfile.select):
X/Y are zeroed at the first (leftmost) selected nozzle, each selected nozzle
has its own height zero, and several selected nozzles all print the same
path, each in place.

The built-in profiles are in BUILTIN_PROFILES; config.json picks one with
"printer_profile" and can override any of its values, or add profiles,
under "profiles" (see config.Config).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field, model_validator

# The axes the G-code processor knows (see gcode_processor.AXES): every
# nozzle's height axis must be one of HEIGHT_AXES and its plunger one of
# PLUNGER_AXES. A printer with other axis letters (Printess 2.0) needs its
# letters added here and to gcode_processor.AXIS_DECIMALS.
HEIGHT_AXES = ("Z", "A")
PLUNGER_AXES = ("B", "C")


class AxisRange(BaseModel):
    min: float
    max: float

    @model_validator(mode="after")
    def _check_order(self) -> AxisRange:
        if self.min >= self.max:
            raise ValueError(f"min ({self.min}) must be below max ({self.max})")
        return self


class BedLimits(BaseModel):
    """Where the nozzles may go, in mm relative to the G92 zero point. Every
    nozzle's height axis has the z range.

    The printer has no homing or endstops, so these are enforced by the host
    only. The defaults match the 60 × 60 × 60 mm Cura machine definition with
    the zero in the middle of the bed and Z0 at the print surface. Given as
    min/max rather than a size so an off-centre zero can be described.
    """

    x: AxisRange = AxisRange(min=-30.0, max=30.0)
    y: AxisRange = AxisRange(min=-30.0, max=30.0)
    z: AxisRange = AxisRange(min=0.0, max=60.0)


class BuildVolume(BaseModel):
    """The slicer's machine size in mm (Cura's machine_width, machine_depth
    and machine_height). A model larger than this is refused before slicing."""

    x: float = Field(default=60.0, gt=0)
    y: float = Field(default=60.0, gt=0)
    z: float = Field(default=60.0, gt=0)


class NozzleSpec(BaseModel):
    """One nozzle: its height motor, its plunger, and where it sits."""

    id: str = Field(min_length=1)
    name: str
    height_axis: str
    plunger_axis: str
    # mm from the leftmost nozzle (the profile's first)
    offset_x: float = 0.0
    offset_y: float = 0.0

    @model_validator(mode="after")
    def _check_axes(self) -> NozzleSpec:
        if self.height_axis not in HEIGHT_AXES:
            raise ValueError(
                f"nozzle {self.id!r}: height_axis {self.height_axis!r} isn't one of {', '.join(HEIGHT_AXES)}"
            )
        if self.plunger_axis not in PLUNGER_AXES:
            raise ValueError(
                f"nozzle {self.id!r}: plunger_axis {self.plunger_axis!r} "
                f"isn't one of {', '.join(PLUNGER_AXES)}"
            )
        return self


class PrinterProfile(BaseModel):
    id: str = Field(min_length=1)
    name: str
    # Left to right
    nozzles: list[NozzleSpec] = Field(min_length=1)
    bed: BedLimits = BedLimits()
    build_volume: BuildVolume = BuildVolume()
    # The nozzles' offset_x/offset_y have been measured on the printer.
    # Prints with more than one nozzle are refused until they are.
    nozzle_offset_measured: bool = False

    @model_validator(mode="after")
    def _check_nozzles(self) -> PrinterProfile:
        ids = [n.id for n in self.nozzles]
        if len(set(ids)) != len(ids):
            raise ValueError(f"profile {self.id!r}: nozzle ids must be different ({', '.join(ids)})")
        axes = [ax for n in self.nozzles for ax in (n.height_axis, n.plunger_axis)]
        if len(set(axes)) != len(axes):
            raise ValueError(f"profile {self.id!r}: each nozzle needs its own height and plunger axis")
        return self

    def nozzle(self, nozzle_id: str) -> NozzleSpec:
        for nozzle in self.nozzles:
            if nozzle.id == nozzle_id:
                return nozzle
        raise ValueError(f"Unknown nozzle {nozzle_id!r} (the {self.name} has {', '.join(self.nozzle_ids)})")

    @property
    def nozzle_ids(self) -> list[str]:
        return [n.id for n in self.nozzles]

    def select(self, nozzle_ids: Sequence[str]) -> list[NozzleSpec]:
        """The nozzles with these ids, left to right. Raises ValueError if
        there are none, or one is unknown or named twice."""
        if not nozzle_ids:
            raise ValueError("Select at least one nozzle")
        if len(set(nozzle_ids)) != len(nozzle_ids):
            raise ValueError(f"A nozzle is selected twice: {', '.join(nozzle_ids)}")
        wanted = {self.nozzle(nozzle_id).id for nozzle_id in nozzle_ids}
        return [n for n in self.nozzles if n.id in wanted]


# Printess 1.0: two nozzles, the right one 30 mm in +X from the left.
PRINTESS_1: dict[str, Any] = {
    "id": "printess-1",
    "name": "Printess 1.0",
    "nozzles": [
        {"id": "left", "name": "Left", "height_axis": "Z", "plunger_axis": "B"},
        {"id": "right", "name": "Right", "height_axis": "A", "plunger_axis": "C", "offset_x": 30.0},
    ],
}

# Printess 2.0 (four nozzles) goes here once its axis letters are known.
BUILTIN_PROFILES: dict[str, dict[str, Any]] = {PRINTESS_1["id"]: PRINTESS_1}
DEFAULT_PROFILE = PRINTESS_1["id"]


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """`override` on top of `base`, nested objects merged key by key (a list,
    such as nozzles, is replaced as a whole)."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def build_profiles(overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, PrinterProfile]:
    """The built-in profiles with config.json's `overrides` applied: an id
    of a built-in profile overrides the values it gives; any other id adds
    a profile, which then needs all its values."""
    raw = {pid: dict(profile) for pid, profile in BUILTIN_PROFILES.items()}
    for pid, override in (overrides or {}).items():
        raw[pid] = _merge(raw.get(pid, {}), {**override, "id": pid})
    return {pid: PrinterProfile.model_validate(profile) for pid, profile in raw.items()}
