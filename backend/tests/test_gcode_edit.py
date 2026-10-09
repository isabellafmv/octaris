"""PUT /gcode/loaded: replacing the loaded print's lines from the G-code editor."""

import pytest

from backend.main import app
from backend.queue_worker import PrintStatus
from tests.serial_fakes import attach
from tests.test_history import FakeSerial, get_sessions
from tests.test_integration import upload_sample
from tests.test_print_gate import set_status

# A relative lab file, kept as uploaded (no start point)
LAB_FILE = "G91\nG1 X1 B-0.1 F200\nG1 Y1 B-0.1\n"


def loaded_lines() -> list[str]:
    return app.state.session.loaded.gcode.lines


def edited(line: int, text: str) -> str:
    """The loaded program with 1-based `line` replaced by `text`."""
    lines = list(loaded_lines())
    lines[line - 1] = text
    return "\n".join(lines)


async def put(client, body: str, **headers):
    return await client.put(
        "/gcode/loaded", content=body.encode(), headers={"Content-Type": "text/plain", **headers}
    )


async def upload_lab_file(client):
    resp = await client.post("/upload/gcode", files={"file": ("lab.gcode", LAB_FILE.encode(), "text/plain")})
    assert resp.status_code == 200, resp.text


def assert_rejected(resp, line: int | None, text: str) -> None:
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert text in body["detail"]
    assert body["errors"] == [{"line": line, "message": body["detail"]}]


# --- saving -------------------------------------------------------------------


