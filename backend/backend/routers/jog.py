from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.serial_manager import SerialError, SerialManager

router = APIRouter()

VALID_AXES = {"X", "Y", "Z", "A", "B", "C"}
AXES_BY_MODE = {
    "left": {"X", "Y", "Z", "B"},
    "right": {"X", "Y", "A", "C"},
    "both": {"X", "Y", "Z", "A", "B", "C"},
}


class JogRequest(BaseModel):
    axis: str
    distance: float
    feed_rate: float = 300


@router.post("/jog")
async def jog(request: Request, body: JogRequest):
    axis = body.axis.upper()
    if axis not in VALID_AXES:
        raise HTTPException(status_code=400, detail=f"Invalid axis: {axis}")

    current_mode = getattr(request.app.state, "current_syringe_mode", "both")
    allowed = AXES_BY_MODE.get(current_mode, VALID_AXES)
    if axis not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Axis {axis} not available in {current_mode} syringe mode",
        )

    serial: SerialManager = request.app.state.serial_manager
    if not serial.is_connected:
        raise HTTPException(status_code=400, detail="Printer not connected")

    try:
        await serial.send_line("G91")
        await serial.send_line(f"G1 {axis}{body.distance} F{body.feed_rate}")
    except SerialError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"status": "ok", "axis": axis, "distance": body.distance}
