from fastapi import APIRouter, HTTPException, Request

from backend.queue_worker import NotResumable, PrintStatus, QueueWorker
from backend.serial_manager import SerialError, SerialTimeout

router = APIRouter(prefix="/print")


@router.post("/start")
async def start_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    processed = getattr(request.app.state, "processed_gcode", None)

    if processed is None:
        raise HTTPException(status_code=400, detail="No G-code loaded. Upload an STL first.")

    if not request.app.state.serial_manager.is_connected:
        raise HTTPException(status_code=400, detail="Printer not connected")

    if not getattr(request.app.state, "is_calibrated", False):
        raise HTTPException(
            status_code=400,
            detail="Printer not calibrated. Jog the nozzle to position and call /calibration/zero first.",
        )

    worker.load_gcode(
        processed.lines,
        time_estimate_s=processed.time_estimate_s,
        state_before=processed.state_before or None,
        state_after=processed.state_after or None,
        extrusion_axes=processed.extrusion_axes,
        pressurize_mm=processed.pressurize_mm,
    )

    state = request.app.state
    state.history.start(
        filename=getattr(state, "current_filename", None) or "unknown",
        syringe_config=getattr(state, "current_syringe_mode", "left"),
        total_lines=worker.lines_total,
        source=getattr(state, "print_source", None),
        settings=getattr(state, "print_settings", {}),
    )
    worker.start()

    return {
        "status": "printing",
        "lines_total": worker.lines_total,
    }


@router.post("/stop")
async def stop_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    await worker.estop(end_reason="stopped")
    return _stop_result(worker)


@router.post("/estop")
async def estop_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    await worker.estop()
    return _stop_result(worker)


def _stop_result(worker: QueueWorker) -> dict:
    return {
        "status": "stopped",
        "resumable": worker.resumable,
        "reason": worker.stop_reason,
    }


@router.post("/pause")
async def pause_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    worker.pause()
    return {"status": "paused"}


@router.post("/resume")
async def resume_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    if worker.status == PrintStatus.PAUSED:
        worker.resume()
        return {"status": "printing"}
    if worker.status != PrintStatus.STOPPED:
        raise HTTPException(status_code=409, detail="No paused or stopped print to resume")
    try:
        await worker.resume_from_stop()
    except NotResumable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SerialTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except SerialError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "printing"}
