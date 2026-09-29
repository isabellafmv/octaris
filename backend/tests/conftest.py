from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.config import load_config
from backend.database import init_db
from backend.events import EventBus
from backend.history import PrintHistory
from backend.main import app
from backend.queue_worker import QueueWorker
from backend.serial_manager import SerialManager


@pytest.fixture
async def client():
    app.state.config = load_config()
    app.state.event_bus = EventBus()
    app.state.serial_manager = SerialManager()
    app.state.db = init_db(Path(":memory:"))
    app.state.history = PrintHistory(app.state.db)
    app.state.queue_worker = QueueWorker(
        serial_manager=app.state.serial_manager,
        on_event=app.state.event_bus.publish,
        on_print_end=app.state.history.end,
        on_print_resumed=app.state.history.reopen,
        retract_on_estop=app.state.config.retract_on_estop,
    )
    app.state.processed_gcode = None
    app.state.print_source = None
    app.state.print_settings = {}
    app.state.current_filename = None
    app.state.current_syringe_mode = "left"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.state.db.close()
