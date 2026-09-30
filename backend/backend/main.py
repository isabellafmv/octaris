import logging
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.auth import get_token, token_is_valid
from backend.config import Config, load_config
from backend.database import init_db
from backend.events import EventBus
from backend.history import PrintHistory
from backend.limits import LimitError
from backend.queue_worker import InvalidTransition, NotResumable, QueueWorker
from backend.routers.calibration import router as calibration_router
from backend.routers.extrusion import router as extrusion_router
from backend.routers.gcode import router as gcode_router
from backend.routers.history import router as history_router
from backend.routers.jog import router as jog_router
from backend.routers.print_control import router as print_router
from backend.routers.serial import router as serial_router
from backend.routers.upload import router as upload_router
from backend.routers.ws import router as ws_router
from backend.serial_manager import SerialError, SerialManager, SerialTimeout
from backend.session import Conflict, NotReady, PrinterSession

logger = logging.getLogger(__name__)


def wire(app: FastAPI, config: Config, db: sqlite3.Connection) -> None:
    """Build the app's components on app.state. Also used by the tests."""
    event_bus = EventBus()
    history = PrintHistory(db)
    serial_manager = SerialManager(
        on_event=event_bus.publish,
        can_reconnect=lambda: session.can_reconnect(),
    )
    queue_worker = QueueWorker(
        serial_manager=serial_manager,
        on_event=event_bus.publish,
        retract_on_estop=config.retract_on_estop,
        syringe_travel_mm=config.syringe_travel_mm,
    )
    session = PrinterSession(config, serial_manager, queue_worker, history, event_bus.publish)
    event_bus.listen(session.on_event)
    event_bus.listen(history.on_event)

    app.state.config = config
    app.state.db = db
    app.state.event_bus = event_bus
    app.state.history = history
    app.state.serial_manager = serial_manager
    app.state.queue_worker = queue_worker
    app.state.session = session


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not get_token():
        logger.warning(
            "OCTARIS_TOKEN is not set — request authentication is disabled (dev mode)"
        )
    wire(app, load_config(), init_db())
    yield
    if app.state.serial_manager.is_connected:
        await app.state.serial_manager.disconnect()
    app.state.db.close()


app = FastAPI(title="Octaris Bioprinter", version="0.1.0", lifespan=lifespan)

app.include_router(serial_router)
app.include_router(calibration_router)
app.include_router(upload_router)
app.include_router(print_router)
app.include_router(extrusion_router)
app.include_router(jog_router)
app.include_router(gcode_router)
app.include_router(history_router)
app.include_router(ws_router)

# HTTP status for each error the session and the printer raise. A route
# that needs a different status for one of them catches it itself.
ERROR_STATUS: dict[type[Exception], int] = {
    NotReady: 400,
    LimitError: 400,
    Conflict: 409,
    InvalidTransition: 409,
    NotResumable: 409,
    SerialError: 500,
    SerialTimeout: 504,
}


def _error_handler(status_code: int):
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content={"detail": str(exc)})

    return handle


for _error, _status in ERROR_STATUS.items():
    app.add_exception_handler(_error, _error_handler(_status))


@app.middleware("http")
async def require_token(request: Request, call_next):
    """Reject requests missing the per-launch X-Octaris-Token header.

    Added before the CORS middleware below so CORS wraps it and still
    stamps Access-Control-* headers on the 401 responses it returns.
    """
    if request.method == "OPTIONS" or request.url.path == "/":
        return await call_next(request)
    if not token_is_valid(request.headers.get("x-octaris-token")):
        return JSONResponse(status_code=401, content={"detail": "Missing or invalid token"})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "null"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Content-Type", "X-Octaris-Token"],
)


@app.get("/")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
