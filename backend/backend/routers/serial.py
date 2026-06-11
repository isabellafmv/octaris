from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.serial_manager import SerialError

router = APIRouter()


class ConnectRequest(BaseModel):
    port: str


@router.get("/ports")
async def list_ports(request: Request):
    from backend.serial_manager import SerialManager

    manager: SerialManager = request.app.state.serial_manager
    ports = manager.list_ports()
    return {"ports": ports}


@router.post("/connect")
async def connect(request: Request, body: ConnectRequest):
    from backend.serial_manager import SerialManager

    manager: SerialManager = request.app.state.serial_manager
    config = request.app.state.config
    try:
        await manager.connect(body.port, config.baud_rate)
    except SerialError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Auto-send steps/mm calibration (EEPROM disabled on this board)
    try:
        await manager.send_line("M92 X800 Y800 Z800 A800 B800 C800")
    except SerialError:
        pass  # non-fatal — printer still usable

    return {"status": "connected", "port": body.port}


@router.post("/disconnect")
async def disconnect(request: Request):
    from backend.serial_manager import SerialManager

    manager: SerialManager = request.app.state.serial_manager
    await manager.disconnect()
    return {"status": "disconnected"}
