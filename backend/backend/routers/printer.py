from fastapi import APIRouter, Depends

from backend.printer import PrinterProfile
from backend.routers import get_session
from backend.schemas import PrinterProfilesResponse, SelectProfileRequest
from backend.session import PrinterSession

router = APIRouter(prefix="/printer")


@router.get("", response_model=PrinterProfile)
async def get_printer(session: PrinterSession = Depends(get_session)):
    """The selected printer profile: its nozzles left to right, bed limits
    and build volume."""
    return session.profile


@router.put("", response_model=PrinterProfile)
async def select_printer(body: SelectProfileRequest, session: PrinterSession = Depends(get_session)):
    """Select another printer profile, until the backend restarts
    (config.json's printer_profile is the one it starts with). Resets the
    calibration and any resume checkpoint, and unloads the loaded print.
    Refused during a print."""
    return session.select_profile(body.profile)


@router.get("/profiles", response_model=PrinterProfilesResponse)
async def list_profiles(session: PrinterSession = Depends(get_session)):
    """Every printer profile: built in, or added in config.json."""
    return {"selected": session.profile.id, "profiles": list(session.config.profiles.values())}
