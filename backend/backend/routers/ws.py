import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.auth import token_is_valid
from backend.events import EventBus
from backend.schemas import SnapshotEvent

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not token_is_valid(ws.query_params.get("token")):
        await ws.close(code=1008)
        return

    await ws.accept()
    event_bus: EventBus = ws.app.state.event_bus

    snapshot = SnapshotEvent(**ws.app.state.session.snapshot().model_dump())
    await ws.send_text(json.dumps(snapshot.dump()))

    sub_id, queue = event_bus.subscribe()
    # Events are sent until the connection ends. That is noticed by reading
    # from it: the client closing it, and the server shutting down (uvicorn
    # waits for this handler to return before it runs the lifespan cleanup),
    # both arrive as a disconnect message, and nothing else would see it.
    forward = asyncio.create_task(_forward(ws, queue))
    closed = asyncio.create_task(_until_closed(ws))
    try:
        await asyncio.wait({forward, closed}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        event_bus.unsubscribe(sub_id)
        for task in (forward, closed):
            task.cancel()
        # wait(), not gather(): if this handler is cancelled meanwhile, it
        # raises that cancellation as it is (anyio, under the test client,
        # only recognises its own)
        await asyncio.wait({forward, closed})


async def _forward(ws: WebSocket, queue: asyncio.Queue) -> None:
    try:
        while True:
            event = await queue.get()
            await ws.send_text(json.dumps(event))
    except (WebSocketDisconnect, Exception):
        pass  # the connection is gone


async def _until_closed(ws: WebSocket) -> None:
    """Return once the connection is closed (the client sends nothing else)."""
    try:
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    except (WebSocketDisconnect, Exception):
        pass
