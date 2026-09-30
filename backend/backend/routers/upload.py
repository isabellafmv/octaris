from fastapi import APIRouter, Depends, HTTPException, UploadFile

from backend.gcode_processor import GcodeValidationError, ProcessedGcode
from backend.limits import LimitError
from backend.routers import get_session
from backend.schemas import UploadResult
from backend.session import PrinterSession
from backend.slicer import SlicingError

router = APIRouter()

ACCEPTED_MODEL_EXTENSIONS = (".stl", ".3mf")
SYRINGE_MODES = ("left", "right", "both")


def _check_positive(**values: float | None) -> None:
    for name, value in values.items():
        if value is not None and value <= 0:
            raise HTTPException(status_code=400, detail=f"{name} must be positive")


def _upload_result(filename: str, gcode: ProcessedGcode) -> UploadResult:
    return UploadResult(
        status="ready",
        filename=filename,
        lines_total=len(gcode.lines),
        time_estimate_s=gcode.time_estimate_s,
        feed_log_entries=len(gcode.feed_log),
        preview_lines=gcode.lines[:40],
    )


@router.post("/upload", response_model=UploadResult)
async def upload_model(
    file: UploadFile,
    syringe_mode: str = "left",
    nozzle_diameter: float | None = None,
    syringe_diameter: float | None = None,
    layer_height: float | None = None,
    pressurize_mm: float | None = None,
    flow_multiplier: float | None = None,
    travel_retract_multiplier: float | None = None,
    session: PrinterSession = Depends(get_session),
):
    if not file.filename or not file.filename.lower().endswith(ACCEPTED_MODEL_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Only .stl and .3mf files are accepted")
    if syringe_mode not in SYRINGE_MODES:
        raise HTTPException(status_code=400, detail="Invalid syringe_mode")
    settings = {
        "nozzle_diameter": nozzle_diameter,
        "syringe_diameter": syringe_diameter,
        "layer_height": layer_height,
        "pressurize_mm": pressurize_mm,
        "flow_multiplier": flow_multiplier,
        "travel_retract_multiplier": travel_retract_multiplier,
    }
    _check_positive(**settings)

    try:
        gcode = await session.load_model(file.filename, await file.read(), syringe_mode, settings)
    except (SlicingError, GcodeValidationError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except LimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _upload_result(file.filename, gcode)


@router.post("/upload/gcode", response_model=UploadResult)
async def upload_gcode(
    file: UploadFile, syringe_mode: str = "left", session: PrinterSession = Depends(get_session)
):
    if not file.filename or not file.filename.lower().endswith((".gcode", ".gco")):
        raise HTTPException(status_code=400, detail="Only .gcode files are accepted")
    if syringe_mode not in SYRINGE_MODES:
        raise HTTPException(status_code=400, detail="Invalid syringe_mode")

    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        gcode = session.load_gcode(file.filename, raw, syringe_mode)
    except (GcodeValidationError, LimitError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _upload_result(file.filename, gcode)
