from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.queue_worker import QueueWorker

router = APIRouter()

VALID_AXES = {"X", "Y", "Z", "A", "B", "C"}
AXES_BY_MODE = {
    "left": {"X", "Y", "Z", "B"},
    "right": {"X", "Y", "A", "C"},
    "both": {"X", "Y", "Z", "A", "B", "C"},
}


class JogRequest(BaseModel):
    axis: str
    distance: float
    feed_rate: float = 200


@router.post("/jog")
async def jog(request: Request, body: JogRequest):
    axis = body.axis.upper()
    if axis not in VALID_AXES:
        raise HTTPException(status_code=400, detail=f"Invalid axis: {axis}")

    current_mode = getattr(request.app.state, "current_syringe_mode", "both")
    allowed = AXES_BY_MODE.get(current_mode, VALID_AXES)
    if axis not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Axis {axis} not available in {current_mode} syringe mode",
        )

    worker: QueueWorker = request.app.state.queue_worker
    # Send as relative move
    worker.enqueue_priority("G91")
    worker.enqueue_priority(f"G0 {axis}{body.distance} F{body.feed_rate}")

    return {"status": "ok", "axis": axis, "distance": body.distance}
