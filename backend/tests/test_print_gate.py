"""409 gating: jog, raw G-code, and calibration/zero must respect print status."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.main import app
from backend.queue_worker import PrintStatus


def set_status(status: PrintStatus) -> None:
    app.state.queue_worker._status = status


def connect_fake_serial() -> None:
    app.state.serial_manager._serial = SimpleNamespace(is_open=True)
    app.state.serial_manager.send = AsyncMock(return_value="ok")
    app.state.serial_manager.send_lines = AsyncMock(return_value=["ok", "ok", "ok"])


async def test_jog_blocked_while_printing(client):
    connect_fake_serial()
    set_status(PrintStatus.PRINTING)

    resp = await client.post("/jog", json={"axis": "X", "distance": 1})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Pause the print first"


async def test_jog_allowed_while_paused(client):
    connect_fake_serial()
    set_status(PrintStatus.PAUSED)

    resp = await client.post("/jog", json={"axis": "X", "distance": 1})

    assert resp.status_code == 200


async def test_gcode_send_blocked_while_printing(client):
    connect_fake_serial()
    set_status(PrintStatus.PRINTING)

    resp = await client.post("/gcode/send", json={"line": "G28"})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Pause the print first"


async def test_gcode_send_allowed_while_paused(client):
    connect_fake_serial()
    set_status(PrintStatus.PAUSED)

    resp = await client.post("/gcode/send", json={"line": "G28"})

    assert resp.status_code == 200


async def test_gcode_send_rejects_g92_while_paused(client):
    connect_fake_serial()
    set_status(PrintStatus.PAUSED)

    resp = await client.post("/gcode/send", json={"line": "G92 X0 Y0"})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Can't re-zero during a print"


async def test_gcode_send_allows_g92_when_idle(client):
    connect_fake_serial()
    set_status(PrintStatus.IDLE)

    resp = await client.post("/gcode/send", json={"line": "G92 X0 Y0"})

    assert resp.status_code == 200


async def test_calibration_zero_blocked_while_printing(client):
    connect_fake_serial()
    set_status(PrintStatus.PRINTING)

    resp = await client.post("/calibration/zero")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Pause the print first"


async def test_calibration_zero_blocked_while_paused(client):
    connect_fake_serial()
    set_status(PrintStatus.PAUSED)

    resp = await client.post("/calibration/zero")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Can't re-zero during a print"


async def test_calibration_zero_allowed_when_idle(client):
    connect_fake_serial()
    set_status(PrintStatus.IDLE)

    resp = await client.post("/calibration/zero")

    assert resp.status_code == 200
