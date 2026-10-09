from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.gcode_processor import MAX_FEED

Target = Literal["macos", "rpi", "windows"]


def get_project_root() -> Path:
    """Return the project root, handling both source and PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: _MEIPASS is the temp extraction dir.
        # Bundled data files (config.json, context/, resources/) live there.
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # Running from source: backend/backend/config.py → project root is ../../..
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = get_project_root()


def default_target() -> Target:
    """The target for the OS this runs on, used when config.json doesn't set one."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "rpi"
    return "macos"


class AxisRange(BaseModel):
    min: float
    max: float

    @model_validator(mode="after")
    def _check_order(self) -> AxisRange:
        if self.min >= self.max:
            raise ValueError(f"min ({self.min}) must be below max ({self.max})")
        return self


class BedLimits(BaseModel):
    """Where the left nozzle may go, in mm relative to the G92 zero point.

    The printer has no homing or endstops, so these are enforced by the host
    only. The defaults match the 60 × 60 × 60 mm Cura machine definition with
    the zero in the middle of the bed and Z0 at the print surface. Given as
    min/max rather than a size so an off-centre zero can be described.
    """

    x: AxisRange = AxisRange(min=-30.0, max=30.0)
    y: AxisRange = AxisRange(min=-30.0, max=30.0)
    z: AxisRange = AxisRange(min=0.0, max=60.0)


class SensorConfig(BaseModel):
    """A readable name for a temperature sensor, and the targets it accepts."""

    name: str
    min: float = 0.0
    max: float = 120.0

    @model_validator(mode="after")
    def _check_order(self) -> SensorConfig:
        if self.min >= self.max:
            raise ValueError(f"min ({self.min}) must be below max ({self.max})")
        return self


class TemperatureConfig(BaseModel):
    # By the key the printer reports ("T0", "B", ...). A sensor not listed
    # here shows under its raw key with SensorConfig's default range.
    sensors: dict[str, SensorConfig] = {}
    # Readings older than this are deleted from the database at startup
    retention_days: float = 30.0
    # A sensor counts as at its target within ± this (status, and waiting
    # for temperatures before a print)
    target_band_c: float = 1.0
    # How long every target has to hold before a waiting print starts
    settle_s: float = 30.0
    # Warn while printing when a sensor is further than this from its
    # target for longer than deviation_s
    deviation_c: float = 3.0
    deviation_s: float = 60.0
    # Warn when connected and no temperature report came for this long
    report_timeout_s: float = 15.0


class Config(BaseModel):
    target: Target = Field(default_factory=default_target)
    touch_mode: bool = False
    baud_rate: int = 115200
    # After an e-stop, pull the plunger(s) back by pressurize_mm to stop oozing
    retract_on_estop: bool = True
    bed: BedLimits = BedLimits()
    # How far each plunger can travel with a full syringe. A print is refused
    # if it would push a plunger further, assuming the syringe starts full.
    syringe_travel_mm: float = 40.0
    # Highest feed rate (mm/min) a print may use: converted prints are
    # clamped to it, the print speed may not exceed it, and a file sent as
    # uploaded gets a warning above it
    max_feed_mm_min: float = Field(default=MAX_FEED, gt=0)
    # NOZZLE_OFFSET_X in gcode_processor.py is a placeholder until measured.
    # Right-nozzle and dual prints are refused until this is set to true.
    nozzle_offset_measured: bool = False
    temperature: TemperatureConfig = TemperatureConfig()


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = PROJECT_ROOT / "config.json"
    if path.exists():
        data = json.loads(path.read_text())
        return Config(**data)
    return Config()
