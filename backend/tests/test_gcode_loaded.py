"""GET /gcode/loaded: the loaded print's processed G-code, for the client's preview."""

from backend.main import app
from tests.test_integration import RAW_SAMPLE, upload_sample


async def test_nothing_loaded(client):
    resp = await client.get("/gcode/loaded")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "No print loaded"}


async def test_returns_the_processed_lines(client):
    await upload_sample(client)

    resp = await client.get("/gcode/loaded")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    lines = app.state.session.loaded.gcode.lines
    assert resp.text.split("\n") == lines
    assert resp.text != RAW_SAMPLE  # processed, not the uploaded file


async def test_follows_the_latest_upload(client):
    await upload_sample(client)
    first = (await client.get("/gcode/loaded")).text
    resp = await client.post(
        "/upload/gcode",
        files={"file": ("small.gcode", b"G90\nG1 X1 Y1 Z0.3 F300\nG1 X2 Y1 E0.1\n", "text/plain")},
    )
    assert resp.status_code == 200, resp.text

    second = (await client.get("/gcode/loaded")).text
    assert second != first
    assert second.split("\n") == app.state.session.loaded.gcode.lines


async def test_needs_the_token(client, monkeypatch):
    monkeypatch.setenv("OCTARIS_TOKEN", "s3cr3t")
    assert (await client.get("/gcode/loaded")).status_code == 401
    assert (await client.get("/gcode/loaded", headers={"X-Octaris-Token": "s3cr3t"})).status_code == 404
