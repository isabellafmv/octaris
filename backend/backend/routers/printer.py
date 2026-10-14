from fastapi import APIRouter, Depends, HTTPException

from backend.printer import CUSTOM_PROFILE_ID, PrinterProfile
from backend.routers import get_session
from backend.schemas import CustomProfileRequest, PrinterProfilesResponse, SelectProfileRequest
from backend.session import PrinterSession

router = APIRouter(prefix="/printer")


@router.get("", response_model=PrinterProfile)
async def get_printer(session: PrinterSession = Depends(get_session)):
    """The selected printer profile: its nozzles left to right, bed limits
    and build volume."""
    return session.profile


@router.put("", response_model=PrinterProfile)
async def select_printer(body: SelectProfileRequest, session: PrinterSession = Depends(get_session)):
    """Select another printer profile, kept across restarts (settings.json;
    until one is chosen in the app, config.json's printer_profile is used).
    Resets the calibration and any resume checkpoint, unloads the loaded
    print, and sends a connected printer the profile's steps/mm (M92).
    Refused during a print."""
    return await session.select_profile(body.profile)


@router.get("/profiles", response_model=PrinterProfilesResponse)
async def list_profiles(session: PrinterSession = Depends(get_session)):
    """Every printer profile: built in, added in config.json, and the
    Custom one once saved."""
    return {"selected": session.profile.id, "profiles": list(session.profiles.values())}


@router.get("/custom", response_model=PrinterProfile | None)
async def get_custom_profile(session: PrinterSession = Depends(get_session)):
    """The Custom profile edited in the app; null until one is saved."""
    return session.profiles.get(CUSTOM_PROFILE_ID)


@router.put("/custom", response_model=PrinterProfile)
async def save_custom_profile(body: CustomProfileRequest, session: PrinterSession = Depends(get_session)):
    """Save the Custom profile and select it, as PUT /printer does (refused
    during a print). 400 lists every problem: each axis must be one of Z, A,
    B, C, I, J, K, U, V, W, used once in the profile, and a syringe's height
    and plunger axis must differ; 1 to 6 syringes."""
    try:
        return await session.save_custom_profile(body.nozzles, body.nozzle_offset_measured, body.base)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Couldn't save the printer profile: {exc}") from exc


@router.delete("/custom", response_model=PrinterProfile)
async def reset_custom_profile(session: PrinterSession = Depends(get_session)):
    """Delete the Custom profile and select the Printess 1.0 (refused
    during a print). Returns the Printess 1.0 profile."""
    try:
        return await session.reset_custom_profile()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Couldn't save the printer profile: {exc}") from exc
