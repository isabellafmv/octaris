from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import TemperatureHistoryResponse, TemperatureStatus, TemperatureTargetResponse
from backend.session import PrinterSession
from backend.temperature import HISTORY_S

router = APIRouter(prefix="/temperature")


@router.get("", response_model=TemperatureStatus)
async def temperature(session: PrinterSession = Depends(get_session)):
    """The latest reading, target and status of every sensor. `state` says
    whether the printer is connected and has reported any sensor at all."""
    return session.temperature.status()


@router.get("/history", response_model=TemperatureHistoryResponse)
async def temperature_history(
    minutes: int | None = Query(None, ge=1, le=HISTORY_S // 60),
    session_id: int | None = None,
    session: PrinterSession = Depends(get_session),
):
    """Readings for the chart: the last `minutes`, or all of one print session's."""
    store = session.temperature
    if (minutes is None) == (session_id is None):
        raise HTTPException(status_code=400, detail="Give either minutes or session_id")
    if minutes is not None:
        readings = store.history(minutes * 60)
    else:
        readings = store.logged(session_id=session_id)
    return {"series": store.series(readings)}


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


@router.get(
    "/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}, "description": "CSV"}},
)
async def export_temperature_csv(
    session_id: int | None = None,
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    session: PrinterSession = Depends(get_session),
):
    """Logged readings of one print session, or between `from` and `to`
    (ISO 8601; UTC unless an offset is given), as CSV: timestamp, sensor,
    name, actual, target."""
    store = session.temperature
    if session_id is not None and start is None and end is None:
        readings = store.logged(session_id=session_id)
        filename = f"temperature-print-{session_id}.csv"
    elif session_id is None and start is not None and end is not None:
        readings = store.logged(start=_timestamp(start), end=_timestamp(end))
        filename = f"temperature-{start.strftime('%Y%m%d-%H%M')}-{end.strftime('%Y%m%d-%H%M')}.csv"
    else:
        raise HTTPException(status_code=400, detail="Give either session_id, or both from and to")
    return Response(
        store.csv(readings),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class TemperatureTargetRequest(BaseModel):
    sensor: str
    target: float  # °C; 0 turns the heater off


@router.post("/target", response_model=TemperatureTargetResponse)
async def set_temperature_target(
    body: TemperatureTargetRequest, session: PrinterSession = Depends(get_session)
):
    """Set a heater's target with M104 (T, T<n>), M140 (B) or M141 (C).
    Allowed while printing."""
    command = await session.set_temperature_target(body.sensor, body.target)
    return {"status": "ok", "sensor": body.sensor, "target": body.target, "command": command}
