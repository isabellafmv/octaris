import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.queue_worker import PrintStatus, QueueWorker


def make_worker(events: list | None = None, send_delay: float = 0.0):
    serial = MagicMock()

    async def slow_send(line):
        if send_delay:
            await asyncio.sleep(send_delay)
        return "ok"

    serial.send_line = AsyncMock(side_effect=slow_send)
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
    assert serial.send_line.call_count == 3

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


async def test_stop_flushes_queue():
    worker, serial, events = make_worker(send_delay=0.01)
    gcode = [f"G1 X{i}" for i in range(200)]
    worker.load_gcode(gcode)
    worker.start()

    await asyncio.sleep(0.05)
    await worker.stop()
    await asyncio.sleep(0.2)

    assert worker.status == PrintStatus.STOPPED
    calls = [c.args[0] if c.args else "" for c in serial.send_line.call_args_list]
    assert "M410" in calls
    assert worker.lines_sent < 200


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
