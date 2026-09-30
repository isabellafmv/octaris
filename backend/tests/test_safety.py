"""Safety guards: no reconnect mid-print, bed limits, syringe travel, and the
unmeasured nozzle offset."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import serial

from backend.config import BedLimits, Config
from backend.gcode_processor import ProcessedGcode, process_gcode
from backend.limits import (
    LimitError,
    check_jog,
    check_path,
    check_plunger_travel,
    plunger_travel_needed,
    start_state,
)
from backend.main import app
from backend.queue_worker import CONNECTION_LOST, PrintStatus, QueueWorker
from backend.serial_manager import SerialError, SerialManager, _open_port

FIXTURES = Path(__file__).parent / "fixtures"
RAW_SAMPLE = (FIXTURES / "raw_sample.gcode").read_text()
ORIGIN = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0, "B": 0.0, "C": 0.0}


def m114(**pos: float) -> str:
    logical = " ".join(f"{axis}:{value:.2f}" for axis, value in pos.items())
    return f"{logical} Count X:0 Y:0 Z:0"


class FakePrinter:
    """Answers "ok" to everything and M114 with `position`. Writing
    `drop_on` raises SerialException, like a USB cable being pulled."""

    def __init__(self, position: str | None = None, drop_on: str | None = None,
                 delay: float = 0.0):
        self.is_open = True
        self.position = position or m114(**ORIGIN)
        self.drop_on = drop_on
        self.delay = delay
        self.written: list[str] = []
        self._pending: list[str] = []

    def write(self, data: bytes) -> None:
        line = data.decode().strip()
        if line == self.drop_on:
            raise serial.SerialException("device reports readiness to read but returned no data")
        self.written.append(line)
        self._pending = [self.position, "ok"] if line == "M114" else ["ok"]

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        pass

    def readline(self) -> bytes:
        if not self._pending:
            time.sleep(0.005)
            return b""
        if self.delay:
            time.sleep(self.delay)
        return (self._pending.pop(0) + "\n").encode()

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False


async def wait_for(condition, timeout: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.005)


@pytest.fixture
def printer(client):
    fake = FakePrinter()
    app.state.serial_manager._serial = fake
    app.state.serial_manager._port = "/dev/fake"
    app.state.is_calibrated = True
    return fake


@pytest.fixture
def events(client):
    _, queue = app.state.event_bus.subscribe()

    def drain() -> list[dict]:
        out = []
        while not queue.empty():
            out.append(queue.get_nowait())
        return out

    return drain


async def upload_sample(client, mode: str = "left", raw: str = RAW_SAMPLE):
    return await client.post(
        "/upload/gcode",
        params={"syringe_mode": mode},
        files={"file": ("sample.gcode", raw.encode(), "text/plain")},
    )


# --- 1. no automatic reconnect during a print --------------------------------


def test_port_opened_with_dtr_and_rts_cleared_before_open():
    calls: list[tuple[str, object]] = []

    class RecordingSerial:
        def __setattr__(self, name, value):
            calls.append((name, value))
            object.__setattr__(self, name, value)

        def open(self):
            calls.append(("open", None))

    with patch("backend.serial_manager.serial.Serial", RecordingSerial):
        _open_port("/dev/fake", 115200)

    opened = calls.index(("open", None))
    assert ("dtr", False) in calls[:opened]
    assert ("rts", False) in calls[:opened]


async def test_serial_error_while_reconnect_forbidden_does_not_reopen():
    fake = FakePrinter(drop_on="G1 X1")
    manager = SerialManager(can_reconnect=lambda: False)
    manager._serial, manager._port = fake, "/dev/fake"

    with patch("backend.serial_manager.serial.Serial") as opener:
        with pytest.raises(SerialError, match="Connection lost"):
            await manager.send_line("G1 X1")
        with pytest.raises(SerialError, match="Not connected"):
            await manager.send_line("G1 X2")

    opener.assert_not_called()
    assert not manager.is_connected


async def test_serial_error_when_idle_reconnects_without_resending():
    dropped = FakePrinter(drop_on="G1 X1")
    fresh = FakePrinter()
    connected: list[str] = []
    manager = SerialManager(on_connect=connected.append)
    manager._serial, manager._port = dropped, "/dev/fake"

    with patch("backend.serial_manager.serial.Serial", return_value=fresh):
        with pytest.raises(SerialError, match="not re-sent"):
            await manager.send_line("G1 X1")

    assert manager.is_connected
    assert connected == ["/dev/fake"]
    assert fresh.written == []  # the failed line is not repeated


async def test_connection_lost_mid_print_stops_for_good(client, printer, events):
    printer.drop_on = "G1 F200 X20 Y20 B-1"
    assert (await upload_sample(client)).status_code == 200
    events()  # discard upload events
    worker = app.state.queue_worker

    with patch("backend.serial_manager.serial.Serial") as opener:
        assert (await client.post("/print/start")).status_code == 200
        await wait_for(lambda: worker.status == PrintStatus.STOPPED)
        await asyncio.sleep(0.05)
        opener.assert_not_called()  # no reconnect

    assert not app.state.serial_manager.is_connected
    assert not worker.resumable
    assert worker.stop_reason == CONNECTION_LOST
    assert app.state.is_calibrated is False
    published = events()
    assert {"type": "status", "value": "stopped"} in published
    assert {"type": "printer", "connected": False, "port": None} in published
    assert {"type": "calibration", "value": "uncalibrated"} in published
    assert {"type": "stop", "resumable": False, "reason": CONNECTION_LOST} in published

    [session] = (await client.get("/history")).json()["sessions"]
    assert session["end_reason"] == "error"
    resp = await client.post("/print/resume")
    assert resp.status_code == 409
    assert "re-zero" in resp.json()["detail"]


async def test_connection_lost_while_paused_stops_print(client, printer):
    assert (await upload_sample(client)).status_code == 200
    worker = app.state.queue_worker
    printer.delay = 0.01
    assert (await client.post("/print/start")).status_code == 200
    await client.post("/print/pause")
    await wait_for(lambda: worker.status == PrintStatus.PAUSED)

    printer.drop_on = "G91"  # the jog's first line
    with patch("backend.serial_manager.serial.Serial") as opener:
        resp = await client.post("/jog", json={"axis": "X", "distance": 1})
    await asyncio.sleep(0.05)

    assert resp.status_code == 500
    opener.assert_not_called()
    assert worker.status == PrintStatus.STOPPED
    assert worker.stop_reason == CONNECTION_LOST
    assert app.state.is_calibrated is False


async def test_idle_reconnect_resets_calibration(client, printer, events):
    printer.drop_on = "M115"
    fresh = FakePrinter()

    with patch("backend.serial_manager.serial.Serial", return_value=fresh):
        resp = await client.post("/gcode/send", json={"line": "M115"})

    assert resp.status_code == 500
    assert "not re-sent" in resp.json()["detail"]
    assert app.state.serial_manager.is_connected
    assert app.state.is_calibrated is False
    published = events()
    assert {"type": "printer", "connected": True, "port": "/dev/fake"} in published
    assert published[-1] == {"type": "calibration", "value": "uncalibrated"}


@pytest.mark.parametrize("route, body", [
    ("/connect", {"port": "/dev/fake"}),
    ("/disconnect", None),
])
async def test_port_changes_refused_during_print(client, printer, route, body):
    app.state.queue_worker._print_active = True

    resp = await client.post(route, json=body)

    assert resp.status_code == 409
    assert app.state.serial_manager.is_connected


async def test_manual_connect_resets_calibration(client):
    app.state.is_calibrated = True
    with patch("backend.serial_manager.serial.Serial", return_value=FakePrinter()):
        assert (await client.post("/connect", json={"port": "/dev/fake"})).status_code == 200
    assert app.state.is_calibrated is False


# --- 2. bed limits ------------------------------------------------------------

BED = BedLimits()  # X/Y -30..30, Z 0..60


def test_check_path_accepts_sample():
    check_path(BED, process_gcode(RAW_SAMPLE, "left").lines)


def test_check_path_names_the_offending_line():
    with pytest.raises(LimitError, match=r"line 3 \(G1 X31 Y0\) moves X to 31 mm") as info:
        check_path(BED, ["G90", "G1 X0 Y0 Z1", "G1 X31 Y0"])
    assert "-30 to 30 mm" in str(info.value)


def test_check_path_catches_relative_moves_from_start_position():
    lines = ["G91", "G1 X10", "G90"]
    check_path(BED, lines)  # unknown start: nothing to check
    check_path(BED, lines, start_state({"X": 15.0, "Y": 0.0, "Z": 1.0}))
    with pytest.raises(LimitError, match="X to 35"):
        check_path(BED, lines, start_state({"X": 25.0, "Y": 0.0, "Z": 1.0}))


def test_check_path_catches_z_below_zero():
    with pytest.raises(LimitError, match="Z to -0.5"):
        check_path(BED, ["G90", "G1 X0 Y0 Z-0.5"])


def test_check_jog():
    check_jog(BED, "X", 25.0, 5.0)
    with pytest.raises(LimitError, match="X to 30.1"):
        check_jog(BED, "X", 25.0, 5.1)
    check_jog(BED, "B", 0.0, -500.0)  # plungers aren't bed axes


async def test_upload_rejects_print_leaving_bed(client):
    raw = RAW_SAMPLE.replace("G1 F200 X20 Y20 E1.0", "G1 F200 X45 Y20 E1.0")

    resp = await upload_sample(client, raw=raw)

    assert resp.status_code == 422
    assert "leaves the bed" in resp.json()["detail"]
    assert "X to 45 mm" in resp.json()["detail"]
    assert app.state.processed_gcode is None


async def test_upload_stl_rejects_print_leaving_bed(client, tmp_path):
    result = ProcessedGcode(lines=["G90", "G1 X0 Y-40 F300"])
    with patch("backend.routers.upload.slice_model", AsyncMock(return_value=result)), \
         patch("backend.routers.upload.DATA_DIR", tmp_path):
        resp = await client.post(
            "/upload", files={"file": ("cube.stl", b"solid", "application/octet-stream")},
        )

    assert resp.status_code == 422
    assert "Y to -40 mm" in resp.json()["detail"]


async def test_print_start_checks_path_from_actual_position(client, printer):
    app.state.processed_gcode = ProcessedGcode(lines=["G91", "G1 X10 F200", "G90"])
    printer.position = m114(X=25, Y=0, Z=1, A=0, B=0, C=0)

    resp = await client.post("/print/start")

    assert resp.status_code == 400
    assert "X to 35 mm" in resp.json()["detail"]
    assert app.state.queue_worker.status == PrintStatus.IDLE


async def test_refused_start_keeps_resume_checkpoint(client, printer):
    worker = app.state.queue_worker
    worker._status = PrintStatus.STOPPED
    worker._checkpoint = object()  # stands in for a real one
    app.state.processed_gcode = ProcessedGcode(lines=["G90", "G1 X50 F200"])

    assert (await client.post("/print/start")).status_code == 400
    assert worker.resumable


async def test_jog_within_bed_is_tracked(client, printer):
    assert (await client.post("/calibration/zero")).status_code == 200
    for _ in range(5):
        assert (await client.post("/jog", json={"axis": "X", "distance": 5})).status_code == 200

    resp = await client.post("/jog", json={"axis": "X", "distance": 10})

    assert resp.status_code == 400
    assert "X to 35 mm" in resp.json()["detail"]
    assert "M114" not in printer.written  # known from G92 + jogs
    assert printer.written[-1] == "G90"  # the refused jog never went out
    assert (await client.post("/jog", json={"axis": "Z", "distance": -1})).status_code == 400


async def test_jog_seeds_unknown_position_from_m114(client, printer):
    printer.position = m114(X=0, Y=28, Z=1, A=0, B=0, C=0)

    resp = await client.post("/jog", json={"axis": "Y", "distance": 5})

    assert resp.status_code == 400
    assert "Y to 33 mm" in resp.json()["detail"]
    assert printer.written == ["M114"]


async def test_jog_unlimited_before_calibration(client, printer):
    app.state.is_calibrated = False

    resp = await client.post("/jog", json={"axis": "X", "distance": 100})

    assert resp.status_code == 200
    assert "M114" not in printer.written


async def test_position_unknown_after_stop(client, printer):
    manager = app.state.serial_manager
    await manager.send_line("G92 X0 Y0 Z0")
    assert manager.position["X"] == 0
    await manager.emergency_write("M410")
    assert manager.position["X"] is None


# --- 3. syringe travel ------------------------------------------------------------


def test_plunger_travel_is_peak_physical_push():
    lines = [
        "G90",
        "G1 B-1 F400",  # pressurize: 1 mm pushed
        "G92 B0",  # coordinates only
        "G1 X1 B-2",  # 3 mm pushed
        "G91",
        "G1 B1",  # retract: 2 mm
        "G1 B-1.5",  # prime past it: 3.5 mm
        "G90",
    ]
    needed = plunger_travel_needed(lines, start_state(ORIGIN))
    assert needed["B"] == pytest.approx(3.5)
    assert needed["C"] == 0


def test_check_plunger_travel():
    check_plunger_travel({"B": 10.0, "C": 0.0}, 10.0)
    with pytest.raises(LimitError, match=r"12 mm of left plunger \(B\) travel.*only has 10 mm"):
        check_plunger_travel({"B": 12.0}, 10.0)


def sample_need() -> float:
    lines = process_gcode(RAW_SAMPLE, "left").lines
    return plunger_travel_needed(lines, start_state(ORIGIN))["B"]


async def test_print_start_refused_when_syringe_too_short(client, printer):
    app.state.config.syringe_travel_mm = sample_need() * 0.9
    assert (await upload_sample(client)).status_code == 200

    resp = await client.post("/print/start")

    assert resp.status_code == 400
    assert "left plunger (B) travel" in resp.json()["detail"]
    assert app.state.queue_worker.status == PrintStatus.IDLE


async def test_print_start_accounts_for_flow_override(client, printer):
    app.state.config.syringe_travel_mm = sample_need() * 1.2
    assert (await upload_sample(client)).status_code == 200

    await client.post("/extrusion", json={"rate": 150})
    assert (await client.post("/print/start")).status_code == 400

    await client.post("/extrusion", json={"rate": 100})
    assert (await client.post("/print/start")).status_code == 200


async def test_low_travel_warning_emitted_once():
    manager = MagicMock()
    manager.send_line = AsyncMock(return_value="ok")
    events: list[dict] = []
    worker = QueueWorker(manager, on_event=events.append, syringe_travel_mm=10.0)
    worker.load_gcode(["G90", "G1 B-5", "G1 B-8.9", "G1 B-9.2", "G1 B-9.5", "G1 B-9.9"])

    worker.start(start_position=dict(ORIGIN))
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)

    warnings = [e for e in events if e["type"] == "warning"]
    assert warnings == [{
        "type": "warning",
        "message": "The left syringe (B) is nearly empty: 0.8 of 10 mm plunger travel left.",
    }]


# --- 5. unmeasured nozzle offset ----------------------------------------------


@pytest.mark.parametrize("mode", ["right", "both"])
async def test_right_and_dual_prints_refused_until_offset_measured(client, printer, mode):
    app.state.config.bed.x.max = 60  # the right nozzle's +31 mm shift
    assert (await upload_sample(client, mode=mode)).status_code == 200

    resp = await client.post("/print/start")

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "NOZZLE_OFFSET_X" in detail
    assert '"nozzle_offset_measured": true' in detail
    assert "config.json" in detail


async def test_right_print_allowed_once_offset_measured(client, printer):
    app.state.config.nozzle_offset_measured = True
    app.state.config.bed.x.max = 60  # the right nozzle's +31 mm shift
    assert (await upload_sample(client, mode="right")).status_code == 200

    assert (await client.post("/print/start")).status_code == 200


async def test_left_print_unaffected_by_offset_flag(client, printer):
    assert app.state.config.nozzle_offset_measured is False
    assert (await upload_sample(client)).status_code == 200

    assert (await client.post("/print/start")).status_code == 200


def test_config_defaults():
    config = Config()
    assert config.nozzle_offset_measured is False
    assert config.syringe_travel_mm > 0
    assert (config.bed.x.min, config.bed.x.max) == (-30, 30)
    with pytest.raises(ValueError):
        Config(bed={"x": {"min": 5, "max": -5}})
