"""The virtual printer on its own, spoken to through its pyserial interface."""

from __future__ import annotations

import math
import time

import pytest

from backend.serial_manager import number_line
from backend.virtual_printer import VirtualPrinter


@pytest.fixture
def make():
    printers: list[VirtualPrinter] = []

    def make(**options) -> VirtualPrinter:
        printer = VirtualPrinter(**options)
        printers.append(printer)
        return printer

    yield make
    for printer in printers:
        printer.close()


def read_until(
    printer: VirtualPrinter, done=lambda line: line.startswith("ok"), timeout: float = 3.0
) -> list[str]:
    """Lines from the printer up to and including the first `done` one."""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = printer.readline().decode().strip()
        if line:
            lines.append(line)
            if done(line):
                return lines
    raise AssertionError(f"timed out; got {lines}")


def send(printer: VirtualPrinter, line: str) -> list[str]:
    printer.write((line + "\n").encode())
    return read_until(printer)


def test_ok_after_the_configured_delay(make):
    printer = make(ok_delay_s=0.1)
    started = time.monotonic()
    assert send(printer, "G90") == ["ok"]
    assert time.monotonic() - started >= 0.1


def test_positions_through_g90_g91_g92(make):
    printer = make(speed=math.inf)
    send(printer, "G1 X10 Y5 Z1 A2 B-1 C3 F600")
    send(printer, "G91")
    send(printer, "G1 X1 B-0.5")
    send(printer, "G90")
    send(printer, "G0 Y0")
    assert printer.position == {"X": 11, "Y": 0, "Z": 1, "A": 2, "B": -1.5, "C": 3}

    send(printer, "G92 X0 B0")  # new logical zero; the axes stay put
    send(printer, "G1 X1")
    assert printer.position["X"] == 1 and printer.position["B"] == 0
    send(printer, "M92 X800 Y800 Z800 A800 B800 C800")
    [m114, ok] = send(printer, "M114")
    # Marlin's format: logical position, then raw step counts (G92 doesn't move them).
    assert m114 == ("X:1.00 Y:0.00 Z:1.00 A:2.00 B:0.00 C:3.00 Count X:9600 Y:0 Z:800 A:1600 B:-1200 C:2400")
    assert ok == "ok"


def test_move_time_from_distance_and_feedrate(make):
    printer = make(speed=10)
    send(printer, "G1 X100 F6000")  # 100 mm at 100 mm/s: 1 s, at speed 10 0.1 s
    started = time.monotonic()
    send(printer, "M400")
    assert 0.07 < time.monotonic() - started < 0.3
    assert printer.machine_position()["X"] == 100


def test_planner_holds_16_moves_then_blocks(make):
    printer = make(speed=1)
    for i in range(16):
        assert send(printer, f"G1 X{i + 1} F60") == ["ok"]  # 1 s each, all buffered
    assert printer.moves_planned == 16
    printer.write(b"G1 X17 F60\n")
    assert printer.readline() == b""  # no "ok" while the planner is full
    printer.write(b"M410\n")
    assert read_until(printer) == ["ok"]  # the blocked move is dropped...
    assert read_until(printer) == ["ok"]  # ...and M410 gets its own "ok"


def test_busy_while_blocked(make):
    printer = make(speed=10, planner_depth=1, busy_interval_s=0.05)
    send(printer, "G1 X100 F600")  # 1 s
    replies = send(printer, "G1 X0 F600")
    assert replies.count("echo:busy: processing") >= 10
    assert replies[-1] == "ok"


@pytest.mark.parametrize("fault", ["text", "line number"])
def test_corrupted_line_asks_for_a_resend(make, fault):
    printer = make()
    send(printer, "M110 N0")
    printer.corrupt_next("G1 X1" if fault == "text" else 1)
    assert send(printer, number_line(1, "G1 X1")) == [
        "Error:checksum mismatch, Last Line: 0",
        "Resend: 1",
        "ok",
    ]
    assert send(printer, number_line(1, "G1 X1")) == ["ok"]
    assert printer.executed == ["M110 N0", "G1 X1"]


