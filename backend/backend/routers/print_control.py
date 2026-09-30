from fastapi import APIRouter, Depends

from backend.routers import get_session
from backend.session import PrinterSession

router = APIRouter(prefix="/print")


@router.post("/start")
async def start_print(session: PrinterSession = Depends(get_session)):
    lines_total = await session.start_print()
    return {"status": "printing", "lines_total": lines_total}


@router.post("/stop")
async def stop_print(session: PrinterSession = Depends(get_session)):
    resumable, reason = await session.stop(end_reason="stopped")
    return {"status": "stopped", "resumable": resumable, "reason": reason}


@router.post("/estop")
async def estop_print(session: PrinterSession = Depends(get_session)):
    resumable, reason = await session.stop(end_reason="estop")
    return {"status": "stopped", "resumable": resumable, "reason": reason}


@router.post("/pause")
async def pause_print(session: PrinterSession = Depends(get_session)):
    session.pause()
    return {"status": "paused"}


@router.post("/resume")
async def resume_print(session: PrinterSession = Depends(get_session)):
    await session.resume()
    return {"status": "printing"}
