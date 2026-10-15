"""POST /jog/origin: travel to X0 Y0 at the current height and, on request,
lower the selected nozzles to zero height — one sequence."""

from __future__ import annotations

import pytest

from backend.main import app
from backend.printer import AxisRange, BedLimits
from backend.queue_worker import PrintStatus
from tests.serial_fakes import attach
from tests.test_print_gate import set_status
from tests.test_safety import FakePrinter, upload_sample


@pytest.fixture
async def printer(client):
    fake = FakePrinter()
    attach(app.state.serial_manager, fake)
    app.state.session.zeroed = {"X", "Y", "Z", "A"}
    return fake


def select(*nozzles: str) -> None:
    app.state.session._calibration_nozzles = nozzles


async def origin(client, lower: bool = False):
    return await client.post("/jog/origin", json={"lower": lower})


async def test_go_to_origin_moves_xy_only(client, printer):
    resp = await origin(client)

    assert resp.status_code == 200
    assert printer.written == ["G90", "G1 X0 Y0 F300"]
    assert resp.json()["lines"] == printer.written


@pytest.mark.parametrize(
    "nozzles, lower_line",
    [(["left"], "G1 Z0 F300"), (["right"], "G1 A0 F300"), (["left", "right"], "G1 Z0 A0 F300")],
)
async def test_lower_moves_the_height_axes_of_the_selected_nozzles(client, printer, nozzles, lower_line):
    select(*nozzles)

    resp = await origin(client, lower=True)

    assert resp.status_code == 200
    # X/Y first, at the current height, then down
    assert printer.written == ["G90", "G1 X0 Y0 F300", lower_line]


async def test_restores_relative_mode(client, printer):
    await app.state.serial_manager.send("G91")

    assert (await origin(client, lower=True)).status_code == 200

    assert printer.written[-1] == "G91"
    assert app.state.serial_manager.relative


async def test_stays_absolute_when_it_was_absolute(client, printer):
    await app.state.serial_manager.send("G90")

    assert (await origin(client)).status_code == 200

    assert printer.written[-1] == "G1 X0 Y0 F300"
    assert not app.state.serial_manager.relative


async def test_back_to_relative_while_a_print_is_loaded(client, printer):
    assert (await upload_sample(client)).status_code == 200
    await app.state.serial_manager.send("G90")

    assert (await origin(client)).status_code == 200

    assert printer.written[-1] == "G91"


@pytest.mark.parametrize("lower", [False, True])
async def test_refused_while_printing(client, printer, lower):
    set_status(PrintStatus.PRINTING)

    resp = await origin(client, lower)

    assert resp.status_code == 409
    assert printer.written == []


async def test_allowed_while_paused(client, printer):
    set_status(PrintStatus.PAUSED)

    assert (await origin(client)).status_code == 200


async def test_lower_refused_until_the_height_is_zeroed(client, printer):
    select("left", "right")
    app.state.session.zeroed = {"X", "Y", "Z"}  # the right nozzle not yet

    resp = await origin(client, lower=True)

    assert resp.status_code == 400
    assert "Zero the nozzle height first" in resp.json()["detail"]
    assert printer.written == []


async def test_xy_allowed_before_calibration(client, printer):
    app.state.session.zeroed = set()

    assert (await origin(client)).status_code == 200


@pytest.mark.parametrize(
    "bed, lower, message",
    [
        (BedLimits(x=AxisRange(min=5, max=60)), False, "X to 0 mm"),
        (BedLimits(y=AxisRange(min=-60, max=-1)), False, "Y to 0 mm"),
        (BedLimits(z=AxisRange(min=1, max=60)), True, "Z to 0 mm"),
    ],
)
async def test_origin_off_the_bed_is_refused(client, printer, monkeypatch, bed, lower, message):
    monkeypatch.setattr(app.state.session.profile, "bed", bed)

    resp = await origin(client, lower)

    assert resp.status_code == 400
    assert message in resp.json()["detail"]
    assert printer.written == []


async def test_height_limit_not_checked_without_lower(client, printer, monkeypatch):
    monkeypatch.setattr(app.state.session.profile, "bed", BedLimits(z=AxisRange(min=1, max=60)))

    assert (await origin(client)).status_code == 200


async def test_needs_a_connection(client):
    assert (await origin(client)).status_code == 400
