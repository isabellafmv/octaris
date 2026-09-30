from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.routers.ws import build_status_snapshot
from backend.serial_manager import SerialError

router = APIRouter()


@router.get("/status")
async def status(request: Request):
    return build_status_snapshot(request.app)


class ConnectRequest(BaseModel):
    port: str


def _refuse_during_print(request: Request) -> None:
    # (Re)opening or closing the port can reset the board mid-print.
    if request.app.state.queue_worker.print_active:
        raise HTTPException(status_code=409, detail="Stop the print first")


def _reset_calibration(request: Request) -> None:
    """Opening the port can reset the board, which loses the G92 zero."""
    request.app.state.is_calibrated = False
    request.app.state.event_bus.publish({"type": "calibration", "value": "uncalibrated"})


@router.get("/ports")
async def list_ports(request: Request):
    from backend.serial_manager import SerialManager

    manager: SerialManager = request.app.state.serial_manager
    ports = manager.list_ports()
    return {"ports": ports}


@router.post("/connect")
async def connect(request: Request, body: ConnectRequest):
    from backend.serial_manager import SerialManager

    _refuse_during_print(request)
    manager: SerialManager = request.app.state.serial_manager
    config = request.app.state.config
    try:
        await manager.connect(body.port, config.baud_rate)
    except SerialError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    request.app.state.queue_worker.invalidate_checkpoint("The printer was reconnected")

    # Auto-send steps/mm calibration (EEPROM disabled on this board)
    try:
        await manager.send_line("M92 X800 Y800 Z800 A800 B800 C800")
    except SerialError:
        pass  # non-fatal — printer still usable

    request.app.state.event_bus.publish(
        {"type": "printer", "connected": True, "port": body.port}
    )
    _reset_calibration(request)
    return {"status": "connected", "port": body.port}


@router.post("/disconnect")
async def disconnect(request: Request):
    from backend.serial_manager import SerialManager

    _refuse_during_print(request)
    manager: SerialManager = request.app.state.serial_manager
    await manager.disconnect()
    request.app.state.queue_worker.invalidate_checkpoint("The printer disconnected")
    request.app.state.event_bus.publish(
        {"type": "printer", "connected": False, "port": None}
    )
    _reset_calibration(request)
    return {"status": "disconnected"}
