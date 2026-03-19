from unittest.mock import MagicMock, patch

import pytest

from backend.serial_manager import SerialError, SerialManager


def test_list_ports_returns_list():
    with patch("backend.serial_manager.serial.tools.list_ports.comports") as mock_comports:
        mock_port = MagicMock()
        mock_port.device = "/dev/tty.usbmodem1234"
        mock_port.description = "USB Modem"
        mock_comports.return_value = [mock_port]

        ports = SerialManager.list_ports()
        assert len(ports) == 1
        assert ports[0]["device"] == "/dev/tty.usbmodem1234"


async def test_connect_failure_raises():
    manager = SerialManager()
    with patch("backend.serial_manager.serial.Serial", side_effect=Exception("No device")):
        with pytest.raises(SerialError):
            await manager.connect("/dev/nonexistent", 250000)


async def test_connect_disconnect():
    mock_serial = MagicMock()
    mock_serial.is_open = True

    manager = SerialManager()
    with patch("backend.serial_manager.serial.Serial", return_value=mock_serial):
        await manager.connect("/dev/ttyUSB0", 250000)
        assert manager.is_connected
        assert manager.port == "/dev/ttyUSB0"

    await manager.disconnect()
    assert not manager.is_connected


async def test_send_line_when_disconnected():
    manager = SerialManager()
    with pytest.raises(SerialError, match="Not connected"):
        await manager.send_line("G28")


async def test_ports_endpoint(client):
    with patch("backend.serial_manager.serial.tools.list_ports.comports") as mock_comports:
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB0"
        mock_port.description = "USB Serial"
        mock_comports.return_value = [mock_port]

        resp = await client.get("/ports")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["ports"]) == 1


async def test_connect_endpoint_invalid_port(client):
    resp = await client.post("/connect", json={"port": "/dev/nonexistent"})
    assert resp.status_code == 400


async def test_disconnect_endpoint(client):
    resp = await client.post("/disconnect")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disconnected"
