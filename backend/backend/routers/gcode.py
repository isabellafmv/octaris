from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.serial_manager import SerialError

router = APIRouter()


class GcodeSendRequest(BaseModel):
    line: str


class GcodeSendResponse(BaseModel):
    status: str
    response: str


@router.post("/gcode/send", response_model=GcodeSendResponse)
async def send_gcode(body: GcodeSendRequest, request: Request):
    """Send a raw G-code line to the printer via the priority lane and return the response."""
    serial_manager = request.app.state.serial_manager

    if not serial_manager.is_connected:
        raise HTTPException(status_code=400, detail="Printer not connected")

    line = body.line.strip()
    if not line:
        raise HTTPException(status_code=400, detail="Empty G-code line")

    try:
        response = await serial_manager.send_line(line)
        return GcodeSendResponse(status="ok", response=response)
    except SerialError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/gcode/log")
async def get_serial_log(request: Request, limit: int = 200):
    """Return the most recent serial log entries."""
    serial_manager = request.app.state.serial_manager
    entries = serial_manager.log_buffer

    # Return the last `limit` entries
    if limit > 0:
        entries = entries[-limit:]

    return {"entries": entries}
