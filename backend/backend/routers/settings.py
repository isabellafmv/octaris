from fastapi import APIRouter, Depends, HTTPException

from backend.routers import get_session
from backend.schemas import Settings
from backend.session import PrinterSession

router = APIRouter(prefix="/settings")


@router.get("", response_model=Settings)
async def get_settings(session: PrinterSession = Depends(get_session)):
    return session.settings.current


@router.put("", response_model=Settings)
async def put_settings(body: Settings, session: PrinterSession = Depends(get_session)):
    """Save the settings given (the others stay as they are); they apply
    at once and are kept across restarts. Turning temperature_sensors on or
    off starts or stops temperature monitoring right away, connected or
    not. A new temperature_protocol forgets the readings and targets of the
    old one; 409 while a print waits for its temperatures."""
    try:
        return session.update_settings(body)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Couldn't save the settings: {exc}") from exc
