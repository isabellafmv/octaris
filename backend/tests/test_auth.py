"""Per-launch token auth: 401 without it, 200 with it, WS rejection, dev-mode bypass."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.config import load_config
from backend.database import init_db
from backend.events import EventBus
from backend.history import PrintHistory
from backend.main import app
from backend.queue_worker import QueueWorker
from backend.serial_manager import SerialManager

TOKEN = "s3cr3t-test-token"


def _setup_app_state():
    app.state.config = load_config()
    app.state.event_bus = EventBus()
    app.state.serial_manager = SerialManager()
    app.state.db = init_db(Path(":memory:"))
    app.state.history = PrintHistory(app.state.db)
    app.state.queue_worker = QueueWorker(
        serial_manager=app.state.serial_manager,
        on_event=app.state.event_bus.publish,
        on_print_end=app.state.history.end,
    )
    app.state.processed_gcode = None
    app.state.print_source = None
    app.state.print_settings = {}
    app.state.current_filename = None
    app.state.current_syringe_mode = "left"
    app.state.is_calibrated = False


@pytest.fixture
def token_env(monkeypatch):
    monkeypatch.setenv("OCTARIS_TOKEN", TOKEN)
    return TOKEN


async def test_request_without_token_is_401(client, token_env):
    resp = await client.get("/gcode/log")
    assert resp.status_code == 401


async def test_request_with_wrong_token_is_401(client, token_env):
    resp = await client.get("/gcode/log", headers={"X-Octaris-Token": "wrong"})
    assert resp.status_code == 401


async def test_request_with_correct_token_is_200(client, token_env):
    resp = await client.get("/gcode/log", headers={"X-Octaris-Token": TOKEN})
    assert resp.status_code == 200


async def test_health_endpoint_exempt_without_token(client, token_env):
    resp = await client.get("/")
    assert resp.status_code == 200


async def test_no_checks_when_token_unset(client, monkeypatch):
    monkeypatch.delenv("OCTARIS_TOKEN", raising=False)
    resp = await client.get("/gcode/log")
    assert resp.status_code == 200


def test_ws_rejected_without_token(monkeypatch):
    monkeypatch.setenv("OCTARIS_TOKEN", TOKEN)
    _setup_app_state()
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws"):
            pass

    assert exc_info.value.code == 1008


def test_ws_accepted_with_correct_token(monkeypatch):
    monkeypatch.setenv("OCTARIS_TOKEN", TOKEN)
    _setup_app_state()
    client = TestClient(app)

    with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        data = ws.receive_json()

    assert data["type"] == "snapshot"


def test_ws_accepted_without_token_when_unset(monkeypatch):
    monkeypatch.delenv("OCTARIS_TOKEN", raising=False)
    _setup_app_state()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        data = ws.receive_json()

    assert data["type"] == "snapshot"
