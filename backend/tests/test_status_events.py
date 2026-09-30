from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.config import load_config
from backend.database import init_db
from backend.main import app, wire
from tests.serial_fakes import mock_port


def _setup_app_state():
    """Wire up app.state the same way the conftest `client` fixture does,
    without running the real lifespan (which would open a real db file)."""
    wire(app, load_config(), init_db(Path(":memory:")))


def receive_state_event(ws) -> dict:
    """The next event that isn't serial traffic (log lines, temperatures)."""
    while True:
        event = ws.receive_json()
        if event["type"] not in ("serial_log", "temperature"):
            return event


def _fake_connected_serial() -> MagicMock:
    return mock_port()


def test_ws_sends_snapshot_right_after_connect():
    _setup_app_state()
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        data = ws.receive_json()

    assert data == {
        "type": "snapshot",
        "printer_connected": False,
        "port": None,
        "print_status": "idle",
        "lines_sent": 0,
        "lines_total": 0,
        "calibrated": False,
        "flow_rate": 100.0,
        "resumable": False,
        "stop_reason": None,
        "time_estimate_s": None,
    }


def test_status_endpoint_matches_snapshot_shape():
    _setup_app_state()
    client = TestClient(app)

    resp = client.get("/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "printer_connected": False,
        "port": None,
        "print_status": "idle",
        "lines_sent": 0,
        "lines_total": 0,
        "calibrated": False,
        "flow_rate": 100.0,
        "resumable": False,
        "stop_reason": None,
        "time_estimate_s": None,
    }


def test_connect_endpoint_publishes_printer_connected_event():
    _setup_app_state()
    client = TestClient(app)
    fake = _fake_connected_serial()

    with patch("backend.serial_manager.serial.Serial", return_value=fake):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial snapshot

            resp = client.post("/connect", json={"port": "/dev/ttyUSB0"})
            assert resp.status_code == 200

            event = receive_state_event(ws)

    assert event == {"type": "printer", "connected": True, "port": "/dev/ttyUSB0"}


def test_disconnect_endpoint_publishes_printer_disconnected_event():
    _setup_app_state()
    client = TestClient(app)
    fake = _fake_connected_serial()

    with patch("backend.serial_manager.serial.Serial", return_value=fake):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial snapshot

            client.post("/connect", json={"port": "/dev/ttyUSB0"})
            receive_state_event(ws)  # printer connected event
            # Opening the port may reset the board, so calibration is reset
            assert receive_state_event(ws) == {"type": "calibration", "value": "uncalibrated"}

            resp = client.post("/disconnect")
            assert resp.status_code == 200

            event = receive_state_event(ws)

    assert event == {"type": "printer", "connected": False, "port": None}


def test_status_reflects_connected_printer():
    _setup_app_state()
    client = TestClient(app)
    fake = _fake_connected_serial()

    with patch("backend.serial_manager.serial.Serial", return_value=fake):
        client.post("/connect", json={"port": "/dev/ttyUSB0"})
        data = client.get("/status").json()

    assert data["printer_connected"] is True
    assert data["port"] == "/dev/ttyUSB0"
