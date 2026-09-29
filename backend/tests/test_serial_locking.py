import asyncio
import time
from unittest.mock import patch

from backend.serial_manager import SerialManager


class FakeSerial:
    """Mimics serial.Serial, recording write/read order with artificial
    delays so concurrent callers would interleave if not properly locked."""

    def __init__(self, delay: float = 0.02):
        self.is_open = True
        self.delay = delay
        self.log: list[tuple[str, str]] = []

    def write(self, data: bytes) -> None:
        content = data.decode().strip()
        self.log.append(("write", content))
        time.sleep(self.delay)

    def flush(self) -> None:
        pass

    def readline(self) -> bytes:
        time.sleep(self.delay)
        self.log.append(("read", "ok"))
        return b"ok\n"

    def close(self) -> None:
        self.is_open = False


def _make_connected_manager(fake_serial: FakeSerial) -> SerialManager:
    manager = SerialManager()
    manager._serial = fake_serial
    manager._port = "/dev/fake"
    manager._baud_rate = 115200
    return manager


async def test_concurrent_send_line_calls_do_not_interleave():
    fake = FakeSerial()
    manager = _make_connected_manager(fake)

    await asyncio.gather(
        manager.send_line("G1 X10"),
        manager.send_line("G1 Y10"),
    )

    assert len(fake.log) == 4
    # Each call must complete its write/read pair before the other starts.
    assert [entry[0] for entry in fake.log] == ["write", "read", "write", "read"]
    first_line = fake.log[0][1]
    second_line = fake.log[2][1]
    assert {first_line, second_line} == {"G1 X10", "G1 Y10"}


async def test_send_lines_is_atomic_against_concurrent_send_line():
    fake = FakeSerial()
    manager = _make_connected_manager(fake)

    await asyncio.gather(
        manager.send_lines(["G91", "G1 X5", "G90"]),
        manager.send_line("M114"),
    )

    assert len(fake.log) == 8
    assert [entry[0] for entry in fake.log] == [
        "write", "read", "write", "read", "write", "read", "write", "read",
    ]

    lines_in_order = [entry[1] for entry in fake.log if entry[0] == "write"]
    batch = ["G91", "G1 X5", "G90"]
    solo = ["M114"]

    # The three batched lines must appear contiguously and in order, either
    # entirely before or entirely after the solo call — never split by it.
    if lines_in_order[:3] == batch:
        assert lines_in_order[3:] == solo
    else:
        assert lines_in_order[:1] == solo
        assert lines_in_order[1:] == batch


async def test_send_line_reconnect_does_not_deadlock():
    fake = FakeSerial(delay=0.0)
    manager = SerialManager()
    manager._port = "/dev/fake"
    manager._baud_rate = 115200
    manager._serial = None  # force the reconnect path

    with patch("backend.serial_manager.serial.Serial", return_value=fake):
        response = await asyncio.wait_for(manager.send_line("G28"), timeout=2)

    assert response == "ok"
    assert manager.is_connected
    assert fake.log == [("write", "G28"), ("read", "ok")]


async def test_on_connect_fires_after_automatic_reconnect():
    fake = FakeSerial(delay=0.0)
    connected_ports: list[str] = []
    manager = SerialManager(on_connect=connected_ports.append)
    manager._port = "/dev/fake"
    manager._baud_rate = 115200
    manager._serial = None  # force the reconnect path

    with patch("backend.serial_manager.serial.Serial", return_value=fake):
        await manager.send_line("G28")

    assert connected_ports == ["/dev/fake"]


async def test_on_connect_not_called_for_manual_connect():
    """Manual connect() is reported by the caller (the /connect router), not
    from within SerialManager — on_connect is only for automatic reconnects."""
    fake = FakeSerial(delay=0.0)
    connected_ports: list[str] = []
    manager = SerialManager(on_connect=connected_ports.append)

    with patch("backend.serial_manager.serial.Serial", return_value=fake):
        await manager.connect("/dev/fake", 115200)

    assert connected_ports == []
