"""Several nozzles selected and a file written for one of them: its plunger
and height words are copied to the other nozzles' axes, so they all print."""

from __future__ import annotations

from backend.gcode_processor import mirror_to_nozzles, parse, render
from backend.main import app
from tests.nozzles import BOTH, LEFT

LEFT_FILE = "\n".join(
    [
        "; for the left nozzle",
        "G91",
        "G92 B0",
        "G0 Z1 F300 ; lift",
        "G1 X10 B-0.1 F300",
        "G4 P300",
    ]
)


def mirrored(text: str, nozzles=BOTH) -> tuple[list[str], str | None]:
    cmds, note = mirror_to_nozzles(parse(text.splitlines()), nozzles)
    return render(cmds), note


def test_left_file_is_copied_to_the_right_nozzle():
    lines, note = mirrored(LEFT_FILE)

    assert lines == [
        "; for the left nozzle",
        "G91",
        "G92 B0 C0",
        "G0 Z1 A1 F300 ; lift",
        "G1 X10 B-0.1 C-0.1 F300",
        "G4 P300",
    ]
    assert note == "Copied B to C and Z to A on every line, so both nozzles print the same path"


def test_right_file_is_copied_to_the_left_nozzle():
    lines, note = mirrored("G91\nG0 A1\nG1 X10 C-0.1")

    assert lines == ["G91", "G0 A1 Z1", "G1 X10 C-0.1 B-0.1"]
    assert note is not None and "C to B and A to Z" in note


def test_dual_file_is_left_alone():
    text = "G91\nG0 Z1 A1\nG1 X10 B-0.1 C-0.1"
    lines, note = mirrored(text)

    assert lines == text.splitlines()
    assert note is None


def test_height_moved_on_both_sides_is_left_alone():
    lines, _ = mirrored("G91\nG0 Z1 A1\nG1 X10 B-0.1")

    assert lines == ["G91", "G0 Z1 A1", "G1 X10 B-0.1 C-0.1"]


def test_one_nozzle_is_left_alone():
    lines, note = mirrored(LEFT_FILE, LEFT)

    assert lines == LEFT_FILE.splitlines()
    assert note is None


async def upload(client, nozzles: list[str], needs_changes: bool = False):
    return await client.post(
        "/upload/gcode",
        params={"nozzles": nozzles, "needs_changes": needs_changes},
        files={"file": ("left.gcode", LEFT_FILE.encode(), "text/plain")},
    )


async def test_two_nozzle_upload_prints_a_left_file_from_both_nozzles(client):
    resp = await upload(client, ["left", "right"])

    assert resp.status_code == 200, resp.text
    gcode = app.state.session.loaded.gcode
    assert "G1 X10 B-0.1 C-0.1 F300" in gcode.lines
    assert gcode.extrusion_axes == ("B", "C")
    assert gcode.height_axes == ("Z", "A")
    assert any("Copied B to C" in w for w in resp.json()["warnings"])


async def test_relative_lab_file_with_needs_changes_is_copied_too(client):
    # Given in any order: the selection is put left to right
    resp = await upload(client, ["right", "left"], needs_changes=True)

    assert resp.status_code == 200, resp.text
    assert "G1 X10 B-0.1 C-0.1 F300" in app.state.session.loaded.gcode.lines


async def test_left_nozzle_upload_is_unchanged(client):
    resp = await upload(client, ["left"])

    assert resp.status_code == 200, resp.text
    assert app.state.session.loaded.gcode.lines == LEFT_FILE.splitlines()
