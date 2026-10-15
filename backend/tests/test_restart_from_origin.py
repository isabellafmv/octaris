"""Restart (POST /print/start with from_origin) returns to the zero point
first, also for a file sent as uploaded."""

from __future__ import annotations

from backend.main import app
from tests.serial_fakes import attach
from tests.test_safety import FakePrinter, m114, wait_for

# A relative lab file, kept as uploaded (no start point of its own)
LAB_FILE = "G91\nG1 X1 B-0.1 F200\nG1 Y1 B-0.1\n"
AWAY = m114(X=5.0, Y=-3.0, Z=2.0, A=0.0, B=-0.2, C=0.0)


async def upload_as_is(client):
    resp = await client.post(
        "/upload/gcode",
        params={"nozzles": list(("left",)), "needs_changes": False},
        files={"file": ("lab.gcode", LAB_FILE.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text


def setup_printer(position: str = AWAY) -> FakePrinter:
    fake = FakePrinter(position=position)
    attach(app.state.serial_manager, fake)
    app.state.session.zeroed = {"X", "Y", "Z", "B"}
    app.state.session._calibration_nozzles = ("left",)
    return fake


async def before_print(fake: FakePrinter) -> list[str]:
    """What was sent between M114 and the print's own first line."""
    await wait_for(lambda: "G1 X1 B-0.1 F200" in fake.written)
    start = fake.written.index("M114") + 1
    return fake.written[start : fake.written.index("G1 X1 B-0.1 F200")]


async def test_restart_returns_to_the_zero_point_first(client):
    fake = setup_printer()
    await upload_as_is(client)

    resp = await client.post("/print/start", json={"from_origin": True})

    assert resp.status_code == 200, resp.text
    # Lift 5 mm clear (from Z2), travel to X0 Y0, lower to height 0; the plungers stay put
    assert (await before_print(fake))[:4] == ["G91", "G1 Z5 F300", "G1 X-5 Y3 F300", "G1 Z-7 F300"]


async def test_plain_start_of_an_uploaded_file_starts_where_the_nozzle_is(client):
    fake = setup_printer()
    await upload_as_is(client)

    assert (await client.post("/print/start")).status_code == 200

    assert not any(line.startswith("G1 X-5") for line in await before_print(fake))


async def test_restart_needs_the_zero_point(client):
    setup_printer()
    app.state.session.zeroed = set()
    await upload_as_is(client)

    resp = await client.post("/print/start", json={"from_origin": True})

    assert resp.status_code == 400
