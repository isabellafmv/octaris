from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import JogResponse
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
