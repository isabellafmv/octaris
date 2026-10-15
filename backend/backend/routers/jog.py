from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator

from backend.routers import get_session
from backend.schemas import JogResponse, OriginResponse
from backend.session import PrinterSession

router = APIRouter()


class JogRequest(BaseModel):
    # One axis, or several moved together in one G1 block (e.g. the height
    # axes chosen in the jog panel); give one of the two
    axis: str | None = None
    axes: list[str] | None = None
    distance: float
    feed_rate: float = 300

    @model_validator(mode="after")
    def _one_of(self) -> JogRequest:
        if (self.axis is None) == (self.axes is None):
            raise ValueError("Give either axis or axes")
        return self


@router.post("/jog", response_model=JogResponse)
async def jog(body: JogRequest, session: PrinterSession = Depends(get_session)):
    """Move one axis, or several together (`axes`), by `distance`. Each
    axis is checked against the bed limits once zeroed; 400 if one would
    leave the bed, and then nothing moves."""
    axes = await session.jog_axes(
        body.axes if body.axes is not None else [body.axis or ""], body.distance, body.feed_rate
    )
    return {"status": "ok", "axis": axes[0], "axes": axes, "distance": body.distance}


class OriginRequest(BaseModel):
    # Also lower the selected nozzles to zero height
    lower: bool = False


@router.post("/jog/origin", response_model=OriginResponse)
async def go_to_origin(body: OriginRequest, session: PrinterSession = Depends(get_session)):
    lines = await session.go_to_origin(body.lower)
    return {"status": "ok", "lines": lines}
