from fastapi import APIRouter, Depends, Query

from backend.routers import get_session
from backend.session import PrinterSession

router = APIRouter()


@router.get("/history")
async def get_history(
    limit: int = Query(50, ge=1, le=500), session: PrinterSession = Depends(get_session)
):
    """Recent print sessions, newest first, with their extrusion events."""
    return {"sessions": session.print_history(limit)}
