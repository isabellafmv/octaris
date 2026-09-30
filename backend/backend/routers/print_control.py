from fastapi import APIRouter, HTTPException, Request

from backend.checkpoint import parse_m114
from backend.gcode_processor import NOZZLE_OFFSET_X, scale_flow
from backend.limits import (
    LimitError,
    check_path,
    check_plunger_travel,
    plunger_travel_needed,
    start_state,
)
from backend.queue_worker import InvalidTransition, NotResumable, PrintStatus, QueueWorker
from backend.serial_manager import SerialError, SerialTimeout

router = APIRouter(prefix="/print")

NOZZLE_OFFSET_UNMEASURED = (
    f"Right-nozzle and dual prints are disabled: the nozzle offset "
    f"(NOZZLE_OFFSET_X = {NOZZLE_OFFSET_X:g} mm) is a placeholder that hasn't been "
    f"measured. Zero at the left nozzle, jog until the right nozzle is over the "
    f"same point, and read the X distance. Set NOZZLE_OFFSET_X in "
    f"backend/backend/gcode_processor.py to it, then set "
    f'"nozzle_offset_measured": true in config.json and restart.'
)


@router.post("/start")
async def start_print(request: Request):
    worker: QueueWorker = request.app.state.queue_worker
    processed = getattr(request.app.state, "processed_gcode", None)

    if processed is None:
        raise HTTPException(status_code=400, detail="No G-code loaded. Upload an STL first.")

    if worker.print_active:
        raise HTTPException(status_code=409, detail="A print is already running")
    await worker.wait_until_sent()

    if not request.app.state.serial_manager.is_connected:
        raise HTTPException(status_code=400, detail="Printer not connected")

    if not getattr(request.app.state, "is_calibrated", False):
        raise HTTPException(
            status_code=400,
            detail="Printer not calibrated. Jog the nozzle to position and call /calibration/zero first.",
        )

    state = request.app.state
    mode = getattr(state, "current_syringe_mode", "left")
    if mode in ("right", "both") and not state.config.nozzle_offset_measured:
        raise HTTPException(status_code=400, detail=NOZZLE_OFFSET_UNMEASURED)

    # The printer's actual position, so relative moves can be checked too.
    # Also seeds the worker's as-sent tracker (see QueueWorker.start).
    try:
        reply = await state.serial_manager.send("M114")
    except SerialTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except SerialError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    position = parse_m114(reply)
    if position is None:
        raise HTTPException(
            status_code=500,
            detail=f"Couldn't read the printer's position (M114 replied {reply!r})",
        )

    # Checked before load_gcode, which would drop a resumable checkpoint.
    try:
        check_path(state.config.bed, processed.lines, start_state(position))
        # As it will be sent: the flow override scales every B/C value.
        lines = [scale_flow(line, worker.flow_rate / 100.0) for line in processed.lines]
        needed = plunger_travel_needed(lines, start_state(position))
        check_plunger_travel(needed, state.config.syringe_travel_mm)
    except LimitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        # Another start may have got in while M114 was being read.
        worker.load_gcode(
            processed.lines,
            time_estimate_s=processed.time_estimate_s,
            state_before=processed.state_before or None,
            state_after=processed.state_after or None,
            extrusion_axes=processed.extrusion_axes,
            pressurize_mm=processed.pressurize_mm,
        )
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    state.history.start(
        filename=getattr(state, "current_filename", None) or "unknown",
        syringe_config=getattr(state, "current_syringe_mode", "left"),
        total_lines=worker.lines_total,
        source=getattr(state, "print_source", None),
        settings=getattr(state, "print_settings", {}),
    )
    worker.start(start_position=position)

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
    try:
        worker.pause()
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail="No running print to pause") from exc
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
