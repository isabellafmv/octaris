import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile

from backend.gcode_processor import GcodeValidationError, SyringeMode, process_gcode
from backend.slicer import SlicingError, slice_model

router = APIRouter()

# Store processed gcode in app state for print start
DATA_DIR = Path(tempfile.gettempdir()) / "octaris"


ACCEPTED_MODEL_EXTENSIONS = (".stl", ".3mf")


@router.post("/upload")
async def upload_model(
    request: Request,
    file: UploadFile,
    syringe_mode: str = "left",
    nozzle_diameter: float | None = None,
    syringe_diameter: float | None = None,
    layer_height: float | None = None,
    pressurize_mm: float | None = None,
    flow_multiplier: float | None = None,
    travel_retract_multiplier: float | None = None,
):
    if not file.filename or not file.filename.lower().endswith(ACCEPTED_MODEL_EXTENSIONS):
        raise HTTPException(
            status_code=400,
            detail="Only .stl and .3mf files are accepted",
        )

    if syringe_mode not in ("left", "right", "both"):
        raise HTTPException(status_code=400, detail="Invalid syringe_mode")

    if nozzle_diameter is not None and nozzle_diameter <= 0:
        raise HTTPException(status_code=400, detail="nozzle_diameter must be positive")

    if syringe_diameter is not None and syringe_diameter <= 0:
        raise HTTPException(status_code=400, detail="syringe_diameter must be positive")

    if layer_height is not None and layer_height <= 0:
        raise HTTPException(status_code=400, detail="layer_height must be positive")

    if pressurize_mm is not None and pressurize_mm <= 0:
        raise HTTPException(status_code=400, detail="pressurize_mm must be positive")

    if flow_multiplier is not None and flow_multiplier <= 0:
        raise HTTPException(status_code=400, detail="flow_multiplier must be positive")

    if travel_retract_multiplier is not None and travel_retract_multiplier <= 0:
        raise HTTPException(
            status_code=400, detail="travel_retract_multiplier must be positive"
        )

    mode: SyringeMode = syringe_mode  # type: ignore

    event_bus = request.app.state.event_bus
    event_bus.publish({"type": "status", "value": "slicing"})

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    model_path = DATA_DIR / file.filename
    content = await file.read()
    model_path.write_bytes(content)

    try:
        result = await slice_model(
            model_path, mode,
            nozzle_diameter=nozzle_diameter,
            syringe_diameter=syringe_diameter,
            layer_height=layer_height,
            pressurize_mm=pressurize_mm,
            flow_multiplier=flow_multiplier,
            travel_retract_multiplier=travel_retract_multiplier,
        )
    except (SlicingError, GcodeValidationError) as exc:
        event_bus.publish({"type": "status", "value": "idle"})
        raise HTTPException(status_code=500, detail=str(exc))

    # Store processed gcode, and the settings it was made with, for print start
    request.app.state.queue_worker.invalidate_checkpoint("A new file was loaded")
    request.app.state.processed_gcode = result
    request.app.state.current_filename = file.filename
    request.app.state.current_syringe_mode = mode
    request.app.state.print_source = "stl"
    request.app.state.print_settings = {
        "nozzle_diameter": nozzle_diameter,
        "syringe_diameter": syringe_diameter,
        "layer_height": layer_height,
        "pressurize_mm": pressurize_mm,
        "flow_multiplier": flow_multiplier,
        "travel_retract_multiplier": travel_retract_multiplier,
    }

    event_bus.publish({"type": "status", "value": "ready"})

    return {
        "status": "ready",
        "filename": file.filename,
        "lines_total": len(result.lines),
        "time_estimate_s": result.time_estimate_s,
        "feed_log_entries": len(result.feed_log),
        "preview_lines": result.lines[:40],
    }


@router.post("/upload/gcode")
async def upload_gcode(request: Request, file: UploadFile, syringe_mode: str = "left"):
    if not file.filename or not file.filename.lower().endswith((".gcode", ".gco")):
        raise HTTPException(status_code=400, detail="Only .gcode files are accepted")

    if syringe_mode not in ("left", "right", "both"):
        raise HTTPException(status_code=400, detail="Invalid syringe_mode")

    mode: SyringeMode = syringe_mode  # type: ignore

    content = await file.read()
    raw_gcode = content.decode("utf-8", errors="replace")

    try:
        result = process_gcode(raw_gcode, mode)
    except GcodeValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    request.app.state.queue_worker.invalidate_checkpoint("A new file was loaded")
    request.app.state.processed_gcode = result
    request.app.state.current_filename = file.filename
    request.app.state.current_syringe_mode = mode
    request.app.state.print_source = "gcode"
    request.app.state.print_settings = {}

    event_bus = request.app.state.event_bus
    event_bus.publish({"type": "status", "value": "ready"})

    return {
        "status": "ready",
        "filename": file.filename,
        "lines_total": len(result.lines),
        "time_estimate_s": result.time_estimate_s,
        "feed_log_entries": len(result.feed_log),
        "preview_lines": result.lines[:40],
    }
