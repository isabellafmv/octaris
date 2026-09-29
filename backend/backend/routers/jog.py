from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.queue_worker import PrintStatus, QueueWorker
from backend.serial_manager import SerialError, SerialManager, SerialTimeout

router = APIRouter()

VALID_AXES = {"X", "Y", "Z", "A", "B", "C"}


class JogRequest(BaseModel):
    axis: str
    distance: float
    feed_rate: float = 300


@router.post("/jog")
async def jog(request: Request, body: JogRequest):
    axis = body.axis.upper()
    if axis not in VALID_AXES:
        raise HTTPException(status_code=400, detail=f"Invalid axis: {axis}")

    worker: QueueWorker = request.app.state.queue_worker
    if worker.status == PrintStatus.PRINTING:
        raise HTTPException(status_code=409, detail="Pause the print first")

    serial: SerialManager = request.app.state.serial_manager
    if not serial.is_connected:
        raise HTTPException(status_code=400, detail="Printer not connected")

    if axis in ("B", "C"):
        worker.invalidate_checkpoint(f"The {axis} plunger was jogged")
    try:
        await serial.send_lines(
            ["G91", f"G1 {axis}{body.distance} F{body.feed_rate}", "G90"]
        )
    except SerialTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except SerialError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"status": "ok", "axis": axis, "distance": body.distance}
