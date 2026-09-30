from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.routers import get_session
from backend.serial_manager import SerialError
from backend.session import PrinterSession

router = APIRouter()


@router.get("/status")
async def status(session: PrinterSession = Depends(get_session)):
    return session.snapshot()


class ConnectRequest(BaseModel):
    port: str


@router.get("/ports")
async def list_ports(session: PrinterSession = Depends(get_session)):
    return {"ports": session.list_ports()}


@router.post("/connect")
async def connect(body: ConnectRequest, session: PrinterSession = Depends(get_session)):
    try:
        await session.connect(body.port)
    except SerialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "connected", "port": body.port}


@router.post("/disconnect")
async def disconnect(session: PrinterSession = Depends(get_session)):
    await session.disconnect()
    return {"status": "disconnected"}
