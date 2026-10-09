from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import CalibrateResponse, CalibrationResetResponse, CalibrationStatusResponse
from backend.session import PrinterSession

router = APIRouter(prefix="/calibration")


class CalibrateRequest(BaseModel):
    """Which nozzle to zero, for which syringe mode (default: the loaded
    print's, or the last calibration's)."""

    nozzle: Literal["left", "right"] | None = None
    syringe_mode: Literal["left", "right", "both"] | None = None


@router.get("/status", response_model=CalibrationStatusResponse)
async def calibration_status(session: PrinterSession = Depends(get_session)):
    """Whether the printer is calibrated for the current syringe mode, and
    which nozzles are zeroed."""
    return {"calibrated": session.calibrated, "nozzles": session.nozzles_calibrated()}


@router.post("/zero", response_model=CalibrateResponse)
async def calibrate_zero(
    body: CalibrateRequest | None = None, session: PrinterSession = Depends(get_session)
):
    """
    Zero a nozzle where it is. X/Y are always set at the LEFT nozzle's
    position over the print's start point; each nozzle's height with that
    nozzle lowered onto the bed (about 0.2 mm above the surface).

    - left mode: `nozzle: "left"` → G92 X0 Y0 Z0 B0
    - right mode: `nozzle: "right"` → G92 X0 Y0 A0 C0 (left nozzle over the
      start point, right nozzle on the bed)
    - both mode, two steps: `nozzle: "left"` → G92 X0 Y0 Z0 B0, then lower the
      right nozzle onto the bed and `nozzle: "right"` → G92 A0 C0

    The right nozzle's X offset (NOZZLE_OFFSET_X) is applied in post-processing.
    """
    body = body or CalibrateRequest()
    command = await session.calibrate(body.nozzle, body.syringe_mode)
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