def test_dropped_line_then_the_next_asks_for_a_resend(make):
    printer = make()
    send(printer, "M110 N0")
    printer.drop_next(1)
    printer.write((number_line(1, "G1 X1") + "\n").encode())
    assert printer.readline() == b""  # lost: no reply
    assert send(printer, number_line(2, "G1 X2")) == [
        "Error:Line Number is not Last Line Number+1, Last Line: 0",
        "Resend: 1",
        "ok",
    ]


def test_temperatures(make):
    printer = make(report_scale=0.01)
    assert send(printer, "M105") == ["ok T:21.30 /0.00 B:20.10 /0.00 @:0 B@:0"]
    send(printer, "M155 S1")  # every 10 ms here
    reports = read_until(printer, lambda line: line.startswith("T:"))
    assert reports == ["T:21.30 /0.00 B:20.10 /0.00 @:0 B@:0"]


def test_heating_waits_for_the_target(make):
    printer = make(speed=50, busy_interval_s=0.05)  # 100 °C/s
    replies = send(printer, "M109 S40")
    assert replies[-1] == "ok"
    assert abs(printer.temperatures["T"][0] - 40) < 1


def test_each_heater_moves_towards_its_target(make):
    printer = make(speed=50, sensors={"T0": 21.0, "T1": 21.0, "B": 20.0, "C": 22.0})
    for command in ("M104 S37", "M104 T1 S10", "M140 S30", "M141 S25"):
        assert send(printer, command) == ["ok"]
    assert {key: target for key, (_, target) in printer.temperatures.items()} == {
        "T0": 37,  # the active tool
        "T1": 10,
        "B": 30,
        "C": 25,
    }
    time.sleep(0.3)  # 100 °C/s
    assert {key: round(actual) for key, (actual, _) in printer.temperatures.items()} == {
        "T0": 37,
        "T1": 10,
        "B": 30,
        "C": 25,
    }

    send(printer, "M104 T1 S0")  # off: back to ambient
    time.sleep(0.3)
    assert printer.temperatures["T1"] == (21.0, 0.0)


def test_several_tools_report_the_active_one_as_t(make):
    printer = make(sensors={"T0": 21.0, "T1": 22.0, "B": 20.0})
    assert send(printer, "M105") == ["ok T:21.00 /0.00 T0:21.00 /0.00 T1:22.00 /0.00 B:20.00 /0.00 @:0 B@:0"]


def test_heaters_it_lacks_are_ignored(make):
    printer = make()  # T and B only
    assert send(printer, "M141 S30") == ["ok"]
    assert send(printer, "M104 T3 S30") == ["ok"]
    assert printer.temperatures == {"T": (21.3, 0.0), "B": (20.1, 0.0)}


def test_without_sensors_nothing_is_reported(make):
    printer = make(sensors={}, report_scale=0.01)
    assert send(printer, "M105") == ["ok"]
    send(printer, "M155 S1")
    time.sleep(0.1)
    assert printer.readline() == b""


def test_m115_reports_the_emergency_parser(make):
    replies = send(make(), "M115")
    assert replies[0].startswith("FIRMWARE_NAME:Marlin")
    assert "Cap:EMERGENCY_PARSER:1" in replies
    assert replies[-1] == "ok"


def test_m410_stops_where_the_axes_are(make):
    printer = make(speed=10)
    send(printer, "G1 X100 F600")  # 1 s
    send(printer, "G1 Y100 F600")
    time.sleep(0.3)
    printer.write(b"M410\n")
    assert read_until(printer) == ["ok"]
    stopped = dict(printer.position)
    assert 10 < stopped["X"] < 60 and stopped["Y"] == 0  # part way along the first move
    assert printer.moves_planned == 0
    assert send(printer, "M400") == ["ok"]  # nothing left to wait for
    time.sleep(0.1)
    assert printer.machine_position() == stopped
    assert send(printer, "M114")[0].startswith(f"X:{stopped['X']:.2f} Y:0.00")


def test_hold_freezes_motion_at_a_point(make):
    printer = make(speed=10)
    printer.hold_at("X100", 0.25)
    send(printer, "G1 X100 F600")
    assert printer.held.wait(2)
    time.sleep(0.05)
    assert printer.machine_position()["X"] == pytest.approx(25)
    printer.release()
    send(printer, "M400")
    assert printer.machine_position()["X"] == 100
