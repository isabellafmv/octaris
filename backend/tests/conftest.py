from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.config import load_config
from backend.database import init_db
from backend.events import EventBus
from backend.history import PrintHistory
from backend.main import app, make_queue_worker, make_serial_manager


@pytest.fixture
async def client():
    app.state.config = load_config()
    app.state.event_bus = EventBus()
    app.state.serial_manager = make_serial_manager(app)
    app.state.db = init_db(Path(":memory:"))
    app.state.history = PrintHistory(app.state.db)
    app.state.queue_worker = make_queue_worker(app)
    app.state.processed_gcode = None
    app.state.print_source = None
    app.state.print_settings = {}
    app.state.current_filename = None
    app.state.current_syringe_mode = "left"
    app.state.is_calibrated = False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.state.db.close()
