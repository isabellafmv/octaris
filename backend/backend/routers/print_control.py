from fastapi import APIRouter, Depends

from backend.routers import get_session
from backend.schemas import (
    PauseResponse,
    PrintStartResponse,
    ResumeResponse,
    StopResponse,
)
from backend.session import PrinterSession

router = APIRouter(prefix="/print")


@router.post("/start", response_model=PrintStartResponse)
async def start_print(session: PrinterSession = Depends(get_session)):
    lines_total = await session.start_print()
    return {"status": "printing", "lines_total": lines_total}


@router.post("/stop", response_model=StopResponse)
async def stop_print(session: PrinterSession = Depends(get_session)):
    resumable, reason = await session.stop(end_reason="stopped")
    return {"status": "stopped", "resumable": resumable, "reason": reason}


@router.post("/estop", response_model=StopResponse)
async def estop_print(session: PrinterSession = Depends(get_session)):
    resumable, reason = await session.stop(end_reason="estop")
    return {"status": "stopped", "resumable": resumable, "reason": reason}


@router.post("/pause", response_model=PauseResponse)
async def pause_print(session: PrinterSession = Depends(get_session)):
    session.pause()
    return {"status": "paused"}


@router.post("/resume", response_model=ResumeResponse)
async def resume_print(session: PrinterSession = Depends(get_session)):
    await session.resume()
    return {"status": "printing"}
