"""The serial protocol against the virtual printer: line numbers, checksums
and resends, temperature reports, and busy messages during long moves."""

from __future__ import annotations

import asyncio
import math
import time
from unittest.mock import patch

import pytest

from backend import serial_manager as serial_module
from backend.queue_worker import PrintStatus, QueueWorker
from backend.serial_manager import SerialManager, SerialTimeout, number_line, parse_temperatures
from backend.virtual_printer import VirtualPrinter
from tests.serial_fakes import attach


def fast_printer(**options) -> VirtualPrinter:
    """Instant moves unless asked otherwise, and frequent busy messages and
    temperature reports, so the tests run quickly."""
    defaults = {"speed": math.inf, "planner_depth": 4, "busy_interval_s": 0.05, "report_scale": 0.01}
    return VirtualPrinter(**{**defaults, **options})


PRINT = [f"G1 X{i} Y{i % 3} F6000" for i in range(1, 11)]


async def wait_for(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.005)


@pytest.fixture
async def rig():
    """A SerialManager on a virtual printer, collecting its events."""
    events: list[dict] = []
    managers: list[SerialManager] = []

    def make() -> SerialManager:
        manager = SerialManager(on_event=events.append)
        managers.append(manager)
        return manager

    yield make, events
    for manager in managers:
        await manager.disconnect()


async def run_print(manager: SerialManager, lines: list[str]) -> QueueWorker:
    worker = QueueWorker(manager)
    worker.load_gcode(lines)
    worker.start()
    await wait_for(lambda: worker.status in (PrintStatus.COMPLETED, PrintStatus.PAUSED))
    return worker


def sent_log(manager: SerialManager) -> list[str]:
    return [e["content"] for e in manager.log_buffer if e["direction"] == "sent"]


def received_log(manager: SerialManager) -> list[str]:
    return [e["content"] for e in manager.log_buffer if e["direction"] == "received"]


def test_numbered_line_format():
    # Checksum: XOR of every byte of "N1 G28".
    assert number_line(1, "G28") == "N1 G28*18"


def test_parse_temperatures():
    assert parse_temperatures("T:21.30 /0.00 B:20.10 /60.00 @:0 B@:0") == {
        "T": {"actual": 21.3, "target": 0.0},
        "B": {"actual": 20.1, "target": 60.0},
    }
    assert parse_temperatures("ok T:21.3 /0.0") == {"T": {"actual": 21.3, "target": 0.0}}
    # M114 mentions B: and C: too, but isn't a temperature report.
    assert parse_temperatures("X:1.00 Y:2.00 Z:0.00 A:0.00 B:-1.00 C:0.00 Count X:0") is None
    assert parse_temperatures("ok") is None


# --- line numbers and resends -------------------------------------------------


async def test_print_lines_are_numbered_after_m110(rig):
    make, _ = rig
    printer = fast_printer()
    manager = make()
    attach(manager, printer)

    worker = await run_print(manager, PRINT[:3])

    assert worker.status == PrintStatus.COMPLETED
    assert printer.received == [
        "M114",
        "M84 S0",
        "M110 N0",
        number_line(1, PRINT[0]),
        number_line(2, PRINT[1]),
        number_line(3, PRINT[2]),
        "M400",  # the planner drained before COMPLETED
    ]
    # Manual commands stay unnumbered.
    await manager.send("M114")
    assert printer.received[-1] == "M114"


async def test_corrupted_line_is_resent(rig):
    make, _ = rig
    printer = fast_printer()
    manager = make()
    attach(manager, printer)
    printer.corrupt_next("G1 X4 ")

    worker = await run_print(manager, PRINT)

    assert worker.status == PrintStatus.COMPLETED
    assert worker.lines_sent == len(PRINT)
    # Every print line ran exactly once, in order, the corrupted one included.
    assert printer.executed[3:] == [*PRINT, "M400"]
    # N4 arrived twice: garbled, then the resend from history.
    n4 = [raw for raw in printer.received if raw.startswith("N4 ")]
    assert len(n4) == 2 and n4[1] == number_line(4, PRINT[3]) != n4[0]
    assert "Error:checksum mismatch, Last Line: 3" in received_log(manager)
    assert "Resend: 4" in received_log(manager)
    assert sent_log(manager).count(PRINT[3]) == 2  # the send and the resend


async def test_lost_line_times_out_then_resyncs_line_numbers(rig, monkeypatch):
    monkeypatch.setattr(serial_module, "REPLY_DEADLINE_S", 0.2)
    make, _ = rig
    printer = fast_printer()
    manager = make()
    attach(manager, printer)
    printer.drop_next("G1 X4 ")  # never reaches the printer: no reply at all

    worker = await run_print(manager, PRINT)
    assert worker.status == PrintStatus.PAUSED
    assert worker.lines_sent == 3

    worker.resume()
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    # The retry re-uses N4 after telling the printer where it stands, so the
    # printer's line count and ours agree whether or not it saw the first try.
    assert "M110 N3" in printer.received
    assert printer.received[printer.received.index("M110 N3") + 1] == number_line(4, PRINT[3])
    assert [c for c in printer.executed if c.startswith("G1")] == PRINT


# --- temperatures ----------------------------------------------------------------


