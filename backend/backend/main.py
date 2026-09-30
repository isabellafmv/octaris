import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.auth import get_token, token_is_valid
from backend.config import load_config
from backend.database import init_db
from backend.events import EventBus
from backend.history import PrintHistory
from backend.queue_worker import QueueWorker
from backend.routers.calibration import router as calibration_router
from backend.routers.extrusion import router as extrusion_router
from backend.routers.gcode import router as gcode_router
from backend.routers.history import router as history_router
from backend.routers.jog import router as jog_router
from backend.routers.print_control import router as print_router
from backend.routers.serial import router as serial_router
from backend.routers.upload import router as upload_router
from backend.routers.ws import router as ws_router
from backend.serial_manager import SerialManager

logger = logging.getLogger(__name__)


def make_serial_manager(app: FastAPI) -> SerialManager:
    """The app's SerialManager, wired to the event bus, calibration state and
    queue worker (all looked up on app.state when the callbacks fire)."""

    def _reset_calibration():
        app.state.is_calibrated = False
        app.state.event_bus.publish({"type": "calibration", "value": "uncalibrated"})

    def _on_disconnect():
        # Stops a running print for good (the board may reset on reconnect).
        app.state.queue_worker.connection_lost()
        app.state.event_bus.publish({"type": "printer", "connected": False, "port": None})
        _reset_calibration()

    def _on_connect(port: str):
        app.state.event_bus.publish({"type": "printer", "connected": True, "port": port})
        # Opening the port may have reset the board, losing the G92 zero.
        _reset_calibration()

    return SerialManager(
        on_disconnect=_on_disconnect,
        on_serial_log=lambda entry: app.state.event_bus.publish(entry),
        on_connect=_on_connect,
        # Never reconnect automatically during a print; see connection_lost().
        can_reconnect=lambda: not app.state.queue_worker.print_active,
    )


def make_queue_worker(app: FastAPI) -> QueueWorker:
    return QueueWorker(
        serial_manager=app.state.serial_manager,
        on_event=app.state.event_bus.publish,
        on_print_end=app.state.history.end,
        on_print_resumed=app.state.history.reopen,
        retract_on_estop=app.state.config.retract_on_estop,
        syringe_travel_mm=app.state.config.syringe_travel_mm,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not get_token():
        logger.warning(
            "OCTARIS_TOKEN is not set — request authentication is disabled (dev mode)"
        )
    app.state.config = load_config()
    app.state.event_bus = EventBus()
    app.state.serial_manager = make_serial_manager(app)
    app.state.db = init_db()
    app.state.history = PrintHistory(app.state.db)
    app.state.queue_worker = make_queue_worker(app)
    app.state.processed_gcode = None
    app.state.print_source = None
    app.state.print_settings = {}
    app.state.current_filename = None
    app.state.current_syringe_mode = "left"
    app.state.is_calibrated = False
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
