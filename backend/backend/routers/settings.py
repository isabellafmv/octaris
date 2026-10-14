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
    """Save the settings; they apply at once and are kept across restarts.
    Turning temperature_sensors on or off starts or stops temperature
    monitoring right away, connected or not."""
    try:
        return session.update_settings(body)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Couldn't save the settings: {exc}") from exc
