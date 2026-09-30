from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Subscriber
from app.services.mag_portal import issue_session, valid_session
from app.services.subscribers import add_months


@pytest.mark.asyncio
async def test_admin_key_required_and_private_playlist_lifecycle(client, monkeypatch):
    headers = {"X-Admin-Key": "long-admin-secret-for-subscriber-tests-1234567890"}
    payload = {"name": "Alice", "mac_address": "aa-bb-cc-dd-ee-ff", "months": 2, "notes": "Living room"}
    monkeypatch.setattr(settings, "admin_api_key", None)
    assert (await client.get("/admin/subscribers", headers=headers)).status_code == 401
    monkeypatch.setattr(settings, "admin_api_key", headers["X-Admin-Key"])
    assert (await client.get("/admin/subscribers")).status_code == 401
    assert (await client.post("/admin/subscribers", headers={"X-Admin-Key": "wrong"}, json=payload)).status_code == 401
    assert (await client.post("/admin/subscribers", headers=headers, json={**payload, "mac_address": "invalid"})).status_code == 422
    assert (await client.post("/admin/subscribers", headers=headers, json={**payload, "months": 0})).status_code == 422

    created = await client.post("/admin/subscribers", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    row = created.json()
    assert row["mac_address"] == "AA:BB:CC:DD:EE:FF"
    assert row["portal_url"].startswith("http://127.0.0.1:8000/watch/")
    assert datetime.fromisoformat(row["expires_at"]).utcoffset() == timedelta(0)
    assert row["playlist_url"].startswith("http://127.0.0.1:8000/subscribers/playlist/")
    assert row["mag_portal_url"].startswith("http://127.0.0.1:8000/stalker/")
    path = row["playlist_url"].removeprefix("http://127.0.0.1:8000")
    watch_path = row["portal_url"].removeprefix("http://127.0.0.1:8000")
    listed = await client.get("/admin/subscribers", headers=headers)
    assert len(listed.json()) == 1
    assert datetime.fromisoformat(listed.json()[0]["created_at"]).utcoffset() == timedelta(0)
    assert "playlist_url" not in listed.text and "portal_url" not in listed.text and path not in listed.text
    assert "mag_portal_url" not in listed.text
    assert (await client.get("/subscribers/playlist/unknown.m3u")).status_code == 404
    assert (await client.get("/watch/unknown")).status_code == 404
    watch = await client.get(watch_path)
    assert watch.status_code == 200 and "TV portal" in watch.text
    assert watch.headers["cache-control"] == "no-store"
    assert watch.headers["referrer-policy"] == "no-referrer"
    assert (await client.get(path)).text == "#EXTM3U\n"

    imported = await client.post("/import-m3u", json={"raw_m3u": "#EXTM3U\n#EXTINF:-1,Demo TV\nhttps://media.example/demo.m3u8"})
    assert imported.status_code == 200
    playlist = await client.get(path)
    assert playlist.status_code == 200 and "https://media.example/demo.m3u8" in playlist.text
    assert playlist.headers["cache-control"] == "no-store"
    movie = await client.post("/vod/import", json={"source_name": "authorized", "items": [
        {"title": "My Movie", "media_type": "movie", "stream_url": "https://media.example/movie.mp4"},
        {"title": "My Episode", "media_type": "tv", "stream_url": "https://media.example/episode.m3u8"},
    ]})
    assert movie.status_code == 200
    playlist = (await client.get(path)).text
    assert 'group-title="Movies",My Movie\nhttps://media.example/movie.mp4' in playlist
    assert 'group-title="TV Shows",My Episode\nhttps://media.example/episode.m3u8' in playlist
    assert "vidsrc.sh/embed" not in playlist
    assert (await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"is_active": False})).status_code == 200
    assert (await client.get(path)).status_code == 404
    assert (await client.get(watch_path)).status_code == 404
    assert (await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"is_active": True})).status_code == 200
    assert (await client.get(path)).status_code == 200
    assert (await client.get(watch_path)).status_code == 200

    async with AsyncSessionLocal() as session:
        subscriber = await session.get(Subscriber, row["id"])
        subscriber.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    assert (await client.get(path)).status_code == 404
    assert (await client.get(watch_path)).status_code == 404
    extended = await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"extend_months": 1})
    assert extended.status_code == 200
    assert datetime.fromisoformat(extended.json()["expires_at"]).replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    assert (await client.get(path)).status_code == 200
    assert (await client.get(watch_path)).status_code == 200
    rotated = await client.post(f"/admin/subscribers/{row['id']}/rotate", headers=headers)
    assert rotated.status_code == 200
    new_path = rotated.json()["playlist_url"].removeprefix("http://127.0.0.1:8000")
    new_watch_path = rotated.json()["portal_url"].removeprefix("http://127.0.0.1:8000")
    assert new_path != path
    assert (await client.get(path)).status_code == 404
    assert (await client.get(watch_path)).status_code == 404
    assert (await client.get(new_path)).status_code == 200
    assert (await client.get(new_watch_path)).status_code == 200
    assert (await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={})).status_code == 422
    assert (await client.post("/admin/subscribers/999/rotate", headers=headers)).status_code == 404


