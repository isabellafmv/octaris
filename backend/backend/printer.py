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
under "profiles" (see config.Config). The "Custom" profile is edited in
the app and kept in settings.json (see session.PrinterSession).

Nothing else in the app knows axis letters: everything that moves, checks
or reports an axis takes them from the active profile's `axes`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# The letters a nozzle's height motor or plunger can have: Marlin's axis
# names, except X/Y (the stage) and E (the slicer's extruder, which the
# processing turns into plunger moves), and D, the Printess 2.0 board's
# fourth plunger
NOZZLE_AXIS_LETTERS = ("Z", "A", "B", "C", "I", "J", "K", "U", "V", "W", "D")
# How many nozzles a profile may have
MAX_NOZZLES = 6
# Steps/mm sent for every axis on connect (M92), unless a profile sets its own
DEFAULT_STEPS_PER_MM = 800.0
# The id of the profile edited in the app
CUSTOM_PROFILE_ID = "custom"
CUSTOM_PROFILE_NAME = "Custom"


@dataclass(frozen=True)
class Axes:
    """A profile's axes: X/Y, then each nozzle's height motor and plunger,
    left to right. `names` are the nozzles' names, in the same order."""

    heights: tuple[str, ...]
    plungers: tuple[str, ...]
    names: tuple[str, ...] = ()

    @classmethod
    def of(cls, nozzles: Sequence[NozzleSpec]) -> Axes:
        return cls(
            heights=tuple(n.height_axis for n in nozzles),
            plungers=tuple(n.plunger_axis for n in nozzles),
            names=tuple(n.name for n in nozzles),
        )

    @property
    def all(self) -> tuple[str, ...]:
        """Every axis: X, Y, the heights, the plungers."""
        return ("X", "Y", *self.heights, *self.plungers)

    @property
    def stage(self) -> tuple[str, ...]:
        """X, Y and the heights: the axes the bed limits apply to."""
        return ("X", "Y", *self.heights)

    def decimals(self, axis: str) -> int:
        """Decimals the axis is written with in a converted print: Cura's own
        resolution for X/Y/Z (3) and for E (5), which the plungers come from."""
        return 5 if axis in self.plungers else 3

    def plunger_name(self, axis: str) -> str:
        """The name of the plunger's nozzle, lower case ("left" for the Printess 1.0's B)."""
        if axis in self.plungers and len(self.names) == len(self.plungers):
            return self.names[self.plungers.index(axis)].lower()
        return axis

    def describe_plungers(self) -> str:
        """ "B (left) and C (right)"."""
        parts = [f"{axis} ({self.plunger_name(axis)})" for axis in self.plungers]
        return " and ".join([", ".join(parts[:-1]), parts[-1]] if len(parts) > 1 else parts)


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
    name: str = Field(min_length=1)
    # Each one of NOZZLE_AXIS_LETTERS, and different from each other
    height_axis: str
    plunger_axis: str
    # mm from the leftmost nozzle (the profile's first)
    offset_x: float = 0.0
    offset_y: float = 0.0

    @field_validator("height_axis", "plunger_axis", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_axes(self) -> NozzleSpec:
        for field_name in ("height_axis", "plunger_axis"):
            axis = getattr(self, field_name)
            if axis not in NOZZLE_AXIS_LETTERS:
                raise ValueError(
                    f"nozzle {self.id!r}: {field_name} {axis!r} isn't one of {', '.join(NOZZLE_AXIS_LETTERS)}"
                )
        if self.height_axis == self.plunger_axis:
            raise ValueError(
                f"nozzle {self.id!r}: the height and plunger axis must be different "
                f"(both are {self.height_axis})"
            )
        return self


class PrinterProfile(BaseModel):
    id: str = Field(min_length=1)
    name: str
    # Left to right
    nozzles: list[NozzleSpec] = Field(min_length=1, max_length=MAX_NOZZLES)
    bed: BedLimits = BedLimits()
    build_volume: BuildVolume = BuildVolume()
    # The nozzles' offset_x/offset_y have been measured on the printer.
    # Prints with more than one nozzle are refused until they are.
    nozzle_offset_measured: bool = False
    # Steps/mm of every axis, sent with M92 on connect (EEPROM is disabled
    # on the board)
    steps_per_mm: float = Field(default=DEFAULT_STEPS_PER_MM, gt=0)

    @model_validator(mode="after")
    def _check_nozzles(self) -> PrinterProfile:
        ids = [n.id for n in self.nozzles]
        if len(set(ids)) != len(ids):
            raise ValueError(f"profile {self.id!r}: nozzle ids must be different ({', '.join(ids)})")
        axes = [ax for n in self.nozzles for ax in (n.height_axis, n.plunger_axis)]
        repeated = sorted({ax for ax in axes if axes.count(ax) > 1})
        if repeated:
            raise ValueError(
                f"profile {self.id!r}: each nozzle needs its own height and plunger axis "
                f"({', '.join(repeated)} is used twice)"
            )
        return self

    @property
    def axes(self) -> Axes:
        return Axes.of(self.nozzles)

    @property
    def steps_per_mm_command(self) -> str:
        """The M92 sent on connect: X, Y and every axis of the profile."""
        steps = f"{self.steps_per_mm:g}"
        return "M92 " + " ".join(f"{axis}{steps}" for axis in self.axes.all)

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

# Printess 2.0: four syringes 30 mm apart, on a 200 × 200 mm bed, every
# axis at 3200 steps/mm.
PRINTESS_2: dict[str, Any] = {
    "id": "printess-2",
    "name": "Printess 2.0",
    "nozzles": [
        {
            "id": f"syringe-{i + 1}",
            "name": f"Syringe {i + 1}",
            "height_axis": height,
            "plunger_axis": plunger,
            "offset_x": 30.0 * i,
        }
        for i, (height, plunger) in enumerate((("Z", "U"), ("A", "V"), ("B", "W"), ("C", "D")))
    ],
    "bed": {
        "x": {"min": -100.0, "max": 100.0},
        "y": {"min": -100.0, "max": 100.0},
        "z": {"min": 0.0, "max": 60.0},
    },
    "build_volume": {"x": 200.0, "y": 200.0, "z": 60.0},
    "nozzle_offset_measured": True,
    "steps_per_mm": 3200.0,
}

BUILTIN_PROFILES: dict[str, dict[str, Any]] = {p["id"]: p for p in (PRINTESS_1, PRINTESS_2)}
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
        if pid == CUSTOM_PROFILE_ID:
            raise ValueError(f"The profile id {pid!r} is kept for the profile edited in the app")
        raw[pid] = _merge(raw.get(pid, {}), {**override, "id": pid})
    return {pid: PrinterProfile.model_validate(profile) for pid, profile in raw.items()}


def default_axes() -> Axes:
    """The default profile's axes, for a serial manager, queue worker or
    virtual printer made without a profile (the session sets the active
    profile's)."""
    return PrinterProfile.model_validate(BUILTIN_PROFILES[DEFAULT_PROFILE]).axes


class NozzleFields(BaseModel):
    """A nozzle as the app's editor sends it: checked by save_custom_profile,
    which reports every problem in words, rather than by FastAPI."""

    id: str
    name: str
    height_axis: str
    plunger_axis: str
    offset_x: float = 0.0
    offset_y: float = 0.0


class RangeFields(BaseModel):
    """An axis range as the app's editor sends it, checked (min < max) by
    custom_profile like NozzleFields."""

    min: float
    max: float


class BedFields(BaseModel):
    """Bed limits as the app's editor sends them (see BedLimits)."""

    x: RangeFields
    y: RangeFields
    z: RangeFields


def custom_profile(
    base: PrinterProfile,
    nozzles: Sequence[NozzleFields | dict[str, Any]],
    nozzle_offset_measured: bool,
    bed: BedFields | dict[str, Any] | None = None,
    steps_per_mm: float | None = None,
) -> PrinterProfile:
    """The Custom profile: `base`'s build volume, with these nozzles, `bed`
    and `steps_per_mm` (None: `base`'s). Raises pydantic.ValidationError
    for invalid nozzles, bed limits or steps/mm."""
    data = base.model_dump()
    data.update(
        id=CUSTOM_PROFILE_ID,
        name=CUSTOM_PROFILE_NAME,
        nozzles=[n.model_dump() if isinstance(n, BaseModel) else n for n in nozzles],
        nozzle_offset_measured=nozzle_offset_measured,
    )
    if bed is not None:
        data["bed"] = bed.model_dump() if isinstance(bed, BaseModel) else bed
    if steps_per_mm is not None:
        data["steps_per_mm"] = steps_per_mm
    return PrinterProfile.model_validate(data)
