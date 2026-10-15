"""Stopping the server (Ctrl+C, a --reload, POST /shutdown) is prompt, with
the virtual printer connected and a WebSocket client attached.

These run the real uvicorn server, lifespan included: uvicorn only runs the
lifespan cleanup once every request and WebSocket handler has returned.
"""

import asyncio
from pathlib import Path

import httpx
import pytest
import uvicorn
from websockets.asyncio.client import connect as ws_connect

from backend import main, virtual_printer
from backend.database import init_db
from tests.conftest import make_config

# Well under the graceful-shutdown timeout the servers below get, so a
# handler that doesn't return by itself fails the test instead of being
# cancelled by uvicorn.
SHUTDOWN_LIMIT_S = 5.0
GRACEFUL_TIMEOUT_S = 30.0
# Slow, so a print is still running when the server stops
PRINTER_OPTIONS = {"planner_depth": 2, "speed": 1, "busy_interval_s": 0.05, "ok_delay_s": 0}
GCODE = "G91\n" + "G1 X1 F60\nG1 X-1 F60\n" * 50


@pytest.fixture
def dev_mode(monkeypatch, tmp_path):
    """The lifespan as in dev mode, with the tests' machine settings, an
    in-memory database and no log files."""
    monkeypatch.setenv("OCTARIS_VIRTUAL_PRINTER", "1")
    monkeypatch.delenv("OCTARIS_TOKEN", raising=False)
    monkeypatch.setattr(virtual_printer, "DEV_OPTIONS", PRINTER_OPTIONS)
    monkeypatch.setattr(main, "load_config", make_config)
    monkeypatch.setattr(main, "init_db", lambda: init_db(Path(":memory:")))
    monkeypatch.setattr(main, "setup_logging", lambda: tmp_path / "octaris-backend.log")


async def start_server(timeout_graceful_shutdown: float | None) -> tuple[uvicorn.Server, asyncio.Task, str]:
    config = uvicorn.Config(
        main.app,
        host="127.0.0.1",
        port=0,
        lifespan="on",
        log_level="warning",
        timeout_graceful_shutdown=timeout_graceful_shutdown,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        assert not task.done(), "the server didn't start"
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, task, f"127.0.0.1:{port}"


def websocket(host: str):
    # Reads every event as it comes, as the app does (by default the client
    # stops reading once 16 are unread, and then misses the server's close)
    return ws_connect(f"ws://{host}/ws", max_queue=None)


async def connected(http: httpx.AsyncClient) -> None:
    resp = await http.post("/connect", json={"port": "virtual"})
    assert resp.status_code == 200, resp.text


async def start_print(http: httpx.AsyncClient) -> None:
    assert (await http.post("/calibration/zero")).status_code == 200
    resp = await http.post(
        "/upload/gcode",
        params={"syringe_mode": "left", "needs_changes": "false"},
        files={"file": ("moves.gcode", GCODE.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    resp = await http.post("/print/start")
    assert resp.status_code == 200, resp.text
    assert main.app.state.queue_worker.print_active


@pytest.mark.parametrize("printing", [False, True], ids=["idle", "mid-print"])
async def test_stop_with_a_websocket_attached(dev_mode, printing):
    server, task, host = await start_server(GRACEFUL_TIMEOUT_S)
    try:
        async with httpx.AsyncClient(base_url=f"http://{host}") as http:
            await connected(http)
            async with websocket(host) as ws:
                await ws.recv()  # the snapshot
                if printing:
                    await start_print(http)
                    await ws.recv()

                server.should_exit = True  # what Ctrl+C and a reload do
                await asyncio.wait_for(task, SHUTDOWN_LIMIT_S)

        state = main.app.state
        assert not state.serial_manager.is_connected
        assert not state.queue_worker.print_active
        assert not state.queue_worker.sending
    finally:
        server.should_exit = True
        if not task.done():
            task.cancel()


async def test_shutdown_request_mid_print(dev_mode, monkeypatch):
    server, task, host = await start_server(GRACEFUL_TIMEOUT_S)
    monkeypatch.setattr(main.app.state, "uvicorn_server", server, raising=False)
    try:
        async with httpx.AsyncClient(base_url=f"http://{host}") as http:
            await connected(http)
            async with websocket(host) as ws:
                await ws.recv()
                await start_print(http)

                resp = await http.post("/shutdown")
                assert resp.status_code == 200
                await asyncio.wait_for(task, SHUTDOWN_LIMIT_S)

        assert not main.app.state.queue_worker.print_active
    finally:
        server.should_exit = True
        if not task.done():
            task.cancel()


async def test_a_slow_request_does_not_hold_up_the_stop(dev_mode):
    """Started by the uvicorn CLI (no graceful-shutdown timeout), a request
    still waiting for the printer is cut short."""
    server, task, host = await start_server(None)
    try:
        async with httpx.AsyncClient(base_url=f"http://{host}", timeout=60) as http:
            await connected(http)
            dwell = asyncio.create_task(http.post("/gcode/send", json={"line": "G4 S60"}))
            while not any("G4" in line for line in main.app.state.serial_manager._serial.received):
                await asyncio.sleep(0.01)

            server.should_exit = True
            await asyncio.wait_for(task, main.GRACEFUL_SHUTDOWN_S + SHUTDOWN_LIMIT_S)
            assert server.config.timeout_graceful_shutdown == main.GRACEFUL_SHUTDOWN_S
            dwell.cancel()
            await asyncio.gather(dwell, return_exceptions=True)
        assert not main.app.state.serial_manager.is_connected
    finally:
        server.should_exit = True
        if not task.done():
            task.cancel()
