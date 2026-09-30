from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.gcode_processor import parse_words
from backend.queue_worker import PrintStatus, QueueWorker
from backend.serial_manager import SerialError, SerialTimeout

router = APIRouter()


class GcodeSendRequest(BaseModel):
    line: str


class GcodeSendResponse(BaseModel):
    status: str
    response: str


@router.post("/gcode/send", response_model=GcodeSendResponse)
async def send_gcode(body: GcodeSendRequest, request: Request):
    """Send a raw G-code line to the printer via the priority lane and return the response."""
    worker: QueueWorker = request.app.state.queue_worker
    if worker.status == PrintStatus.PRINTING:
        raise HTTPException(status_code=409, detail="Pause the print first")

    serial_manager = request.app.state.serial_manager

    if not serial_manager.is_connected:
        raise HTTPException(status_code=400, detail="Printer not connected")

    line = body.line.strip()
    if not line:
        raise HTTPException(status_code=400, detail="Empty G-code line")

    if worker.status == PrintStatus.PAUSED and line.upper().startswith("G92"):
        raise HTTPException(status_code=409, detail="Can't re-zero during a print")

    reason = _invalidates_checkpoint(line)
    if reason:
        worker.invalidate_checkpoint(reason)

    try:
        response = await serial_manager.send(line)
        return GcodeSendResponse(status="ok", response=response)
    except SerialTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except SerialError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _invalidates_checkpoint(line: str) -> str | None:
    """Why a manual line would make an e-stop checkpoint unusable, if it does."""
    words = parse_words(line)
    if not words or words[0][0] != "G":
        return None
    if words[0][1] == 92:
        return "G92 changed the coordinate system"
    if words[0][1] in (0, 1) and any(letter in ("B", "C") for letter, _ in words[1:]):
        return "A plunger was moved manually"
    return None


@router.get("/gcode/log")
async def get_serial_log(request: Request, limit: int = 200):
    """Return the most recent serial log entries."""
    serial_manager = request.app.state.serial_manager
    entries = serial_manager.log_buffer

    # Return the last `limit` entries
    if limit > 0:
        entries = entries[-limit:]

    return {"entries": entries}
