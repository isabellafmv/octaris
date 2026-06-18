from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.serial_manager import SerialError, SerialManager

router = APIRouter(prefix="/calibration")


class CalibrateRequest(BaseModel):
    """Optional overrides for the zeroing command."""
    zero_z: bool = True
    zero_b: bool = True
    zero_c: bool = False


@router.get("/status")
async def calibration_status(request: Request):
    """Check whether the printer has been calibrated this session."""
    return {"calibrated": getattr(request.app.state, "is_calibrated", False)}


@router.post("/zero")
async def calibrate_zero(request: Request, body: CalibrateRequest | None = None):
    """
    Set the current nozzle position as the origin.

    IMPORTANT: Always zero using the LEFT nozzle, even when printing with
    the right nozzle or both. The software automatically applies the nozzle
    offset (NOZZLE_OFFSET_X) for the right nozzle.

    Jog the LEFT nozzle to the center of the print area at the correct Z
    height (~0.2 mm above surface) before calling this endpoint.
    """
    serial: SerialManager = request.app.state.serial_manager
    if not serial.is_connected:
        raise HTTPException(status_code=400, detail="Printer not connected")

    if body is None:
        body = CalibrateRequest()

    # Build the G92 command based on the current syringe mode and request
    mode = getattr(request.app.state, "current_syringe_mode", "left")

    axes = "X0 Y0"
    if body.zero_z:
        z_axis = "Z" if mode in ("left", "both") else "A"
        axes += f" {z_axis}0"
    if body.zero_b:
        axes += " B0"
    if body.zero_c or mode == "both":
        axes += " C0"

    try:
        await serial.send_line(f"G92 {axes}")
    except SerialError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    request.app.state.is_calibrated = True

    event_bus = request.app.state.event_bus
    event_bus.publish({"type": "calibration", "value": "calibrated"})

    return {"status": "calibrated", "command": f"G92 {axes}"}


@router.post("/reset")
async def reset_calibration(request: Request):
    """Mark calibration as invalid (e.g. after a disconnect or power cycle)."""
    request.app.state.is_calibrated = False

    event_bus = request.app.state.event_bus
    event_bus.publish({"type": "calibration", "value": "uncalibrated"})

    return {"status": "uncalibrated"}