async def test_unsolicited_temperature_reports_while_printing(rig):
    make, events = rig
    # Reports every 20 ms, while each move takes ~20 ms and the planner holds 2.
    printer = fast_printer(speed=5, planner_depth=2)
    manager = make()
    with patch("backend.serial_manager.serial.Serial", return_value=printer):
        await manager.connect("/dev/virtual", 115200)
    await wait_for(lambda: any(e["type"] == "temperature" for e in events))

    moves = [f"G1 X{10 * (i % 2)} Y{i} F6000" for i in range(30)]
    before = len(events)
    worker = await run_print(manager, moves)

    assert worker.status == PrintStatus.COMPLETED
    assert worker._tracker.error is None  # the seed M114 parsed despite the reports
    assert [c for c in printer.executed if c.startswith("G1")] == moves
    reports = [e for e in events[before:] if e["type"] == "temperature"]
    assert len(reports) >= 5  # arrived while the print was running
    assert reports[-1]["temperatures"] == {
        "T": {"actual": 21.3, "target": 0.0},
        "B": {"actual": 20.1, "target": 0.0},
    }
    # Reports never end up in a reply or the serial log.
    reply = await manager.send("M114")
    assert reply.splitlines()[1:] == ["ok"] and reply.startswith("X:")
    assert not any(line.startswith("T:") for line in received_log(manager))
    assert printer.executed[0] == "M155 S2"


async def test_polls_m105_without_auto_report(rig, monkeypatch):
    monkeypatch.setattr(serial_module, "AUTOREPORT_WAIT_S", 0.1)
    monkeypatch.setattr(serial_module, "TEMPERATURE_POLL_S", 0.05)
    make, events = rig
    printer = fast_printer(autoreport=False)
    manager = make()
    with patch("backend.serial_manager.serial.Serial", return_value=printer):
        await manager.connect("/dev/virtual", 115200)

    await wait_for(lambda: len([e for e in events if e["type"] == "temperature"]) >= 2)

    assert printer.executed.count("M105") >= 2
    assert "M105" not in sent_log(manager)  # polls stay out of the log
    # A print still runs with polls going out between its lines.
    worker = await run_print(manager, PRINT)
    assert worker.status == PrintStatus.COMPLETED
    assert [c for c in printer.executed if c.startswith("G1")] == PRINT


async def test_manual_m105_returns_the_temperatures(rig):
    make, events = rig
    printer = fast_printer(autoreport=False)
    manager = make()
    attach(manager, printer)

    reply = await manager.send("M105")

    assert reply == "ok " + printer.temperature_report()
    await wait_for(lambda: any(e["type"] == "temperature" for e in events))


# --- busy -----------------------------------------------------------------------


async def test_busy_keeps_a_long_move_alive(rig, monkeypatch):
    monkeypatch.setattr(serial_module, "REPLY_DEADLINE_S", 0.3)
    make, _ = rig
    # 100 mm at 10 mm/s is 10 s, scaled to 1 s. With a one-move planner the
    # next move waits for it, sending "busy" every 50 ms.
    printer = fast_printer(speed=10, planner_depth=1)
    manager = make()
    attach(manager, printer)

    await manager.send("G1 X100 F600")
    started = time.monotonic()
    assert await manager.send("G1 X0 F600") == "ok"

    assert time.monotonic() - started > 0.8  # well past the 0.3 s deadline
    assert received_log(manager).count("echo:busy: processing") >= 10


async def test_long_move_without_busy_times_out(rig, monkeypatch):
    monkeypatch.setattr(serial_module, "REPLY_DEADLINE_S", 0.3)
    make, _ = rig
    printer = fast_printer(speed=10, planner_depth=1, busy_interval_s=None)
    manager = make()
    attach(manager, printer)

    await manager.send("G1 X100 F600")
    with pytest.raises(SerialTimeout):
        await manager.send("G1 X0 F600")


async def test_print_of_long_moves_does_not_pause(rig, monkeypatch):
    monkeypatch.setattr(serial_module, "REPLY_DEADLINE_S", 0.3)
    make, _ = rig
    printer = fast_printer(speed=20, planner_depth=1)
    manager = make()
    attach(manager, printer)
    moves = ["G1 X100 F600", "G1 X0 F600", "G1 X100 F600"]  # 0.5 s each

    worker = await run_print(manager, moves)

    assert worker.status == PrintStatus.COMPLETED
    assert "echo:busy: processing" in received_log(manager)


# --- other lines ------------------------------------------------------------------


async def test_host_actions_are_logged_not_taken_as_replies(rig):
    make, _ = rig
    printer = fast_printer(speed=10, planner_depth=1)
    manager = make()
    attach(manager, printer)

    await manager.send("G1 X100 F600")
    move = asyncio.create_task(manager.send("G1 X0 F600"))  # blocks ~1 s
    await asyncio.sleep(0.1)
    printer.inject("//action:notification Refill syringe")

    assert await move == "ok"
    assert "//action:notification Refill syringe" in received_log(manager)


# --- M410 -------------------------------------------------------------------------


async def test_m410_ack_is_not_taken_as_the_next_reply(rig):
    # Marlin stops on M410 the moment it arrives, but also queues the line
    # and acks it in turn: after the "ok" of the move in flight. With a slow
    # link, the host's next command is already out when that "ok" comes.
    make, _ = rig
    printer = fast_printer(speed=10, planner_depth=1, ok_delay_s=0.05)
    manager = make()
    attach(manager, printer)

    await manager.send("G1 X100 F600")  # 1 s at speed 10
    move = asyncio.create_task(manager.send("G1 X0 F600"))  # blocked: planner full
    await asyncio.sleep(0.2)
    await manager.emergency_write("M410")
    assert await move == "ok"

    m400, m114 = await manager.send_lines(["M400", "M114"])

    assert m400 == "ok"
    assert m114.startswith("X:") and m114.endswith("ok")
    stopped = float(m114.split()[0][2:])
    assert 0 < stopped < 100  # mid-move, not at either end
    assert printer.executed[-3:] == ["M410", "M400", "M114"]
    # And the replies stay paired from then on.
    assert (await manager.send("M105")).startswith("ok T:")