def test_calendar_month_expiry_clamps_month_end():
    assert add_months(datetime(2025, 1, 31, tzinfo=timezone.utc), 1) == datetime(2025, 2, 28, tzinfo=timezone.utc)
    assert add_months(datetime(2024, 1, 31, tzinfo=timezone.utc), 1) == datetime(2024, 2, 29, tzinfo=timezone.utc)


def test_mag_session_expires_and_is_bound_to_current_account_token_and_mac(monkeypatch):
    from app.services import mag_portal

    subscriber = Subscriber(token_hash="a" * 64)
    mac = "AA:BB:CC:DD:EE:FF"
    monkeypatch.setattr(mag_portal.time, "time", lambda: 2_000_000_000)
    session = issue_session(subscriber, mac)
    assert valid_session(subscriber, mac, session)
    assert not valid_session(subscriber, "11:22:33:44:55:66", session)
    subscriber.token_hash = "b" * 64
    assert not valid_session(subscriber, mac, session)
    subscriber.token_hash = "a" * 64
    monkeypatch.setattr(mag_portal.time, "time", lambda: 2_000_003_600)
    assert not valid_session(subscriber, mac, session)
    assert not valid_session(subscriber, mac, "broken-session")


@pytest.mark.asyncio
async def test_inactive_mac_record_requires_activation_but_does_not_identify_device(client, monkeypatch):
    headers = {"X-Admin-Key": "long-admin-secret-for-subscriber-tests-1234567890"}
    monkeypatch.setattr(settings, "admin_api_key", headers["X-Admin-Key"])
    created = await client.post("/admin/subscribers", headers=headers, json={
        "name": "Living room", "mac_address": "aa-bb-cc-dd-ee-ff", "months": 1, "is_active": False,
    })
    assert created.status_code == 201
    row = created.json()
    assert row["is_active"] is False and row["mac_address"] == "AA:BB:CC:DD:EE:FF"
    playlist = row["playlist_url"].removeprefix("http://127.0.0.1:8000")
    portal = row["portal_url"].removeprefix("http://127.0.0.1:8000")
    assert (await client.get(playlist)).status_code == 404
    assert (await client.get(portal)).status_code == 404
    changed = await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"mac_address": "11-22-33-44-55-66"})
    assert changed.status_code == 200 and changed.json()["mac_address"] == "11:22:33:44:55:66"
    assert (await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"mac_address": "invalid"})).status_code == 422
    activated = await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"is_active": True})
    assert activated.status_code == 200
    assert (await client.get(playlist)).status_code == 200
    assert (await client.get(portal)).status_code == 200
    cleared = await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"mac_address": None})
    assert cleared.status_code == 200 and cleared.json()["mac_address"] is None
    assert (await client.get(playlist)).status_code == 200
    assert (await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={})).status_code == 422


@pytest.mark.asyncio
async def test_admin_portal_is_served_without_exposing_the_key(client):
    page = await client.get("/admin")
    assert page.status_code == 200 and 'id="subscriberForm"' in page.text
    assert "A reported MAC can be spoofed" in page.text
    assert 'id="subscriberAction"' in page.text
    assert 'id="actionMonthsPreset"' in page.text
    assert 'id="subscriberCustomMonths"' in page.text
    assert 'id="subscriberSearch"' in page.text
    assert 'id="subscriberActive"' in page.text
    assert "Remote: use" in page.text
    for asset in ("/static/js/admin.js", "/static/css/admin.css"):
        assert (await client.get(asset)).status_code == 200
    script = (await client.get("/static/js/admin.js")).text
    assert '$("subscriberAction").addEventListener("submit"' in script
    assert 'prompt(' not in script and 'confirm(' not in script
    assert '"ArrowRight"' in script and '"BrowserBack"' in script
    assert 'document.activeElement.tagName === "SELECT"' in script
    stylesheet = (await client.get("/static/css/admin.css")).text
    assert ":focus-visible" in stylesheet and "min-height: 58px" in stylesheet
    for asset in ("/static/js/watch.js", "/static/css/watch.css"):
        assert (await client.get(asset)).status_code == 200


