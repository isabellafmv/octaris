import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.config import PROJECT_ROOT, Config
from backend.database import init_db
from backend.main import app, wire


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch) -> Path:
    """Keep log files and per-print serial logs out of the real app data folder."""
    path = tmp_path / "data"
    monkeypatch.setenv("OCTARIS_DATA_DIR", str(path))
    return path


def make_config() -> Config:
    """The repo's config.json, with the built-in printer profiles as they
    ship (bed limits, nozzle offsets, unmeasured offset), so the tests don't
    depend on the printer this checkout is set up for."""
    data = json.loads((PROJECT_ROOT / "config.json").read_text())
    for key in ("printer_profile", "profiles", "bed", "nozzle_offset_measured"):
        data.pop(key, None)
    return Config(**data)


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
