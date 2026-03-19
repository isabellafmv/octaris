from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import load_config
from backend.database import init_db
from backend.events import EventBus
from backend.queue_worker import QueueWorker
from backend.routers.extrusion import router as extrusion_router
from backend.routers.gcode import router as gcode_router
from backend.routers.jog import router as jog_router
from backend.routers.print_control import router as print_router
from backend.routers.serial import router as serial_router
from backend.routers.upload import router as upload_router
from backend.routers.ws import router as ws_router
from backend.serial_manager import SerialManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.config = load_config()
    app.state.event_bus = EventBus()
    app.state.serial_manager = SerialManager(
        on_disconnect=lambda: app.state.event_bus.publish({"type": "disconnected"}),
        on_serial_log=lambda entry: app.state.event_bus.publish(entry),
    )
    app.state.queue_worker = QueueWorker(
        serial_manager=app.state.serial_manager,
        on_event=app.state.event_bus.publish,
    )
    app.state.db = init_db()
    app.state.processed_gcode = None
    app.state.current_filename = None
    app.state.current_syringe_mode = "left"
    yield
    if app.state.serial_manager.is_connected:
        await app.state.serial_manager.disconnect()
    app.state.db.close()


app = FastAPI(title="Octaris Bioprinter", version="0.1.0", lifespan=lifespan)

app.include_router(serial_router)
app.include_router(upload_router)
app.include_router(print_router)
app.include_router(extrusion_router)
app.include_router(jog_router)
app.include_router(gcode_router)
app.include_router(ws_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "file://",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def health():
    return {"status": "ok"}
