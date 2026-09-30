import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend import serial_manager as serial_module
from backend.queue_worker import PrintStatus, QueueWorker
from backend.serial_manager import SerialManager, SerialTimeout
from tests.serial_fakes import attach, unframe


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class ScriptedSerial:
    """Mimics serial.Serial with a scripted reply per sent command.

    Each script item is a line to return from readline(), or None for a
    readline() that times out (advancing the fake clock by SERIAL_TIMEOUT_S).
    Once a command's script runs out before its "ok"/"error", every further
    read times out, like a printer that never answers. While no command is
    waiting, reads return nothing without moving the clock (the reader
    thread keeps reading when idle).
    """

    def __init__(self, clock: FakeClock, replies: dict[str, list[str | None]] | None = None,
                 default: list[str | None] | None = None):
        self.is_open = True
        self.clock = clock
        self.replies = replies or {}
        self.default = default if default is not None else ["ok"]
        self.written: list[str] = []
        self._pending: list[str | None] = []
        self._awaiting = False

    def write(self, data: bytes) -> None:
        _, line = unframe(data.decode().strip())
        self.written.append(line)
        self._pending = list(self.replies.get(line, self.default))
        self._awaiting = True

    def flush(self) -> None:
        pass

    def readline(self) -> bytes:
        if not self._pending and not self._awaiting:
            time.sleep(0.001)
            return b""
        item = self._pending.pop(0) if self._pending else None
        if item is None:
            if not self._pending:
                time.sleep(0.001)  # hung: don't spin
            self.clock.now += serial_module.SERIAL_TIMEOUT_S
            return b""
        if item.lower().startswith(("ok", "error")):
            self._awaiting = False
        return (item + "\n").encode()

    def close(self) -> None:
        self.is_open = False


@pytest.fixture
def clock():
    fake = FakeClock()
    with patch.object(serial_module, "time", SimpleNamespace(monotonic=fake.monotonic)):
        yield fake


def make_manager(fake: ScriptedSerial) -> SerialManager:
    manager = SerialManager()
    attach(manager, fake)
    return manager


def silent_for(seconds: int) -> list[None]:
    return [None] * seconds


async def test_slow_ok_with_busy_lines_succeeds(clock):
    # Nothing for 4 s, busy, nothing for 4 s, busy, nothing for 4 s, then ok:
    # 12 s in total, well past the old 5 s readline timeout.
    fake = ScriptedSerial(clock, {"M400": [
        *silent_for(4), "echo:busy: processing",
        *silent_for(4), "echo:busy: processing",
        *silent_for(4), "ok",
    ]})
    manager = make_manager(fake)

    response = await manager.send("M400")

    assert response == "ok"
    assert clock.now == 12


async def test_busy_lines_extend_deadline_and_other_lines_are_kept(clock):
    # 50 s silent + busy + 50 s silent + ok = 100 s, over the 60 s deadline,
    # but the busy line restarts it.
    fake = ScriptedSerial(clock, {"G1 X10 F300": [
        *silent_for(50), "echo:busy: processing",
        *silent_for(50), "X:10.00 Y:0.00", "ok",
    ]})
    manager = make_manager(fake)

    response = await manager.send("G1 X10 F300")

    assert response == "X:10.00 Y:0.00\nok"


async def test_no_reply_raises_serial_timeout(clock):
    fake = ScriptedSerial(clock, {"G1 X10": []})
    manager = make_manager(fake)

    with pytest.raises(SerialTimeout, match="G1 X10") as info:
        await manager.send("G1 X10")

    assert info.value.timeout_s == serial_module.REPLY_DEADLINE_S
    # The reader may read (and tick the fake clock) once more before the
    # test looks; what matters is that it didn't give up early.
    assert clock.now >= serial_module.REPLY_DEADLINE_S


@pytest.mark.parametrize("command", ["G28", "G4 S90", "M400", "M109 S200", "M190 S60"])
async def test_slow_commands_get_long_deadline(clock, command):
    fake = ScriptedSerial(clock, {command: [*silent_for(200), "ok"]})
    manager = make_manager(fake)

    assert await manager.send(command) == "ok"


async def test_late_reply_is_not_taken_for_the_next_command(clock):
    fake = ScriptedSerial(clock, {"M114": []})
    manager = make_manager(fake)

    with pytest.raises(SerialTimeout):
        await manager.send("M114")

    # The first M114's reply turns up after it timed out, before the next
    # command: the reader drops it as unsolicited.
    fake._pending = ["X:1.00 Y:1.00", "ok"]
    await asyncio.sleep(0.05)
    fake.replies = {"M114": ["X:2.00 Y:2.00", "ok"]}

    assert await manager.send("M114") == "X:2.00 Y:2.00\nok"


async def test_error_reply_is_returned(clock):
    fake = ScriptedSerial(clock, {"G1 X999": [
        "echo:busy: processing", "error:Move out of range", "ok",
    ]})
    manager = make_manager(fake)

    response = await manager.send("G1 X999")

    assert response == "error:Move out of range"


async def test_print_pauses_on_timeout_and_retries_line_on_resume(clock):
    fake = ScriptedSerial(clock, {"G1 X2": []})
    manager = make_manager(fake)
    events: list[dict] = []
    worker = QueueWorker(manager, on_event=events.append)

    worker.load_gcode(["G1 X1", "G1 X2", "G1 X3"])
    worker.start()
    await asyncio.sleep(0.2)

    assert worker.status == PrintStatus.PAUSED
    assert worker.lines_sent == 1
    assert fake.written == ["M114", "M84 S0", "M110 N0", "G1 X1", "G1 X2"]
    assert {"type": "status", "value": "paused"} in events
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "G1 X2" in errors[0]["message"]
    assert not any(e["type"] == "disconnected" for e in events)

    # The printer recovers; resuming re-sends the line that timed out, under
    # its old line number after an M110 to agree on it.
    fake.replies = {}
    worker.resume()
    await asyncio.sleep(0.2)

    assert worker.status == PrintStatus.COMPLETED
    assert worker.lines_sent == 3
    assert fake.written == [
        "M114", "M84 S0", "M110 N0", "G1 X1", "G1 X2", "M110 N1", "G1 X2", "G1 X3",
    ]


async def test_gcode_endpoint_returns_504_on_timeout(client, clock):
    from backend.main import app

    attach(app.state.serial_manager, ScriptedSerial(clock, {"G1 X10": []}))

    resp = await client.post("/gcode/send", json={"line": "G1 X10"})

    assert resp.status_code == 504
    assert "G1 X10" in resp.json()["detail"]


async def test_jog_endpoint_returns_504_on_timeout(client, clock):
    from backend.main import app

    attach(app.state.serial_manager, ScriptedSerial(clock, default=[]))

    resp = await client.post("/jog", json={"axis": "X", "distance": 1})

    assert resp.status_code == 504
