from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Config(BaseModel):
    target: Literal["macos", "rpi"] = "macos"
    touch_mode: bool = False
    baud_rate: int = 250000


def load_config(path: Path | None = None) -> Config:
    if path is None:
        path = Path(__file__).resolve().parent.parent.parent / "config.json"
    if path.exists():
        data = json.loads(path.read_text())
        return Config(**data)
    return Config()
