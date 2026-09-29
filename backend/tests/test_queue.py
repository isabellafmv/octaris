import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.queue_worker import PrintStatus, QueueWorker
from backend.serial_manager import SerialManager


def make_worker(events: list | None = None, send_delay: float = 0.0):
    serial = MagicMock()

    async def slow_send(line):
        if send_delay:
            await asyncio.sleep(send_delay)
        return "ok"

    serial.send_line = AsyncMock(side_effect=slow_send)
    serial.emergency_write = AsyncMock()
    serial.is_connected = True

    captured = events if events is not None else []

    def on_event(evt):
        captured.append(evt)

    worker = QueueWorker(serial, on_event=on_event)
    return worker, serial, captured


async def test_load_and_print():
    worker, serial, events = make_worker()
    gcode = ["G1 X10 F200", "G1 Y5 F200", "; comment", "G1 Z1 F100"]
    worker.load_gcode(gcode)
    assert worker.lines_total == 3  # comment stripped

    worker.start()
    await asyncio.sleep(0.3)

    assert worker.status == PrintStatus.COMPLETED
    assert worker.lines_sent == 3
    assert sent_lines(serial) == ["M114", "M84 S0", "G1 X10 F200", "G1 Y5 F200", "G1 Z1 F100"]

    progress_events = [e for e in events if e["type"] == "progress"]
    assert len(progress_events) == 3
    assert progress_events[-1]["lines_sent"] == 3


async def test_priority_bypass():
    events = []
    worker, serial, events = make_worker(events, send_delay=0.01)

    gcode = [f"G1 X{i}" for i in range(50)]
    worker.load_gcode(gcode)
    worker.start()

    await asyncio.sleep(0.05)
    worker.enqueue_priority("M221 S80")
    await asyncio.sleep(1.0)

    calls = [c.args[0] if c.args else "" for c in serial.send_line.call_args_list]
    assert "M221 S80" in calls


def sent_lines(serial) -> list[str]:
    return [c.args[0] if c.args else "" for c in serial.send_line.call_args_list]


async def test_estop_flushes_queue():
    worker, serial, events = make_worker(send_delay=0.01)
    gcode = [f"G1 X{i}" for i in range(200)]
    worker.load_gcode(gcode)
    worker.start()

    await asyncio.sleep(0.05)
    await worker.estop()
    await asyncio.sleep(0.2)

    assert worker.status == PrintStatus.STOPPED
    serial.emergency_write.assert_awaited_once_with("M410")
    assert "M410" not in sent_lines(serial)
    assert worker.lines_sent < 200


async def test_estop_while_idle_leaves_nothing_queued():
    worker, serial, events = make_worker()

    await worker.estop()

    serial.emergency_write.assert_awaited_once_with("M410")
    assert worker._priority_queue.empty()

    worker.load_gcode(["G1 X1", "G1 X2"])
    worker.start()
    await asyncio.sleep(0.3)

    assert worker.status == PrintStatus.COMPLETED
    assert sent_lines(serial) == ["M114", "M84 S0", "G1 X1", "G1 X2"]


async def test_load_gcode_drains_priority_queue():
    worker, serial, events = make_worker()
    worker.enqueue_priority("M221 S80")

    worker.load_gcode(["G1 X1"])
    worker.start()
    await asyncio.sleep(0.3)

    assert sent_lines(serial) == ["M114", "M84 S0", "G1 X1"]


async def test_estop_mid_print_bypasses_in_flight_send():
    worker, serial, events = make_worker()
    release = asyncio.Event()
    in_flight = asyncio.Event()

    async def blocked_send(line):
        if line == "M114":
            return "ok"  # print-start seed: answers immediately, doesn't parse
        in_flight.set()
        await release.wait()  # printer hasn't answered "ok" yet
        return "ok"

    serial.send_line = AsyncMock(side_effect=blocked_send)
    worker.load_gcode([f"G1 X{i}" for i in range(10)])
    worker.start()
    await asyncio.wait_for(in_flight.wait(), timeout=1)

    await asyncio.wait_for(worker.estop(), timeout=1)

    serial.emergency_write.assert_awaited_once_with("M410")
    assert not release.is_set()  # the in-flight send is still waiting
    release.set()
    await asyncio.sleep(0.1)


async def test_no_lines_sent_after_estop():
    worker, serial, events = make_worker(send_delay=0.01)
    worker.load_gcode([f"G1 X{i}" for i in range(200)])
    worker.start()
    await asyncio.sleep(0.05)

    await worker.estop()
    sent_at_estop = serial.send_line.call_count
    await asyncio.sleep(0.3)

    assert serial.send_line.call_count == sent_at_estop
    assert worker.status == PrintStatus.STOPPED
    assert worker._task is not None and worker._task.done()


async def test_estop_while_paused_sends_nothing_more():
    worker, serial, events = make_worker(send_delay=0.005)
    worker.load_gcode([f"G1 X{i}" for i in range(50)])
    worker.start()
    await asyncio.sleep(0.05)
    worker.pause()
    await asyncio.sleep(0.05)

    sent_at_estop = serial.send_line.call_count
    await worker.estop()
    await asyncio.sleep(0.2)

    assert serial.send_line.call_count == sent_at_estop
    assert worker._task is not None and worker._task.done()


async def test_serial_emergency_write_not_blocked_by_pending_ok():
    """M410 reaches the wire while another line holds _io_lock waiting for "ok"."""
    reading = threading.Event()
    answer = threading.Event()
    written: list[bytes] = []

    fake = MagicMock()
    fake.is_open = True
    fake.write.side_effect = written.append

    def readline():
        reading.set()
        answer.wait(timeout=2)
        return b"ok\n"

    fake.readline.side_effect = readline

    manager = SerialManager()
    manager._serial = fake

    send_task = asyncio.create_task(manager.send_line("G1 X10"))
    await asyncio.to_thread(reading.wait, 1)

    await asyncio.wait_for(manager.emergency_write("M410"), timeout=1)

    assert written == [b"G1 X10\n", b"M410\n"]
    assert not send_task.done()
    assert [e["content"] for e in manager.log_buffer] == ["G1 X10", "M410"]

    answer.set()
    assert await send_task == "ok"


async def test_pause_resume():
    worker, serial, events = make_worker(send_delay=0.005)
    gcode = [f"G1 X{i}" for i in range(50)]
    worker.load_gcode(gcode)
    worker.start()

    await asyncio.sleep(0.05)
    worker.pause()
    assert worker.status == PrintStatus.PAUSED

    sent_at_pause = worker.lines_sent
    await asyncio.sleep(0.1)
    # Allow at most 1 extra line (in-flight when pause was set)
    assert worker.lines_sent <= sent_at_pause + 1

    worker.resume()
    await asyncio.sleep(1.0)
    assert worker.status == PrintStatus.COMPLETED
    assert worker.lines_sent == 50


async def test_lines_counter():
    worker, serial, events = make_worker()
    worker.load_gcode(["G1 X1", "G1 X2", "G1 X3"])
    worker.start()
    await asyncio.sleep(0.3)

    assert worker.lines_sent == 3
    assert worker.lines_total == 3
