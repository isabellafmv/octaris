"""Several syringes selected: each prints its own copy of the path, offset
from the first selected one, and every copy has to stay on the bed (on
upload, at print start, in the editor, on Restart, for jogs and Go to
Origin). Before slicing, the model plus the syringes' spread has to fit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend import slicer
from backend.gcode_processor import process_gcode
from backend.limits import (
    LimitError,
    check_fits_bed,
    check_jog,
    check_move_to,
    check_path,
    copies_of,
    start_state,
)
from backend.main import app
from backend.printer import AxisRange, BedLimits, PrinterProfile, custom_profile
from tests.nozzles import PRINTESS_1
from tests.serial_fakes import attach
from tests.test_safety import FakePrinter, m114, wait_for

# Wider than the default, as in the example config: X/Y −45 to 45 mm
BED = BedLimits(x=AxisRange(min=-45, max=45), y=AxisRange(min=-45, max=45))

TWO_NOZZLES = [
    {"id": "left", "name": "Left", "height_axis": "Z", "plunger_axis": "B"},
    {"id": "right", "name": "Right", "height_axis": "A", "plunger_axis": "C", "offset_x": 30.0},
]
# Four syringes on a 20 mm grid, the back ones 20 mm further in +Y
FOUR_NOZZLES = [
    {"id": "fl", "name": "Front left", "height_axis": "Z", "plunger_axis": "B"},
    {"id": "fr", "name": "Front right", "height_axis": "A", "plunger_axis": "C", "offset_x": 20.0},
    {"id": "bl", "name": "Back left", "height_axis": "U", "plunger_axis": "V", "offset_y": 20.0},
    {
        "id": "br",
        "name": "Back right",
        "height_axis": "W",
        "plunger_axis": "K",
        "offset_x": 20.0,
        "offset_y": 20.0,
    },
]
TWO = custom_profile(PRINTESS_1, TWO_NOZZLES, nozzle_offset_measured=True)
FOUR = custom_profile(PRINTESS_1, FOUR_NOZZLES, nozzle_offset_measured=True)
ALL_FOUR = ["fl", "fr", "bl", "br"]

ADVICE = (
    "Move the model, select fewer syringes, or check the zero point, the syringe offsets "
    "and the bed limits (Edit setup on the Setup screen)."
)

# A 10 × 10 mm square, sliced for one extruder (relative E)
SQUARE = "\n".join(
    [
        "M83",
        "G0 X0 Y0 Z0.3 F300",
        "G1 X10 Y0 E0.5 F200",
        "G1 X10 Y10 E0.5",
        "G1 X0 Y10 E0.5",
        "G1 X0 Y0 E0.5",
    ]
)


def path_error(profile: PrinterProfile, ids: list[str], lines: list[str]) -> LimitError:
    selected = profile.select(ids)
    start = start_state(dict.fromkeys(["X", "Y", *profile.axes.heights], 0.0), profile.axes.all)
    with pytest.raises(LimitError) as exc:
        check_path(BED, lines, start, [n.height_axis for n in selected], copies_of(selected))
    return exc.value


def path_ok(profile: PrinterProfile, ids: list[str], lines: list[str]) -> None:
    selected = profile.select(ids)
    start = start_state(dict.fromkeys(["X", "Y", *profile.axes.heights], 0.0), profile.axes.all)
    check_path(BED, lines, start, [n.height_axis for n in selected], copies_of(selected))


# --- check_path ---------------------------------------------------------------------------


def test_copies_are_offset_from_the_first_selected_syringe():
    assert [(c.name, c.dx, c.dy) for c in copies_of(FOUR.select(["fr", "br"]))] == [
        ("front right", 0.0, 0.0),
        ("back right", 0.0, 20.0),
    ]


def test_the_right_syringes_copy_leaves_the_bed():
    error = path_error(TWO, ["left", "right"], ["G90", "G1 X10 Y0", "G1 X22 Y0"])

    assert str(error) == (
        f"The right syringe's copy leaves the bed: line 3 moves it to X 52 mm (bed −45 to 45 mm). {ADVICE}"
    )
    assert error.line == 3
    # Within reach of both, and the same path for the left syringe alone
    path_ok(TWO, ["left", "right"], ["G90", "G1 X15 Y0"])
    path_ok(TWO, ["left"], ["G90", "G1 X10 Y0", "G1 X22 Y0"])


def test_one_syringe_keeps_its_message():
    error = path_error(TWO, ["left"], ["G90", "G1 X50 Y0"])
    assert str(error).startswith("The print leaves the bed: line 2 (G1 X50 Y0) moves X to 50 mm")


def test_the_first_syringe_is_named_too():
    error = path_error(TWO, ["left", "right"], ["G91", "G1 X-46"])
    assert str(error).startswith("The left syringe's copy leaves the bed: line 2 moves it to X −46 mm")


def test_each_syringe_checks_its_own_height():
    error = path_error(TWO, ["left", "right"], ["G91", "G1 Z1 A1", "G1 A-2"])
    assert str(error).startswith(
        "The right syringe's copy leaves the bed: line 3 moves it to A −1 mm (bed 0 to 60 mm)"
    )


def test_four_syringes_in_x_and_y():
    # X 30: both right-hand copies are at 50, the front one is named first
    error = path_error(FOUR, ALL_FOUR, ["G90", "G1 X30 Y0"])
    assert str(error).startswith("The front right syringe's copy leaves the bed: line 2 moves it to X 50 mm")
    # Y 30: the back copies are at 50
    error = path_error(FOUR, ALL_FOUR, ["G91", "G1 Y10", "G1 Y20"])
    assert str(error).startswith("The back left syringe's copy leaves the bed: line 3 moves it to Y 50 mm")
    assert error.line == 3
    path_ok(FOUR, ALL_FOUR, ["G90", "G1 X25 Y25", "G1 X-45 Y-45"])


def test_offsets_count_from_the_first_selected_syringe():
    # The back pair alone: zeroed at the back left, so Y 30 is fine
    path_ok(FOUR, ["bl", "br"], ["G90", "G1 X20 Y30"])
    error = path_error(FOUR, ["bl", "br"], ["G90", "G1 X30 Y30"])
    assert str(error).startswith("The back right syringe's copy leaves the bed: line 2 moves it to X 50 mm")


# --- jogs and Go to Origin ------------------------------------------------------------------


def test_check_jog_checks_every_copy():
    copies = copies_of(TWO.select(["left", "right"]))
    check_jog(BED, TWO.axes.heights, "X", 10.0, 5.0, copies)
    with pytest.raises(LimitError) as exc:
        check_jog(BED, TWO.axes.heights, "X", 10.0, 6.0, copies)
    assert str(exc.value) == "This jog would move the right syringe to X 46 mm, off the bed (−45 to 45 mm)"
    # One syringe: as before
    check_jog(BED, TWO.axes.heights, "X", 10.0, 6.0, copies[:1])
    with pytest.raises(LimitError, match="This jog would move the left syringe to Y 46 mm"):
        check_jog(BED, TWO.axes.heights, "Y", 40.0, 6.0, copies)
    with pytest.raises(LimitError, match="This jog would move Y to 46 mm"):
        check_jog(BED, TWO.axes.heights, "Y", 40.0, 6.0, copies[:1])


def test_check_move_to_checks_every_copy():
    copies = copies_of(FOUR.select(ALL_FOUR))
    narrow = BedLimits(x=AxisRange(min=-10, max=10), y=AxisRange(min=-10, max=30))
    check_move_to(narrow, FOUR.axes.heights, "Y", 0.0, copies)
    with pytest.raises(LimitError) as exc:
        check_move_to(narrow, FOUR.axes.heights, "X", 0.0, copies)
    assert (
        str(exc.value)
        == "This move would take the front right syringe to X 20 mm, off the bed (−10 to 10 mm)"
    )


# --- before slicing -------------------------------------------------------------------------


def test_check_fits_bed():
    two = TWO.select(["left", "right"])
    check_fits_bed((15.0, 60.0), two, BedLimits())  # 15 + 30 mm in a 60 mm bed
    with pytest.raises(LimitError) as exc:
        check_fits_bed((40.0, 10.0), two, BedLimits())
    assert str(exc.value) == (
        "The model doesn't fit on the bed with the left and right syringes: it is 40.0 mm across in X "
        "and the syringes are 30 mm apart, 70.0 mm in all, but the bed is 60 mm across in X (−30 to 30 mm). "
        "Select fewer syringes or use a smaller model."
    )
    four = FOUR.select(ALL_FOUR)
    with pytest.raises(LimitError, match="front left, front right, back left and back right syringes.*in Y"):
        check_fits_bed((30.0, 45.0), four, BedLimits())
    # One syringe: only the build volume applies, as before
    check_fits_bed((100.0, 100.0), TWO.select(["left"]), BedLimits())


def stl(width: float, depth: float, height: float) -> bytes:
    """An ASCII STL whose bounding box is width × depth × height."""
    return (
        "solid box\nfacet normal 0 0 1\nouter loop\n"
        f"vertex 0 0 0\nvertex {width} {depth} 0\nvertex 0 0 {height}\n"
        "endloop\nendfacet\nendsolid box\n"
    ).encode()


@pytest.fixture
def cura(monkeypatch):
    """CuraEngine replaced by a fake that writes SQUARE; lists its calls."""
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[cmd.index("-o") + 1]).write_text(SQUARE)
        proc = MagicMock(returncode=0, pid=1)
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    monkeypatch.setattr(slicer, "_find_cura_engine", lambda: "CuraEngine")
    monkeypatch.setattr(slicer.asyncio, "create_subprocess_exec", fake_exec)
    return calls


async def upload_stl(client, tmp_path, content: bytes, nozzles: list[str]):
    with patch("backend.session.DATA_DIR", tmp_path):
        return await client.post(
            "/upload",
            params={"nozzles": nozzles},
            files={"file": ("model.stl", content, "application/octet-stream")},
        )


async def test_model_too_wide_for_the_syringes_is_refused_before_slicing(client, tmp_path, cura):
    resp = await upload_stl(client, tmp_path, stl(40, 10, 5), ["left", "right"])

    assert resp.status_code == 422
    assert resp.json()["detail"].startswith(
        "The model doesn't fit on the bed with the left and right syringes: it is 40.0 mm across in X"
    )
    assert cura == []  # CuraEngine never ran
    assert app.state.session.loaded is None


async def test_model_that_fits_is_sliced(client, tmp_path, cura):
    # Room for the right syringe's copy of the sliced square (X 0 to 10)
    app.state.session.profile.bed.x = AxisRange(min=-30, max=45)
    for nozzles in (["left"], ["left", "right"]):
        resp = await upload_stl(client, tmp_path, stl(10, 10, 5), nozzles)
        assert resp.status_code == 200, resp.text
    # One syringe: the bed isn't a limit before slicing, only the build volume
    assert (await upload_stl(client, tmp_path, stl(50, 10, 5), ["left"])).status_code == 200
    assert len(cura) == 3


# --- through the API: a custom profile on a ±45 mm bed ------------------------------------


async def save(client, nozzles: list[dict]) -> None:
    resp = await client.put("/printer/custom", json={"nozzles": nozzles, "nozzle_offset_measured": True})
    assert resp.status_code == 200, resp.text
    app.state.session.profile.bed = BED.model_copy(deep=True)


def wide_square(x: float) -> str:
    """SQUARE, stretched to `x` mm in X."""
    return SQUARE.replace("X10", f"X{x:g}")


async def upload_gcode(client, raw: str, nozzles: list[str], needs_changes: bool = True):
    return await client.post(
        "/upload/gcode",
        params={"nozzles": nozzles, "needs_changes": needs_changes},
        files={"file": ("print.gcode", raw.encode(), "text/plain")},
    )


@pytest.mark.parametrize(
    "nozzles, selected, width, name, x",
    [
        (TWO_NOZZLES, ["left", "right"], 22, "right", 52),
        (FOUR_NOZZLES, ALL_FOUR, 30, "front right", 50),
    ],
)
async def test_upload_is_refused_for_a_copy_off_the_bed(client, nozzles, selected, width, name, x):
    await save(client, nozzles)

    resp = await upload_gcode(client, wide_square(width), selected)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail.startswith(f"The {name} syringe's copy leaves the bed: line ")
    assert f"moves it to X {x} mm (bed −45 to 45 mm). {ADVICE}" in detail
    # The first syringe alone prints the same file
    assert (await upload_gcode(client, wide_square(width), selected[:1])).status_code == 200


async def test_sliced_print_is_refused_for_a_copy_off_the_bed(client, tmp_path):
    await save(client, FOUR_NOZZLES)
    sliced = process_gcode(wide_square(30), FOUR.select(ALL_FOUR), axes=FOUR.axes)

    with patch("backend.session.slice_model", AsyncMock(return_value=sliced)):
        resp = await upload_stl(client, tmp_path, b"solid", ALL_FOUR)

    assert resp.status_code == 422
    assert resp.json()["detail"].startswith("The front right syringe's copy leaves the bed: line ")


async def test_edit_is_refused_for_a_copy_off_the_bed(client):
    await save(client, TWO_NOZZLES)
    assert (await upload_gcode(client, SQUARE, ["left", "right"])).status_code == 200
    lines = list(app.state.session.loaded.gcode.lines)
    # The first side of the square: 10 mm in +X, made 22
    number = next(i for i, line in enumerate(lines, start=1) if " X10 " in line)
    lines[number - 1] = lines[number - 1].replace("X10", "X22", 1)

    resp = await client.put(
        "/gcode/loaded", content="\n".join(lines).encode(), headers={"Content-Type": "text/plain"}
    )

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"].startswith(
        f"The right syringe's copy leaves the bed: line {number} moves it to X 52 mm"
    )
    assert body["errors"] == [{"line": number, "message": body["detail"]}]


# A relative lab file, sent as uploaded: it starts wherever the head is
LAB_FILE = "G91\nG1 X20 B-0.1 F200\n"


def printer_at(**position: float) -> FakePrinter:
    fake = FakePrinter(position=m114(**{**dict.fromkeys("XYZABC", 0.0), **position}))
    attach(app.state.serial_manager, fake)
    app.state.session.zeroed = {"X", "Y", "Z", "A"}
    return fake


async def test_start_checks_every_copy_from_the_actual_position(client):
    await save(client, TWO_NOZZLES)
    printer_at(X=0.0)
    assert (await upload_gcode(client, LAB_FILE, ["left", "right"], needs_changes=False)).status_code == 200

    # From X0 the right syringe ends at X50 (its 30 mm + 20)
    resp = await client.post("/print/start")

    assert resp.status_code == 400
    assert resp.json()["detail"].startswith(
        "The right syringe's copy leaves the bed: line 2 moves it to X 50 mm"
    )
    assert app.state.queue_worker.lines_total == 0  # nothing was loaded into the worker


async def test_start_from_where_every_copy_fits(client):
    await save(client, TWO_NOZZLES)
    fake = printer_at(X=-30.0)
    assert (await upload_gcode(client, LAB_FILE, ["left", "right"], needs_changes=False)).status_code == 200

    assert (await client.post("/print/start")).status_code == 200
    await wait_for(lambda: "G1 X20 B-0.1 C-0.1 F200" in fake.written)


async def test_restart_from_origin_checks_every_copy(client):
    await save(client, TWO_NOZZLES)
    fake = printer_at(X=-30.0)
    assert (await upload_gcode(client, LAB_FILE, ["left", "right"], needs_changes=False)).status_code == 200

    # Back at X0 first, so the right syringe would end at X50
    resp = await client.post("/print/start", json={"from_origin": True})

    assert resp.status_code == 400
    assert resp.json()["detail"].startswith(
        "The right syringe's copy leaves the bed: line 2 moves it to X 50 mm"
    )
    assert not any(line.startswith("G1 X30") for line in fake.written)  # never travelled


async def zero(client, nozzles: list[str]) -> None:
    for nozzle in nozzles:
        resp = await client.post("/calibration/zero", json={"nozzle": nozzle, "nozzles": nozzles})
        assert resp.status_code == 200, resp.text


async def test_jog_checks_every_selected_syringe(client):
    await save(client, TWO_NOZZLES)
    fake = printer_at()
    await zero(client, ["left", "right"])

    assert (await client.post("/jog", json={"axis": "X", "distance": 15})).status_code == 200
    resp = await client.post("/jog", json={"axis": "X", "distance": 1})

    assert resp.status_code == 400
    assert (
        resp.json()["detail"]
        == "This jog would move the right syringe to X 46 mm, off the bed (−45 to 45 mm)"
    )
    assert fake.written[-1] != "G1 X1 F300"  # the refused jog never went out

    # The left syringe alone may go on
    await zero(client, ["left"])
    assert (await client.post("/jog", json={"axis": "X", "distance": 16})).status_code == 200


async def test_go_to_origin_checks_every_selected_syringe(client):
    far = [TWO_NOZZLES[0], {**TWO_NOZZLES[1], "offset_x": 50.0}]
    await save(client, far)
    printer_at()
    await zero(client, ["left", "right"])

    resp = await client.post("/jog/origin", json={"lower": False})

    assert resp.status_code == 400
    assert (
        resp.json()["detail"]
        == "This move would take the right syringe to X 50 mm, off the bed (−45 to 45 mm)"
    )
    await zero(client, ["left"])
    assert (await client.post("/jog/origin", json={"lower": False})).status_code == 200


# --- tool changes -------------------------------------------------------------------------

TWO_TOOLS = "T0\nG0 X0 Y0 Z0.3\nG1 X10 E0.5 F200\nT1\nG1 X10 Y10 E0.3 F200\n"


@pytest.mark.parametrize("nozzles", [["left"], ["left", "right"]])
async def test_file_with_tool_changes_is_refused(client, nozzles):
    resp = await upload_gcode(client, TWO_TOOLS, nozzles)

    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        "Line 4: T1 changes tool (the file starts with T0 on line 1). Multi-material files aren't "
        "supported: every selected syringe prints the same path. Slice the model for one extruder."
    )


async def test_file_with_tool_changes_sent_as_uploaded_gets_a_warning(client):
    resp = await upload_gcode(client, "G91\n" + TWO_TOOLS, ["left"], needs_changes=False)

    assert resp.status_code == 200, resp.text
    assert (
        "2 line(s) select a tool (T0, T1; first on line 2). Multi-material files aren't supported: "
        "every selected syringe prints every section, and the tool changes are sent unchanged"
    ) in resp.json()["warnings"]


async def test_stl_upload_accepts_only_stl(client):
    resp = await client.post("/upload", files={"file": ("model.3mf", b"PK", "application/octet-stream")})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Only .stl files are accepted"
