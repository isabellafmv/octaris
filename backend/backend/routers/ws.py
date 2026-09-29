import asyncio
import json
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect

from backend.auth import token_is_valid
from backend.events import EventBus

router = APIRouter()


def build_status_snapshot(app: FastAPI) -> dict[str, Any]:
    """Current connection/print state, shared by GET /status and the ws snapshot."""
    serial_manager = app.state.serial_manager
    worker = app.state.queue_worker
    processed = getattr(app.state, "processed_gcode", None)

    return {
        "printer_connected": serial_manager.is_connected,
        "port": serial_manager.port,
        "print_status": worker.status.value,
        "lines_sent": worker.lines_sent,
        "lines_total": worker.lines_total,
        "calibrated": getattr(app.state, "is_calibrated", False),
        "flow_rate": worker.flow_rate,
        "resumable": worker.resumable,
        "stop_reason": worker.stop_reason,
        "time_estimate_s": processed.time_estimate_s if processed is not None else None,
    }


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not token_is_valid(ws.query_params.get("token")):
        await ws.close(code=1008)
        return

    await ws.accept()
    event_bus: EventBus = ws.app.state.event_bus

    await ws.send_text(json.dumps({"type": "snapshot", **build_status_snapshot(ws.app)}))

    sub_id, queue = event_bus.subscribe()
    try:
        while True:
            event = await queue.get()
            await ws.send_text(json.dumps(event))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        event_bus.unsubscribe(sub_id)
