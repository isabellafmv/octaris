from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import CalibrateResponse, CalibrationResetResponse, CalibrationStatusResponse
from backend.session import PrinterSession

router = APIRouter(prefix="/calibration")


class CalibrateRequest(BaseModel):
    """Which nozzle to zero (default: the first selected), for which
    selection of nozzles, by id (default: the loaded print's, or the last
    calibration's)."""

    nozzle: str | None = None
    nozzles: list[str] | None = None


@router.get("/status", response_model=CalibrationStatusResponse)
async def calibration_status(session: PrinterSession = Depends(get_session)):
    """Whether every selected nozzle is zeroed, and which of the profile's
    nozzles are."""
    return {"calibrated": session.calibrated, "nozzles": session.nozzles_calibrated()}


@router.post("/zero", response_model=CalibrateResponse)
async def calibrate_zero(
    body: CalibrateRequest | None = None, session: PrinterSession = Depends(get_session)
):
    """
    Zero one of the selected nozzles where it is. X/Y are set at the first
    (leftmost) selected nozzle, over the print's start point; each nozzle's
    height with that nozzle lowered onto the bed (about 0.2 mm above the
    surface). Printess 1.0:

    - `nozzles: ["left"]`, `nozzle: "left"` → G92 X0 Y0 Z0 B0
    - `nozzles: ["right"]`, `nozzle: "right"` → G92 X0 Y0 A0 C0 (right nozzle
      over the start point and on the bed)
    - `nozzles: ["left", "right"]`, two steps: `nozzle: "left"` → G92 X0 Y0
      Z0 B0, then lower the right nozzle onto the bed and `nozzle: "right"`
      → G92 A0 C0

    Prints aren't shifted by the nozzle offsets: each nozzle prints its own
    copy of the same path, in place.
    """
    body = body or CalibrateRequest()
    command = await session.calibrate(body.nozzle, body.nozzles)
    return {
        "command": command,
        "calibrated": session.calibrated,
        "nozzles": session.nozzles_calibrated(),
    }


@router.post("/reset", response_model=CalibrationResetResponse)
async def reset_calibration(session: PrinterSession = Depends(get_session)):
    """Mark calibration as invalid (e.g. after a disconnect or power cycle)."""
    session.reset_calibration()
    return {"status": "uncalibrated"}
