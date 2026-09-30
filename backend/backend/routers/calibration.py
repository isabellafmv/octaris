from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import CalibrateResponse, CalibrationResetResponse, CalibrationStatusResponse
from backend.session import PrinterSession

router = APIRouter(prefix="/calibration")


class CalibrateRequest(BaseModel):
    """Optional overrides for the zeroing command."""
    zero_z: bool = True
    zero_b: bool = True
    zero_c: bool = False


@router.get("/status", response_model=CalibrationStatusResponse)
async def calibration_status(session: PrinterSession = Depends(get_session)):
    """Check whether the printer has been calibrated this session."""
    return {"calibrated": session.calibrated}


@router.post("/zero", response_model=CalibrateResponse)
async def calibrate_zero(
    body: CalibrateRequest | None = None, session: PrinterSession = Depends(get_session)
):
    """
    Set the current nozzle position as the origin.

    IMPORTANT: Always zero using the LEFT nozzle, even when printing with
    the right nozzle or both. The software automatically applies the nozzle
    offset (NOZZLE_OFFSET_X) for the right nozzle.

    Jog the LEFT nozzle to the center of the print area at the correct Z
    height (~0.2 mm above surface) before calling this endpoint.
    """
    body = body or CalibrateRequest()
    command = await session.calibrate(body.zero_z, body.zero_b, body.zero_c)
    return {"status": "calibrated", "command": command}


@router.post("/reset", response_model=CalibrationResetResponse)
async def reset_calibration(session: PrinterSession = Depends(get_session)):
    """Mark calibration as invalid (e.g. after a disconnect or power cycle)."""
    session.reset_calibration()
    return {"status": "uncalibrated"}
