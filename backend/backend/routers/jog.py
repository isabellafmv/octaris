from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator

from backend.gcode_processor import TRAVEL_FEED
from backend.routers import get_session
from backend.schemas import JogResponse, OriginResponse
from backend.session import PrinterSession

router = APIRouter()


class JogRequest(BaseModel):
    # One axis, or several moved together in one G1 block (e.g. the height
    # axes or the plungers chosen in the jog panel); give one of the two
    axis: str | None = None
    axes: list[str] | None = None
    # mm, at most 200 either way
    distance: float
    # mm/min, 1 to 3000
    feed_rate: float = TRAVEL_FEED

    @model_validator(mode="after")
    def _one_of(self) -> JogRequest:
        if (self.axis is None) == (self.axes is None):
            raise ValueError("Give either axis or axes")
        return self


@router.post("/jog", response_model=JogResponse)
async def jog(body: JogRequest, session: PrinterSession = Depends(get_session)):
    """Move one axis, or several together (`axes`: X, Y, the profile's
    height axes and plungers), by `distance` (mm, at most 200 either way) at
    `feed_rate` (mm/min, 1 to 3000). Each axis is checked against the bed
    limits once zeroed; 400 if one would leave the bed, and then nothing
    moves."""
    axes = await session.jog_axes(
        body.axes if body.axes is not None else [body.axis or ""], body.distance, body.feed_rate
    )
    return {"status": "ok", "axis": axes[0], "axes": axes, "distance": body.distance}


class OriginRequest(BaseModel):
    # Also lower the selected nozzles to zero height
    lower: bool = False
    # mm/min, 1 to 3000: X/Y's travel, and the heights' lowering
    xy_feed: float = TRAVEL_FEED
    z_feed: float = TRAVEL_FEED


@router.post("/jog/origin", response_model=OriginResponse)
async def go_to_origin(body: OriginRequest, session: PrinterSession = Depends(get_session)):
    """Travel to X0 Y0 at `xy_feed` and, with `lower`, then lower the
    selected nozzles' height axes to their zero at `z_feed`."""
    lines = await session.go_to_origin(body.lower, body.xy_feed, body.z_feed)
    return {"status": "ok", "lines": lines}
