from __future__ import annotations

import json
import platform
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from backend.gcode_processor import MAX_FEED
from backend.printer import (
    DEFAULT_PROFILE,
    AxisRange,  # noqa: F401 - re-exported
    BedLimits,  # noqa: F401 - re-exported
    PrinterProfile,
    build_profiles,
)

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


# The lab firmware's channels, I0..I3. Its M105 reply lists more (5 so far),
# but only these are wired up.
CHANNEL_COUNT = 4


class ChannelConfig(BaseModel):
    """One heater channel of the lab (printess) firmware: M104 I<index> ...
    What it heats (a syringe, the bed or nothing) is chosen in the app, the
    temperature_channels setting."""

    index: int = Field(ge=0, lt=CHANNEL_COUNT)
    # The targets it accepts, besides 0 (off)
    min: float = 4.0
    max: float = 50.0

    @model_validator(mode="after")
    def _check(self) -> ChannelConfig:
        if self.min >= self.max:
            raise ValueError(f"min ({self.min}) must be below max ({self.max})")
        return self

    @property
    def sensor(self) -> str:
        """The key the channel goes by in the app, the API and the log: "I0"."""
        return f"I{self.index}"


def _default_channels() -> list[ChannelConfig]:
    return [ChannelConfig(index=n) for n in range(CHANNEL_COUNT)]


class TemperatureConfig(BaseModel):
    # "marlin": standard Marlin heaters (M104 [T<n>] S, M140, M141).
    # "printess": the lab firmware's channels (M104 I<n> T<°C> H|C), see channels.
    protocol: Literal["marlin", "printess"] = "marlin"
    # By the key the printer reports ("T0", "B", ...). A sensor not listed
    # here shows under its raw key with SensorConfig's default range.
    sensors: dict[str, SensorConfig] = {}
    # printess only: the heater channels and the targets each accepts
    channels: list[ChannelConfig] = Field(default_factory=_default_channels)
    # printess only: the command that switches channel {n} off (Disabled)
    off_command: str = "M104 I{n} D"
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

    @model_validator(mode="after")
    def _check_channels(self) -> TemperatureConfig:
        indexes = [channel.index for channel in self.channels]
        if len(indexes) != len(set(indexes)):
            raise ValueError("Two temperature channels have the same index")
        try:
            self.off_command.format(n=0)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"off_command {self.off_command!r} can only use {{n}}") from exc
        return self

    def channel(self, sensor: str) -> ChannelConfig | None:
        """The printess channel that goes by `sensor` ("I0"), if any."""
        return next((c for c in self.channels if c.sensor == sensor), None)


class Config(BaseModel):
    target: Target = Field(default_factory=default_target)
    touch_mode: bool = False
    baud_rate: int = 115200
    # After an e-stop, pull the plunger(s) back by pressurize_mm to stop oozing
    retract_on_estop: bool = True
    # How far each plunger can travel with a full syringe. A print is refused
    # if it would push a plunger further, assuming the syringe starts full.
    syringe_travel_mm: float = 40.0
    # Highest feed rate (mm/min) a print may use: converted prints are
    # clamped to it, the print speed may not exceed it, and a file sent as
    # uploaded gets a warning above it
    max_feed_mm_min: float = Field(default=MAX_FEED, gt=0)
    temperature: TemperatureConfig = TemperatureConfig()
    # The printer: the id of a profile in `profiles`
    printer_profile: str = DEFAULT_PROFILE
    # Overrides of the built-in profiles (see printer.BUILTIN_PROFILES) and
    # added ones, by id; resolved into `profiles`
    profile_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict, alias="profiles")
    _profiles: dict[str, PrinterProfile] = PrivateAttr(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _legacy_keys(cls, data: Any) -> Any:
        """Before profiles, "bed" and "nozzle_offset_measured" were top-level
        keys. A config.json that still has them applies them to the selected
        profile, rather than silently falling back to the default bed."""
        if not isinstance(data, dict) or not ({"bed", "nozzle_offset_measured"} & data.keys()):
            return data
        data = dict(data)
        legacy = {key: data.pop(key) for key in ("bed", "nozzle_offset_measured") if key in data}
        selected = data.get("printer_profile", DEFAULT_PROFILE)
        profiles = dict(data.get("profiles", {}))
        profiles[selected] = {**legacy, **profiles.get(selected, {})}
        data["profiles"] = profiles
        return data

    @model_validator(mode="after")
    def _resolve_profiles(self) -> Config:
        self._profiles = build_profiles(self.profile_overrides)
        if self.printer_profile not in self._profiles:
            raise ValueError(
                f"printer_profile {self.printer_profile!r} isn't a profile "
                f"(there are {', '.join(self._profiles)})"
            )
        return self

    @property
    def profiles(self) -> dict[str, PrinterProfile]:
        return self._profiles

    @property
    def profile(self) -> PrinterProfile:
        """The selected printer profile."""
        return self._profiles[self.printer_profile]


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = PROJECT_ROOT / "config.json"
    if path.exists():
        data = json.loads(path.read_text())
        return Config(**data)
    return Config()
