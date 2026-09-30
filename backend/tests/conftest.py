from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.config import load_config
from backend.database import init_db
from backend.main import app, wire


@pytest.fixture
async def client():
    wire(app, load_config(), init_db(Path(":memory:")))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    # Stops the serial reader thread of any port a test attached.
    await app.state.serial_manager.disconnect()
    app.state.db.close()
