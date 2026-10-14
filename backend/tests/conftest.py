from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.config import Config, load_config
from backend.database import init_db
from backend.main import app, wire


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch) -> Path:
    """Keep log files and per-print serial logs out of the real app data folder."""
    path = tmp_path / "data"
    monkeypatch.setenv("OCTARIS_DATA_DIR", str(path))
    return path


def make_config() -> Config:
    """The repo's config.json, with the machine-specific values (bed limits,
    measured nozzle offset) at their defaults, so the tests don't depend on
    the printer this checkout is set up for."""
    config = load_config()
    defaults = Config()
    config.bed = defaults.bed
    config.nozzle_offset_measured = defaults.nozzle_offset_measured
    return config


@pytest.fixture
async def client():
    wire(app, make_config(), init_db(Path(":memory:")))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    # Stops the serial reader thread of any port a test attached.
    await app.state.serial_manager.disconnect()
    await app.state.temperature.close()
    app.state.history._traffic.stop()
    app.state.db.close()
