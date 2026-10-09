from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
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


@router.get(
    "/gcode/loaded",
    response_class=PlainTextResponse,
    responses={
        200: {"content": {"text/plain": {"schema": {"type": "string"}}}, "description": "G-code"},
        404: {"description": "No print loaded"},
    },
)
async def get_loaded_gcode(session: PrinterSession = Depends(get_session)):
    """The loaded print's processed G-code, one line per line, as sent to
    the printer (before flow scaling)."""
    if session.loaded is None:
        raise HTTPException(status_code=404, detail="No print loaded")
    return PlainTextResponse("\n".join(session.loaded.gcode.lines))
