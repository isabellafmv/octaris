from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from backend.routers import get_session
from backend.schemas import (
    GcodeEditErrorResponse,
    GcodeEditResult,
    GcodeLineError,
    GcodeSendResponse,
    SerialLogResponse,
)
from backend.session import GcodeEditError, PrinterSession, StaleProgram

router = APIRouter()


class GcodeSendRequest(BaseModel):
    line: str


@router.post("/gcode/send", response_model=GcodeSendResponse)
async def send_gcode(body: GcodeSendRequest, session: PrinterSession = Depends(get_session)):
    """Send a raw G-code line to the printer and return its response."""
    response = await session.send_gcode(body.line)
    return GcodeSendResponse(status="ok", response=response)


@router.get("/gcode/log", response_model=SerialLogResponse)
async def get_serial_log(limit: int = 200, session: PrinterSession = Depends(get_session)):
    """Return the most recent serial log entries."""
    return {"entries": session.serial_log(limit)}


def _etag(program_id: str) -> str:
    return f'"{program_id}"'


def _matches(header: str, program_id: str) -> bool:
    """Whether an If-Match / If-None-Match header names this program."""
    tags = [tag.strip().removeprefix("W/") for tag in header.split(",")]
    return "*" in tags or _etag(program_id) in tags


@router.get(
    "/gcode/loaded",
    response_class=PlainTextResponse,
    responses={
        200: {"content": {"text/plain": {"schema": {"type": "string"}}}, "description": "G-code"},
        304: {"description": "Unchanged since the ETag in If-None-Match"},
        404: {"description": "No print loaded"},
    },
)
async def get_loaded_gcode(
    if_none_match: str | None = Header(None), session: PrinterSession = Depends(get_session)
):
    """The loaded print's processed G-code, one line per line, as sent to
    the printer (before flow scaling). Its ETag is the program id, which
    changes on every load and edit."""
    if session.loaded is None or session.program_id is None:
        raise HTTPException(status_code=404, detail="No print loaded")
    headers = {"ETag": _etag(session.program_id), "Cache-Control": "no-cache"}
    if if_none_match and _matches(if_none_match, session.program_id):
        return Response(status_code=304, headers=headers)
    return PlainTextResponse("\n".join(session.loaded.gcode.lines), headers=headers)


@router.put(
    "/gcode/loaded",
    response_model=GcodeEditResult,
    openapi_extra={
        "requestBody": {"required": True, "content": {"text/plain": {"schema": {"type": "string"}}}}
    },
    responses={
        404: {"description": "No print loaded"},
        409: {"description": "A print is printing or paused"},
        412: {"description": "If-Match names a program that is no longer loaded"},
        422: {"model": GcodeEditErrorResponse, "description": "The edit failed a check"},
    },
)
async def replace_loaded_gcode(
    request: Request,
    response: Response,
    if_match: str | None = Header(None),
    session: PrinterSession = Depends(get_session),
):
    """Replace the loaded print's lines with the request body (text/plain,
    one G-code line per line, as GET /gcode/loaded sends them).

    The edit is validated, simulated and checked against the bed limits and
    the syringe travel; if it fails, nothing changes and the 422 body says
    which line. With If-Match, it is only applied to that program."""
    if session.loaded is None:
        raise HTTPException(status_code=404, detail="No print loaded")
    # The program the edit was made to: checked again once the edit is
    # checked, in case another file is loaded in the meantime
    expected = None
    if if_match and if_match.strip() != "*":
        expected = session.program_id
        if expected is None or not _matches(if_match, expected):
            raise HTTPException(status_code=412, detail="The loaded program has changed since it was opened")

    text = (await request.body()).decode("utf-8", errors="replace")
    try:
        gcode = await session.replace_loaded_lines(text, expected)
    except StaleProgram as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except GcodeEditError as exc:
        error = GcodeLineError(line=exc.line, message=str(exc))
        body = GcodeEditErrorResponse(detail=str(exc), errors=[error])
        return JSONResponse(status_code=422, content=body.model_dump())

    assert session.loaded is not None and session.program_id is not None
    response.headers["ETag"] = _etag(session.program_id)
    return GcodeEditResult(
        status="ready",
        filename=session.loaded.filename,
        lines_total=len(gcode.lines),
        time_estimate_s=gcode.time_estimate_s,
        feed_log_entries=len(gcode.feed_log),
        preview_lines=gcode.lines[:40],
        edited=True,
        warnings=gcode.warnings,
        program_id=session.program_id,
    )
