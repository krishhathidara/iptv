"""A public host only exposes customer-facing pages and explicitly keyed operations."""

import pytest
from sqlalchemy import text

from app.config import settings
from app.database import engine


@pytest.mark.asyncio
async def test_public_tv_blocks_management_and_does_not_expose_admin_page(client, monkeypatch):
    monkeypatch.setattr(settings, "tv_public_mode", True)
    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key-for-tv-public-mode-123456")
    monkeypatch.setattr(settings, "subscriber_base_url", "https://tv.example")
    assert (await client.get("/")).status_code == 200
    assert "private TV link" in (await client.get("/")).text
    assert (await client.get("/health")).status_code == 200
    for route in ("/admin", "/docs", "/openapi.json", "/channels", "/sources",
                  "/playlist.m3u", "/static/admin.html", "/static/js/admin.js",
                  "/static/index.html", "/static/js/dashboard.js"):
        assert (await client.get(route)).status_code == 404, route
    assert (await client.post("/sources", json={})).status_code == 404
    assert (await client.post("/vod/import", json={})).status_code == 401
    assert (await client.post("/vod/import", headers={"X-Admin-Key": "wrong"}, json={})).status_code == 401
    assert (await client.get("/admin/subscribers/1")).status_code == 404
    assert (await client.post("/admin/subscribers/portal-check", json={})).status_code == 401
    assert (await client.options("/admin/subscribers/portal-check")).status_code == 404
    assert (await client.post("/admin/subscribers/1", json={})).status_code == 404
    assert (await client.post("/import-m3u", json={})).status_code == 401
    assert (await client.options("/admin/subscribers", headers={"Origin": "https://other.example", "Access-Control-Request-Method": "POST"})).status_code in {400, 404}
    assert (await client.post("/import-m3u", headers={"X-Admin-Key": "wrong"}, json={})).status_code == 401
    assert (await client.get("/admin/subscribers")).status_code == 401
    assert (await client.get("/admin/subscribers/portal-settings")).status_code == 401
    assert (await client.get("/static/js/watch.js")).status_code == 200
    assert (await client.get("/static/js/mag.js")).status_code == 200
    assert (await client.get("/static/vendor/hls.min.js")).status_code == 200

    headers = {"X-Admin-Key": settings.admin_api_key}
    created = await client.post("/admin/subscribers", headers=headers, json={
        "name": "Test TV", "mac_address": "AA:BB:CC:DD:EE:FF", "months": 1, "is_active": False,
    })
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["portal_url"].startswith("https://tv.example/watch/")
    assert row["mag_portal_url"].startswith("https://tv.example/stalker/")
    portal = row["portal_url"].removeprefix("https://tv.example")
    mag = row["mag_portal_url"].removeprefix("https://tv.example")
    assert (await client.get(portal)).status_code == 404
    assert (await client.get(mag)).status_code == 404
    imported = await client.post("/import-m3u", headers=headers, json={
        "raw_m3u": "#EXTM3U\n#EXTINF:-1,Test\nhttps://media.example/test.m3u8"
    })
    assert imported.status_code == 200
    movies = await client.post("/vod/import", headers=headers, json={"source_name": "authorized", "items": [
        {"title": "Test Movie", "media_type": "movie", "stream_url": "https://media.example/film.mp4"},
    ]})
    assert movies.status_code == 200 and movies.json()["inserted_count"] == 1
    assert (await client.post("/vod/import", headers=headers, json={"source_name": "invalid", "items": [
        {"title": "Webpage", "media_type": "movie", "stream_url": "https://media.example/watch"},
    ]})).status_code == 422
    activated = await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"is_active": True})
    assert activated.status_code == 200
    assert (await client.get(portal)).status_code == 200
    assert (await client.get(mag)).status_code == 200
    assert (await client.get("/admin/subscribers", headers=headers)).json()[0]["is_active"] is True
    assert "https://media.example/test.m3u8" in (await client.get(
        row["playlist_url"].removeprefix("https://tv.example"))).text
    assert "https://media.example/film.mp4" in (await client.get(
        row["playlist_url"].removeprefix("https://tv.example"))).text
    disabled = await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"is_active": False})
    assert disabled.status_code == 200
    assert (await client.get(portal)).status_code == 404
    assert (await client.get(mag)).status_code == 404


@pytest.mark.asyncio
async def test_health_requires_customer_table_and_does_not_expose_database_errors(client):
    assert (await client.get("/health")).status_code == 200
    async with engine.begin() as connection:
        await connection.execute(text("DROP TABLE subscribers"))
    unavailable = await client.get("/health")
    assert unavailable.status_code == 503
    assert unavailable.json() == {"detail": "Customer database unavailable"}
    assert "subscribers" not in unavailable.text


@pytest.mark.asyncio
async def test_missing_customer_column_fails_health_and_keyed_list(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key-for-tv-public-mode-123456")
    async with engine.begin() as connection:
        await connection.execute(text("ALTER TABLE subscribers DROP COLUMN notes"))
    health = await client.get("/health")
    assert health.status_code == 503
    assert health.json() == {"detail": "Customer database unavailable"}
    listed = await client.get("/admin/subscribers", headers={"X-Admin-Key": settings.admin_api_key})
    assert listed.status_code == 503
    assert listed.json() == {"detail": "Customer database unavailable"}
    assert "notes" not in health.text + listed.text