from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import ExtrusionResponse
from backend.session import PrinterSession

router = APIRouter()


class ExtrusionRequest(BaseModel):
    rate: int


@router.post("/extrusion", response_model=ExtrusionResponse)
async def set_extrusion_rate(body: ExtrusionRequest, session: PrinterSession = Depends(get_session)):
    session.set_flow_rate(body.rate)
    return {"status": "ok", "rate": body.rate}
