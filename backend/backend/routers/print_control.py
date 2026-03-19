from fastapi import APIRouter, HTTPException, Request

from backend.queue_worker import QueueWorker

router = APIRouter(prefix="/print")


@router.post("/start")
async def start_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    processed = getattr(request.app.state, "processed_gcode", None)

    if processed is None:
        raise HTTPException(status_code=400, detail="No G-code loaded. Upload an STL first.")

    if not request.app.state.serial_manager.is_connected:
        raise HTTPException(status_code=400, detail="Printer not connected")

    worker.load_gcode(processed.lines)
    worker.start()

    return {
        "status": "printing",
        "lines_total": worker.lines_total,
    }


@router.post("/stop")
async def stop_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    await worker.stop()
    return {"status": "stopped"}


@router.post("/pause")
async def pause_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    worker.pause()
    return {"status": "paused"}


@router.post("/resume")
async def resume_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    worker.resume()
    return {"status": "printing"}
