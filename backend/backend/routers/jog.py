from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import JogResponse, OriginResponse
from backend.session import PrinterSession

router = APIRouter()


class JogRequest(BaseModel):
    axis: str
    distance: float
    feed_rate: float = 300


@router.post("/jog", response_model=JogResponse)
async def jog(body: JogRequest, session: PrinterSession = Depends(get_session)):
    axis = await session.jog(body.axis, body.distance, body.feed_rate)
    return {"status": "ok", "axis": axis, "distance": body.distance}


class OriginRequest(BaseModel):
    # Also lower the nozzle(s) of the syringe mode to zero height
    lower: bool = False


@router.post("/jog/origin", response_model=OriginResponse)
async def go_to_origin(body: OriginRequest, session: PrinterSession = Depends(get_session)):
    lines = await session.go_to_origin(body.lower)
    return {"status": "ok", "lines": lines}
