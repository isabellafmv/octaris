from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, model_validator


def get_project_root() -> Path:
    """Return the project root, handling both source and PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle: _MEIPASS is the temp extraction dir.
        # Bundled data files (config.json, context/, resources/) live there.
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    # Running from source: backend/backend/config.py → project root is ../../..
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = get_project_root()


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


class Config(BaseModel):
    target: Literal["macos", "rpi"] = "macos"
    touch_mode: bool = False
    baud_rate: int = 115200
    # After an e-stop, pull the plunger(s) back by pressurize_mm to stop oozing
    retract_on_estop: bool = True
    bed: BedLimits = BedLimits()
    # How far each plunger can travel with a full syringe. A print is refused
    # if it would push a plunger further, assuming the syringe starts full.
    syringe_travel_mm: float = 40.0
    # NOZZLE_OFFSET_X in gcode_processor.py is a placeholder until measured.
    # Right-nozzle and dual prints are refused until this is set to true.
    nozzle_offset_measured: bool = False


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = PROJECT_ROOT / "config.json"
    if path.exists():
        data = json.loads(path.read_text())
        return Config(**data)
    return Config()
