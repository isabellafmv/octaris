from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SyringeConfig(BaseModel):
    mode: Literal["left", "right", "both"]