async def test_saves_the_edit(client):
    await upload_sample(client)
    session = app.state.session
    before = session.loaded

    resp = await put(client, edited(5, "G1 F300 X10 B-0.5 ; faster"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["edited"] is True
    assert body["filename"] == "sample.gcode"
    assert body["lines_total"] == len(loaded_lines())
    assert body["preview_lines"][4] == "G1 F300 X10 B-0.5 ; faster"
    # Served back as saved, with the print's start point and mode kept
    assert (await client.get("/gcode/loaded")).text.split("\n")[4] == "G1 F300 X10 B-0.5 ; faster"
    loaded = session.loaded
    assert loaded.edited and loaded.mode == before.mode and loaded.source == "gcode"
    assert loaded.gcode.start_position == before.gcode.start_position
    # Re-simulated from the print's zero point
    assert len(loaded.gcode.state_after) == len(loaded.gcode.lines)
    assert loaded.gcode.state_after[4].feed == 300
    assert loaded.gcode.state_after[4].pos["X"] == 20


async def test_line_numbers_follow_the_editor(client):
    """CRLF endings split like LF; a form feed doesn't start a new line."""
    await upload_sample(client)
    body = edited(5, "G1 F200 X10 B-0.5 ;\x0c").replace("\n", "\r\n")

    assert (await put(client, body)).status_code == 200
    assert loaded_lines()[4] == "G1 F200 X10 B-0.5 ;\x0c"

    resp = await put(client, edited(7, "G90").replace("\n", "\r\n"))
    assert_rejected(resp, 7, "G90")


async def test_nothing_loaded(client):
    resp = await put(client, "G91\n")
    assert resp.status_code == 404


async def test_needs_the_token(client, monkeypatch):
    monkeypatch.setenv("OCTARIS_TOKEN", "s3cr3t")
    assert (await put(client, "G91\n")).status_code == 401


@pytest.mark.parametrize("status", [PrintStatus.PRINTING, PrintStatus.PAUSED])
async def test_refused_during_a_print(client, status):
    await upload_sample(client)
    original = list(loaded_lines())
    set_status(status)

    resp = await put(client, edited(5, "G1 F300 X10 B-0.5"))

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Stop the print before editing its G-code"
    assert loaded_lines() == original


async def test_allowed_after_a_stop_and_drops_the_checkpoint(client):
    await upload_sample(client)
    set_status(PrintStatus.STOPPED)
    worker = app.state.queue_worker
    reasons = []
    worker.invalidate_checkpoint = reasons.append

    assert (await put(client, edited(5, "G1 F300 X10 B-0.5"))).status_code == 200
    assert reasons == ["The loaded program was edited"]


# --- checks -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "text", "message"),
    [
        (7, "G90", "Line 7: G90 would make the following moves absolute"),
        (6, "G1 X1 E0.5", "Line 6: Unsubstituted E command"),
        (8, "G1 Y-10 B-0.5 F900", "Line 8: F value 900 exceeds 400"),
    ],
)
async def test_validation_errors_name_the_line(client, line, text, message):
    await upload_sample(client)
    original = list(loaded_lines())
    program_id = app.state.session.program_id

    resp = await put(client, edited(line, text))

    assert_rejected(resp, line, message)
    # Nothing changed
    assert loaded_lines() == original
    assert app.state.session.program_id == program_id
    assert not app.state.session.loaded.edited


async def test_must_start_relative(client):
    await upload_sample(client)
    resp = await put(client, edited(1, "M83"))
    assert_rejected(resp, 1, "must start with G91")


async def test_empty_program(client):
    await upload_sample(client)
    assert_rejected(await put(client, ""), None, "Empty G-code")


async def test_bed_limits_enforced(client):
    await upload_sample(client)
    # From X20 (after lines 4 and 5) back to X10, then 40 mm: X50, off the bed
    resp = await put(client, edited(14, "G1 F200 X40 B-0.5"))
    assert_rejected(resp, 14, "The print leaves the bed: line 14")
    assert "X to 50 mm, outside the bed (-30 to 30 mm)" in resp.json()["detail"]


async def test_syringe_travel_enforced(client):
    await upload_sample(client)
    # syringe_travel_mm is 40 by default
    resp = await put(client, edited(13, "G1 B-45 F400"))
    assert_rejected(resp, 13, "needs")
    assert "left plunger (B)" in resp.json()["detail"]


async def test_lab_file_gets_the_full_validation(client):
    """Uploaded with "Needs changes" on, a lab file is a processed print."""
    await upload_lab_file(client)

    assert_rejected(await put(client, "G91\nG90\nG1 X1\n"), 2, "Line 2: G90")
    assert_rejected(await put(client, "G91\nG1 X1 E1\n"), 2, "Line 2: Unsubstituted E")
    assert_rejected(await put(client, "M83\nG1 X1\nG91\n"), 1, "must start with G91")


async def test_processed_edit_with_g90_is_rejected(client):
    await upload_sample(client)
    before = list(loaded_lines())

    assert_rejected(await put(client, edited(6, "G90")), 6, "Line 6: G90")
    assert loaded_lines() == before


# --- files sent as uploaded ------------------------------------------------------

# Absolute (no G91), so it loads with a warning
AS_UPLOADED_FILE = "G1 X1 B-0.1 F200\nG1 Y1 B-0.1\n"


async def upload_as_uploaded(client, raw: str = AS_UPLOADED_FILE) -> list[str]:
    resp = await client.post(
        "/upload/gcode",
        params={"needs_changes": "false"},
        files={"file": ("plain.gcode", raw.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    assert app.state.session.loaded.as_uploaded
    return resp.json()["warnings"]


async def test_unchanged_as_uploaded_file_with_warnings_saves(client):
    warnings = await upload_as_uploaded(client)
    assert any("No G91" in w for w in warnings)

    resp = await put(client, AS_UPLOADED_FILE)

    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == warnings
    assert app.state.session.loaded.gcode.warnings == warnings


async def test_as_uploaded_edit_adding_g90_saves_with_a_warning(client):
    raw = "G91\nG1 X1 B-0.1 F200\nG1 Y1 B-0.1\n"
    assert await upload_as_uploaded(client, raw) == []

    resp = await put(client, "G91\nG1 X1 B-0.1 F200\nG90\nG1 Y1 B-0.1\n")

    assert resp.status_code == 200, resp.text
    assert resp.json()["warnings"] == ["Line 3: G90 switches the print back to absolute positioning"]
    assert loaded_lines()[2] == "G90"


async def test_as_uploaded_edit_without_moves_is_rejected(client):
    await upload_as_uploaded(client)

    assert_rejected(await put(client, "M83\n"), None, "no motion commands")
    assert_rejected(await put(client, "; nothing\n"), None, "Empty G-code")


async def test_lab_file_edit_rederives_its_axes(client):
    await upload_lab_file(client)
    gcode = app.state.session.loaded.gcode
    assert gcode.extrusion_axes == ("B",) and gcode.start_position is None

    resp = await put(client, "G91\nG1 X1 C-0.1 A0.2 F200\nG1 Y1 C-0.1\n")

    assert resp.status_code == 200, resp.text
    gcode = app.state.session.loaded.gcode
    assert gcode.extrusion_axes == ("C",)
    assert gcode.height_axes == ("A",)
    assert gcode.start_position is None


# --- program id / ETag ----------------------------------------------------------


async def test_etag_changes_on_edit(client):
    await upload_sample(client)
    first = await client.get("/gcode/loaded")
    etag = first.headers["etag"]
    assert first.headers["cache-control"] == "no-cache"

    resp = await put(client, edited(5, "G1 F300 X10 B-0.5"), **{"If-Match": etag})

    assert resp.status_code == 200, resp.text
    new_etag = resp.headers["etag"]
    assert new_etag != etag
    assert new_etag == f'"{resp.json()["program_id"]}"'
    second = await client.get("/gcode/loaded")
    assert second.headers["etag"] == new_etag
    assert second.text != first.text


async def test_if_none_match(client):
    await upload_sample(client)
    etag = (await client.get("/gcode/loaded")).headers["etag"]

    resp = await client.get("/gcode/loaded", headers={"If-None-Match": etag})
    assert resp.status_code == 304
    assert resp.headers["etag"] == etag

    await put(client, edited(5, "G1 F300 X10 B-0.5"))
    assert (await client.get("/gcode/loaded", headers={"If-None-Match": etag})).status_code == 200


async def test_etag_changes_on_upload(client):
    await upload_sample(client)
    etag = (await client.get("/gcode/loaded")).headers["etag"]
    await upload_sample(client)
    assert (await client.get("/gcode/loaded")).headers["etag"] != etag


async def test_if_match_refuses_a_stale_edit(client):
    """An editor opened on one program can't overwrite the next one."""
    await upload_sample(client)
    etag = (await client.get("/gcode/loaded")).headers["etag"]
    body = edited(5, "G1 F300 X10 B-0.5")
    await upload_lab_file(client)

    resp = await put(client, body, **{"If-Match": etag})

    assert resp.status_code == 412
    assert app.state.session.loaded.filename == "lab.gcode"


async def test_upload_during_the_check_wins(client, monkeypatch):
    """A file loaded while an edit is being checked isn't overwritten."""
    await upload_sample(client)
    session = app.state.session
    etag = (await client.get("/gcode/loaded")).headers["etag"]
    check = session._check_edit

    def check_then_upload(old, lines):
        result = check(old, lines)
        session.load_gcode("lab.gcode", LAB_FILE, "left")
        return result

    monkeypatch.setattr(session, "_check_edit", check_then_upload)
    resp = await put(client, edited(5, "G1 F300 X10 B-0.5"), **{"If-Match": etag})

    assert resp.status_code == 412
    assert session.loaded.filename == "lab.gcode"


# --- print history ----------------------------------------------------------------


@pytest.fixture
async def printer(client):
    fake = FakeSerial()
    attach(app.state.serial_manager, fake)
    app.state.session.zeroed = {"X", "Y", "Z", "A"}
    return fake


async def test_history_records_edited(client, printer):
    await upload_sample(client)
    await client.post("/print/start")
    await client.post("/print/stop")

    assert (await put(client, edited(5, "G1 F300 X10 B-0.5"))).status_code == 200
    resp = await client.post("/print/start")
    assert resp.status_code == 200, resp.text
    await client.post("/print/stop")

    edited_run, original_run = await get_sessions(client)
    assert edited_run["edited"] is True
    assert original_run["edited"] is False


async def test_start_refuses_a_program_edited_while_starting(client, printer):
    """start_print checks the program it read; an edit that lands while it
    reads the printer's position mustn't be swapped in unchecked."""
    await upload_sample(client)
    session = app.state.session
    send = session.serial.send

    async def send_and_edit(line, *args, **kwargs):
        reply = await send(line, *args, **kwargs)
        if line == "M114":
            await session.replace_loaded_lines(edited(5, "G1 F300 X10 B-0.5"))
        return reply

    session.serial.send = send_and_edit
    resp = await client.post("/print/start")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "The loaded program changed while the print was starting"
    assert app.state.queue_worker.status == PrintStatus.IDLE


async def test_cors_lets_the_renderer_use_the_etag(client):
    await upload_sample(client)
    origin = {"Origin": "http://localhost:5173"}
    preflight = await client.options(
        "/gcode/loaded",
        headers={
            **origin,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type,if-match,x-octaris-token",
        },
    )
    assert preflight.status_code == 200
    resp = await client.get("/gcode/loaded", headers=origin)
    assert "etag" in resp.headers["access-control-expose-headers"].lower()
