import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.events import EventBus

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    event_bus: EventBus = ws.app.state.event_bus

    sub_id, queue = event_bus.subscribe()
    try:
        while True:
            event = await queue.get()
            await ws.send_text(json.dumps(event))
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        event_bus.unsubscribe(sub_id)
