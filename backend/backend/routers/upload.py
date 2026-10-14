from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile

from backend.gcode_processor import GcodeValidationError, ProcessedGcode
from backend.limits import LimitError
from backend.routers import get_session
from backend.schemas import UploadResult
from backend.session import PrinterSession
from backend.slicer import PrintSettings, SlicingError

router = APIRouter()

ACCEPTED_MODEL_EXTENSIONS = (".stl", ".3mf")
# Plain text G-code (.txt) is read exactly like .gcode
ACCEPTED_GCODE_EXTENSIONS = (".gcode", ".gco", ".txt")
# The nozzles to print with, by id (repeat the parameter for several:
# ?nozzles=left&nozzles=right). Default: the printer profile's first.
NozzlesParam = Query(None, description="Ids of the nozzles to print with; default: the profile's first")


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
        warnings=gcode.warnings,
    )


@router.post("/upload", response_model=UploadResult)
async def upload_model(
    file: UploadFile,
    nozzles: list[str] | None = NozzlesParam,
    nozzle_diameter: float | None = None,
    syringe_diameter: float | None = None,
    layer_height: float | None = None,
    pressurize_mm: float | None = None,
    flow_multiplier: float | None = None,
    travel_retract_multiplier: float | None = None,
    print_speed: float | None = None,
    session: PrinterSession = Depends(get_session),
):
    if not file.filename or not file.filename.lower().endswith(ACCEPTED_MODEL_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Only .stl and .3mf files are accepted")
    ids = session.select_nozzles(nozzles)
    settings: PrintSettings = {
        "nozzle_diameter": nozzle_diameter,
        "syringe_diameter": syringe_diameter,
        "layer_height": layer_height,
        "pressurize_mm": pressurize_mm,
        "flow_multiplier": flow_multiplier,
        "travel_retract_multiplier": travel_retract_multiplier,
        "print_speed": print_speed,
    }
    _check_positive(**settings)
    max_feed = session.config.max_feed_mm_min
    # The client sends the feed rate typed in mm/min divided by 60, so allow
    # for rounding (400 mm/min must not come back as 400.00000000000006)
    if print_speed is not None and print_speed * 60 > max_feed + 1e-6:
        raise HTTPException(
            status_code=400,
            detail=f"Feed rate {print_speed * 60:g} mm/min ({print_speed:g} mm/s) is above the limit of "
            f"{max_feed:g} mm/min (max_feed_mm_min in config.json)",
        )

    try:
        gcode = await session.load_model(file.filename, await file.read(), ids, settings)
    except (SlicingError, GcodeValidationError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except LimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _upload_result(file.filename, gcode)


@router.post("/upload/gcode", response_model=UploadResult)
async def upload_gcode(
    file: UploadFile,
    nozzles: list[str] | None = NozzlesParam,
    # False: sent exactly as uploaded, only checked (see check_as_uploaded)
    needs_changes: bool = True,
    session: PrinterSession = Depends(get_session),
):
    if not file.filename or not file.filename.lower().endswith(ACCEPTED_GCODE_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Only .gcode and .txt files are accepted")
    ids = session.select_nozzles(nozzles)

    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        gcode = session.load_gcode(file.filename, raw, ids, needs_changes)
    except (GcodeValidationError, LimitError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _upload_result(file.filename, gcode)
