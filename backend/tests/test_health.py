async def test_health(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_cors_headers(client):
    resp = await client.options(
        "/",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
