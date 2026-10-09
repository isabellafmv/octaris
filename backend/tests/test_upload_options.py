""".txt uploads, the "needs changes" choice for G-code uploads, and the
print speed / feed cap settings.

A file that doesn't need changes is sent exactly as uploaded and only
checked; the runtime (position tracking, bed and syringe limits, e-stop and
resume) must still work on it, so a lab-style G91 file is printed end to end
against the virtual printer.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend import slicer
from backend.config import Config
from backend.gcode_processor import (
    MAX_FEED,
    GcodeValidationError,
    check_as_uploaded,
    clamp_feed_rates,
    parse,
    process_gcode,
    render,
    validate,
)
from backend.main import app
from backend.queue_worker import PrintStatus
from tests.test_relative_printing import (
    LAB_FILE,
    RAW_SAMPLE,
    jog,
    lab_end,
    print_to_the_end,
    stop_half_way,
    wait_for,
)
from tests.test_relative_printing import lab_printer as lab_printer  # noqa: F401 - fixtures
from tests.test_relative_printing import printer as printer  # noqa: F401

FIXTURES = Path(__file__).parent / "fixtures"


async def upload_gcode(client, raw: str, filename: str = "print.gcode", **params):
    return await client.post(
        "/upload/gcode",
        params={"syringe_mode": "left", **params},
        files={"file": (filename, raw.encode(), "text/plain")},
    )


# --- .txt files -------------------------------------------------------------------


@pytest.mark.parametrize("needs_changes", [True, False])
async def test_txt_upload_is_treated_like_gcode(client, needs_changes):
    raw = RAW_SAMPLE if needs_changes else LAB_FILE
    as_txt = await upload_gcode(client, raw, "print.TXT", needs_changes=needs_changes)
    txt_lines = app.state.session.loaded.gcode.lines
    as_gcode = await upload_gcode(client, raw, "print.gcode", needs_changes=needs_changes)

    assert as_txt.status_code == as_gcode.status_code == 200, as_txt.text
    assert txt_lines == app.state.session.loaded.gcode.lines
    assert {**as_txt.json(), "filename": None} == {**as_gcode.json(), "filename": None}
    assert as_txt.json()["filename"] == "print.TXT"


async def test_other_extensions_are_refused(client):
    resp = await upload_gcode(client, LAB_FILE, "print.csv")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Only .gcode and .txt files are accepted"


# --- needs changes / doesn't need changes ------------------------------------------


async def test_needs_changes_post_processes_as_before(client):
    resp = await upload_gcode(client, RAW_SAMPLE, needs_changes=True)
    assert resp.status_code == 200
    assert app.state.session.loaded.gcode.lines == process_gcode(RAW_SAMPLE, "left").lines
    assert resp.json()["warnings"] == []


async def test_needs_changes_is_the_api_default(client):
    assert (await upload_gcode(client, RAW_SAMPLE)).status_code == 200
    assert app.state.session.loaded.gcode.lines == process_gcode(RAW_SAMPLE, "left").lines


async def test_unchanged_file_is_sent_as_uploaded_with_warnings(client):
    # The absolute Cura-style sample: E values, no G91, nothing converted
    raw = RAW_SAMPLE
    resp = await upload_gcode(client, raw, needs_changes=False)
    assert resp.status_code == 200, resp.text

    loaded = app.state.session.loaded.gcode
    assert loaded.lines == raw.splitlines()
    assert loaded.start_position is None
    body = resp.json()
    assert body["lines_total"] == len(raw.splitlines())
    assert body["feed_log_entries"] == 0
    warnings = body["warnings"]
    assert len(warnings) == 3
    assert warnings[0].startswith("3 line(s) set a feed rate above the 400 mm/min limit")
    assert "up to F1200" in warnings[0]
    assert "use E" in warnings[1]
    assert "No G91" in warnings[2]


async def test_clean_lab_file_has_no_warnings(client):
    resp = await upload_gcode(client, LAB_FILE, needs_changes=False)
    assert resp.status_code == 200
    assert resp.json()["warnings"] == []
    assert app.state.session.loaded.gcode.lines == LAB_FILE.splitlines()


@pytest.mark.parametrize(
    "raw, detail",
    [
        ("", "Empty G-code"),
        ("; only a comment\n\n", "Empty G-code"),
        ("G91\nM400\nG4 P100\n", "no motion commands"),
    ],
)
async def test_unchanged_file_without_moves_is_refused(client, raw, detail):
    resp = await upload_gcode(client, raw, needs_changes=False)
    assert resp.status_code == 422
    assert detail in resp.json()["detail"]
    assert app.state.session.loaded is None


async def test_unchanged_file_leaving_the_bed_is_refused(client):
    # Absolute, so its coordinates can be checked before it is started
    resp = await upload_gcode(client, "G90\nG1 X40 Y0 F300\n", needs_changes=False)
    assert resp.status_code == 422
    assert "leaves the bed" in resp.json()["detail"]


def test_check_as_uploaded_warnings():
    assert check_as_uploaded(parse(LAB_FILE.splitlines())) == []
    [warning] = check_as_uploaded(parse(["G91", "G1 X1 F300", "G90", "G1 X0"]))
    assert warning == "Line 3: G90 switches the print back to absolute positioning"
    [warning] = check_as_uploaded(parse(["G91", "G1 X1 F500", "G1 X1 F450"]))
    assert warning.startswith(
        "2 line(s) set a feed rate above the 400 mm/min limit (first on line 2, up to F500)"
    )
    # The cap is a parameter (config.max_feed_mm_min)
    assert check_as_uploaded(parse(["G91", "G1 X1 F500"]), max_feed=600) == []
    with pytest.raises(GcodeValidationError, match="Empty"):
        check_as_uploaded([])


# --- an unchanged lab file through the runtime ---------------------------------------


def test_unchanged_lab_file_state_simulation(client):
    gcode = app.state.session.load_gcode("lab.gcode", LAB_FILE, "left", needs_changes=False)
    assert len(gcode.state_before) == len(gcode.state_after) == len(gcode.lines)
    assert gcode.extrusion_axes == ("B",) and gcode.height_axes == ("Z",)
    # Starts wherever the head is: nothing known until the printer is read
    assert all(value is None for value in gcode.state_before[0].pos.values())
    assert all(state.relative for state in gcode.state_after[1:])


async def test_unchanged_lab_file_prints_from_where_the_head_is(client, lab_printer):  # noqa: F811
    printer = lab_printer
    assert (await upload_gcode(client, LAB_FILE, needs_changes=False)).status_code == 200
    await print_to_the_end(client)
    assert printer.executed[:3] == ["M114", "M84 S0", "M110 N0"]  # no travel to a zero point
    assert printer.position == pytest.approx(lab_end())


async def test_unchanged_lab_file_estop_and_resume(client, lab_printer):  # noqa: F811
    printer = lab_printer
    assert (await upload_gcode(client, LAB_FILE, needs_changes=False)).status_code == 200
    worker = app.state.queue_worker
    await stop_half_way(client, printer, "X-6 B-0.65")
    # Tracked from the M114 at the start, as for a processed file
    assert worker.checkpoint.position == pytest.approx(
        {"X": 15, "Y": -4, "Z": 0.55, "A": 0, "B": -4.425, "C": 0}, abs=0.006
    )

    await jog(client, "Z", 2)
    assert (await client.post("/print/resume")).status_code == 200
    await wait_for(lambda: worker.status == PrintStatus.COMPLETED)
    assert "G90" not in printer.executed
    assert printer.position == pytest.approx(lab_end(), abs=0.006)


async def test_unchanged_lab_file_bed_limit_checked_at_start(client, printer):  # noqa: F811
    # Fine where it is uploaded (position unknown), off the bed from X27
    assert (await upload_gcode(client, LAB_FILE, needs_changes=False)).status_code == 200
    printer.move_externally(X=27)
    resp = await client.post("/print/start")
    assert resp.status_code == 400
    assert "leaves the bed" in resp.json()["detail"]
    assert "X to 33 mm" in resp.json()["detail"]


async def test_unchanged_lab_file_syringe_travel_checked_at_start(client, printer):  # noqa: F811
    assert (await upload_gcode(client, LAB_FILE, needs_changes=False)).status_code == 200
    app.state.config.syringe_travel_mm = 4.0  # the file pushes B 4.2 mm
    resp = await client.post("/print/start")
    assert resp.status_code == 400
    assert "4.2 mm of left plunger (B) travel" in resp.json()["detail"]


# --- the feed cap ------------------------------------------------------------------------


def test_feed_cap_is_a_config_value(tmp_path):
    assert Config().max_feed_mm_min == MAX_FEED == 400
    path = tmp_path / "config.json"
    path.write_text('{"max_feed_mm_min": 250}')
    from backend.config import load_config

    assert load_config(path).max_feed_mm_min == 250
    with pytest.raises(ValueError):
        Config(max_feed_mm_min=0)


def test_clamp_and_validate_take_the_cap():
    cmds, log = clamp_feed_rates(parse(["G1 X1 F600", "G1 X2 F250"]), max_f=300)
    assert render(cmds) == ["G1 X1 F300", "G1 X2 F250"]
    assert log == ["Line 1: F600 clamped to F300"]
    validate(parse(["G91", "G1 X1 F300"]), max_feed=300)
    with pytest.raises(GcodeValidationError, match="exceeds 300"):
        validate(parse(["G91", "G1 X1 F301"]), max_feed=300)


async def test_upload_clamps_to_the_configured_cap(client):
    app.state.config.max_feed_mm_min = 150
    resp = await upload_gcode(client, RAW_SAMPLE, needs_changes=True)
    assert resp.status_code == 200
    assert resp.json()["feed_log_entries"] > 0
    feeds = [w.value for c in parse(app.state.session.loaded.gcode.lines) for w in c.words if w.letter == "F"]
    assert max(feeds) == 150

    resp = await upload_gcode(client, LAB_FILE, needs_changes=False)  # F150/F300 moves
    assert "above the 150 mm/min limit" in resp.json()["warnings"][0]


# --- print speed --------------------------------------------------------------------------


async def upload_stl(client, tmp_path, **params):
    sliced = AsyncMock(return_value=process_gcode(RAW_SAMPLE, "left"))
    with patch("backend.session.slice_model", sliced), patch("backend.session.DATA_DIR", tmp_path):
        resp = await client.post(
            "/upload",
            params={"syringe_mode": "left", **params},
            files={"file": ("cube.stl", b"solid cube", "application/octet-stream")},
        )
    return resp, sliced


async def test_print_speed_is_passed_to_the_slicer(client, tmp_path):
    resp, sliced = await upload_stl(client, tmp_path, print_speed=4)
    assert resp.status_code == 200, resp.text
    kwargs = sliced.await_args.kwargs
    assert kwargs["print_speed"] == 4
    assert kwargs["max_feed"] == 400


@pytest.mark.parametrize("speed, ok", [(6.5, True), (6.67, False), (10, False)])
async def test_print_speed_is_checked_against_the_cap(client, tmp_path, speed, ok):
    resp, sliced = await upload_stl(client, tmp_path, print_speed=speed)
    if ok:
        assert resp.status_code == 200
    else:
        assert resp.status_code == 400
        assert "above the limit of 6.66667 mm/s (400 mm/min" in resp.json()["detail"]
        sliced.assert_not_awaited()


async def test_print_speed_follows_the_configured_cap(client, tmp_path):
    app.state.config.max_feed_mm_min = 600
    resp, _ = await upload_stl(client, tmp_path, print_speed=10)
    assert resp.status_code == 200


async def test_print_speed_must_be_positive(client, tmp_path):
    resp, _ = await upload_stl(client, tmp_path, print_speed=0)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "print_speed must be positive"


@pytest.mark.parametrize("suffix, dual", [(".stl", False), (".3mf", True)])
async def test_slicer_sets_cura_speeds(tmp_path, monkeypatch, suffix, dual):
    model = tmp_path / f"cube{suffix}"
    model.write_bytes(b"")
    monkeypatch.setattr(slicer, "_check_stl_dimensions", lambda path: None)
    monkeypatch.setattr(slicer, "_check_3mf", lambda path: None)
    monkeypatch.setattr(slicer, "_find_cura_engine", lambda: "CuraEngine")

    async def fake_exec(*cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[cmd.index("-o") + 1]).write_text(RAW_SAMPLE)
        proc = MagicMock(returncode=0, pid=1)
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(slicer.asyncio, "create_subprocess_exec", fake_exec)
    await slicer.slice_model(model, "both" if dual else "left", print_speed=3.5, max_feed=300)

    [cmd] = calls
    settings = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-s"]
    for name in ("speed_print", "speed_travel", "speed_wall_0", "speed_infill", "speed_layer_0"):
        assert settings.count(f"{name}=3.5") == (2 if dual else 1)

    calls.clear()
    await slicer.slice_model(model, "left")
    assert not any(arg.startswith("speed_") for arg in calls[0])


async def test_print_speed_saved_in_history(client, printer, tmp_path):  # noqa: F811
    resp, _ = await upload_stl(client, tmp_path, print_speed=4.5)
    assert resp.status_code == 200
    await print_to_the_end(client)
    [session] = (await client.get("/history")).json()["sessions"]
    assert session["print_speed"] == 4.5