@pytest.mark.asyncio
async def test_mag_portal_handshake_channels_playback_and_revocation(client, monkeypatch):
    headers = {"X-Admin-Key": "long-admin-secret-for-subscriber-tests-1234567890"}
    monkeypatch.setattr(settings, "admin_api_key", headers["X-Admin-Key"])
    created = await client.post("/admin/subscribers", headers=headers, json={
        "name": "MAG TV", "mac_address": "aa-bb-cc-dd-ee-ff", "months": 1, "is_active": False,
    })
    row = created.json()
    portal = row["mag_portal_url"].removeprefix("http://127.0.0.1:8000")
    api = portal.replace("/c/index.html", "/server/load.php")
    assert (await client.get(portal)).status_code == 404
    assert (await client.get(api, params={"type": "stb", "action": "handshake", "mac": "AA:BB:CC:DD:EE:FF"})).status_code == 404
    await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"is_active": True})
    assert (await client.get(portal)).status_code == 200
    assert (await client.get(portal)).headers["cache-control"] == "no-store"
    assert (await client.get("/static/js/mag.js")).status_code == 200
    assert (await client.get("/static/css/mag.css")).status_code == 200
    mag_script = (await client.get("/static/js/mag.js")).text
    assert 'xhr.setRequestHeader("X-Device-Mac", mac)' in mag_script
    assert '"get_ordered_list"' in mag_script and 'id="loadMore"' in (await client.get(portal)).text
    assert 'stb.Play(cmd)' in mag_script and 'stb.SetPIG(0, 150,' in mag_script
    assert (await client.get(api, params={"type": "stb", "action": "handshake", "mac": "invalid"})).status_code == 403
    assert (await client.get(api, params={"type": "stb", "action": "handshake", "mac": "00:00:00:00:00:00"})).status_code == 403
    assert (await client.get(api, params={"type": "stb", "action": "handshake"}, headers={"X-Device-Mac": "AA:BB:CC:DD:EE:FF"})).status_code == 200
    handshake = await client.get(api, params={"type": "stb", "action": "handshake", "mac": "aa:bb:cc:dd:ee:ff"})
    assert handshake.status_code == 200
    bearer = handshake.json()["js"]["token"]
    mac = {"mac": "AA:BB:CC:DD:EE:FF"}
    auth = {"Authorization": f"Bearer {bearer}"}
    assert (await client.get(api, params={**mac, "type": "itv", "action": "get_all_channels"})).status_code == 403
    assert (await client.get(api, params={"mac": "11:22:33:44:55:66", "type": "itv", "action": "get_all_channels"}, headers=auth)).status_code == 403
    assert (await client.post(api, data={**mac, "type": "stb", "action": "get_profile"}, headers=auth)).json()["js"]["mac"] == mac["mac"]
    imported = await client.post("/import-m3u", json={"raw_m3u": "#EXTM3U\n#EXTINF:-1,Test MAG\nhttps://media.example/live.m3u8"})
    assert imported.status_code == 200
    result = await client.get(api, params={**mac, "type": "itv", "action": "get_all_channels"}, headers=auth)
    assert result.status_code == 200
    channel = next(item for item in result.json()["js"]["data"] if item["name"] == "Test MAG")
    assert channel["cmd"].startswith("ffmpeg channel_")
    link = await client.get(api, params={**mac, "type": "itv", "action": "create_link", "cmd": channel["cmd"]}, headers=auth)
    assert link.json()["js"]["cmd"] == "ffmpeg https://media.example/live.m3u8"
    assert (await client.get(api, params={**mac, "type": "itv", "action": "create_link", "cmd": "ffmpeg https://evil.example"}, headers=auth)).status_code == 400
    assert (await client.get(api, params={**mac, "type": "itv", "action": "get_ordered_list", "p": "invalid"}, headers=auth)).status_code == 400
    assert (await client.get(api, params={**mac, "type": "itv", "action": "get_genres"}, headers=auth)).status_code == 200
    page = await client.get(api, params={**mac, "type": "itv", "action": "get_ordered_list", "p": "1"}, headers=auth)
    assert page.status_code == 200 and page.json()["js"]["total_items"] >= 1
    rotated = (await client.post(f"/admin/subscribers/{row['id']}/rotate", headers=headers)).json()
    assert rotated["mag_portal_url"] != row["mag_portal_url"]
    assert (await client.get(portal)).status_code == 404
    assert (await client.get(api, params={**mac, "type": "itv", "action": "get_all_channels"}, headers=auth)).status_code == 404
    new_api = rotated["mag_portal_url"].removeprefix("http://127.0.0.1:8000").replace("/c/index.html", "/server/load.php")
    assert (await client.get(new_api, params={**mac, "type": "itv", "action": "get_all_channels"}, headers=auth)).status_code == 403
    assert (await client.get(new_api, params={**mac, "type": "stb", "action": "handshake"})).status_code == 200
    await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"mac_address": "11:22:33:44:55:66"})
    assert (await client.get(new_api, params={**mac, "type": "stb", "action": "handshake"})).status_code == 403
    assert (await client.get(new_api, params={"mac": "11:22:33:44:55:66", "type": "stb", "action": "handshake"})).status_code == 200
    async with AsyncSessionLocal() as session:
        subscriber = await session.get(Subscriber, row["id"])
        subscriber.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    assert (await client.get(new_api, params={"mac": "11:22:33:44:55:66", "type": "stb", "action": "handshake"})).status_code == 404
    await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"extend_months": 1})
    await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"is_active": False})
    assert (await client.get(new_api, params={**mac, "type": "stb", "action": "handshake"})).status_code == 404