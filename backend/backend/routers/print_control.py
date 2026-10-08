from fastapi import APIRouter, Depends

from backend.routers import get_session
from backend.schemas import (
    PauseResponse,
    PrintStartRequest,
    PrintStartResponse,
    ResumeResponse,
    StopResponse,
)
from backend.session import PrinterSession

router = APIRouter(prefix="/print")


@router.post("/start", response_model=PrintStartResponse)
async def start_print(body: PrintStartRequest | None = None, session: PrinterSession = Depends(get_session)):
    body = body or PrintStartRequest()
    lines_total = await session.start_print(body.wait_for_temperature)
    return {"status": "printing", "lines_total": lines_total}


@router.post("/stop", response_model=StopResponse)
async def stop_print(session: PrinterSession = Depends(get_session)):
    """Stop at once: M410 goes straight to the printer, then the stop point
    is located so the print can be resumed from there."""
    resumable, reason = await session.stop()
    return {"status": "stopped", "resumable": resumable, "reason": reason}


@router.post("/pause", response_model=PauseResponse)
async def pause_print(session: PrinterSession = Depends(get_session)):
    session.pause()
    return {"status": "paused"}


@router.post("/resume", response_model=ResumeResponse)
async def resume_print(session: PrinterSession = Depends(get_session)):
    await session.resume()
    return {"status": "printing"}
