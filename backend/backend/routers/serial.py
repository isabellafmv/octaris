from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import ConnectResponse, DisconnectResponse, PortsResponse, Snapshot
from backend.serial_manager import SerialError
from backend.session import PrinterSession

router = APIRouter()


@router.get("/status", response_model=Snapshot)
async def status(session: PrinterSession = Depends(get_session)):
    return session.snapshot()


class ConnectRequest(BaseModel):
    port: str


@router.get("/ports", response_model=PortsResponse)
async def list_ports(session: PrinterSession = Depends(get_session)):
    return {"ports": session.list_ports()}


@router.post("/connect", response_model=ConnectResponse)
async def connect(body: ConnectRequest, session: PrinterSession = Depends(get_session)):
    try:
        await session.connect(body.port)
    except SerialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "connected", "port": body.port}


@router.post("/disconnect", response_model=DisconnectResponse)
async def disconnect(session: PrinterSession = Depends(get_session)):
    await session.disconnect()
    return {"status": "disconnected"}
