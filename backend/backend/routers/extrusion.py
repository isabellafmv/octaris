from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.queue_worker import QueueWorker

router = APIRouter()


class ExtrusionRequest(BaseModel):
    rate: int


@router.post("/extrusion")
async def set_extrusion_rate(request: Request, body: ExtrusionRequest):
    if body.rate < 50 or body.rate > 150:
        raise HTTPException(status_code=400, detail="Rate must be between 50 and 150")

    worker: QueueWorker = request.app.state.queue_worker
    worker.enqueue_priority(f"M221 S{body.rate}")

    event_bus = request.app.state.event_bus
    event_bus.publish({"type": "extrusion_rate", "value": body.rate})

    return {"status": "ok", "rate": body.rate}
