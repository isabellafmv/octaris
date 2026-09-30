from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import GcodeSendResponse, SerialLogResponse
from backend.session import PrinterSession

router = APIRouter()


class GcodeSendRequest(BaseModel):
    line: str


@router.post("/gcode/send", response_model=GcodeSendResponse)
async def send_gcode(body: GcodeSendRequest, session: PrinterSession = Depends(get_session)):
    """Send a raw G-code line to the printer and return its response."""
    response = await session.send_gcode(body.line)
    return GcodeSendResponse(status="ok", response=response)


@router.get("/gcode/log", response_model=SerialLogResponse)
async def get_serial_log(limit: int = 200, session: PrinterSession = Depends(get_session)):
    """Return the most recent serial log entries."""
    return {"entries": session.serial_log(limit)}
