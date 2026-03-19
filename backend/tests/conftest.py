import pytest
from httpx import ASGITransport, AsyncClient

from backend.config import load_config
from backend.events import EventBus
from backend.main import app
from backend.queue_worker import QueueWorker
from backend.serial_manager import SerialManager


@pytest.fixture
async def client():
    app.state.config = load_config()
    app.state.event_bus = EventBus()
    app.state.serial_manager = SerialManager()
    app.state.queue_worker = QueueWorker(
        serial_manager=app.state.serial_manager,
        on_event=app.state.event_bus.publish,
    )
    app.state.processed_gcode = None
    app.state.current_filename = None
    app.state.current_syringe_mode = "left"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
