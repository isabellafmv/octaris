from fastapi import APIRouter, Query, Request

from backend.database import list_sessions

router = APIRouter()


@router.get("/history")
async def get_history(request: Request, limit: int = Query(50, ge=1, le=500)):
    """Recent print sessions, newest first, with their extrusion events."""
    return {"sessions": list_sessions(request.app.state.db, limit)}
