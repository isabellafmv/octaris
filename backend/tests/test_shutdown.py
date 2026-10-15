"""POST /shutdown: authenticated, closes the port, stops CuraEngine, exits."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend import slicer
from backend.main import app
from tests.serial_fakes import mock_port


@pytest.fixture
def server(monkeypatch):
    """Stands in for the uvicorn server /shutdown stops."""
    fake = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(app.state, "uvicorn_server", fake, raising=False)
    return fake


async def test_shutdown_needs_the_token(client, server, monkeypatch):
    monkeypatch.setenv("OCTARIS_TOKEN", "t")
    resp = await client.post("/shutdown")
    assert resp.status_code == 401
    assert not server.should_exit


async def test_shutdown_closes_the_port_and_exits(client, server):
    with patch("backend.serial_manager.serial.Serial", return_value=mock_port()):
        await app.state.serial_manager.connect("/dev/ttyUSB0", 115200)
    assert app.state.serial_manager.is_connected

    resp = await client.post("/shutdown")
    assert resp.status_code == 200
    assert not app.state.serial_manager.is_connected
    assert server.should_exit


async def test_shutdown_stops_curaengine(client, server):
    # A stand-in for a long slice
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)")
    slicer._running.add(proc)
    try:
        resp = await client.post("/shutdown")
        assert resp.status_code == 200
        assert proc.returncode is not None
    finally:
        slicer._running.discard(proc)
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


def test_shutdown_is_not_in_the_client_schema():
    assert "/shutdown" not in app.openapi()["paths"]
