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
    profile = (await client.post(api, data={**mac, "type": "stb", "action": "get_profile"}, headers=auth)).json()["js"]
    assert profile["mac"] == mac["mac"] and profile["token"] == bearer
    assert profile["status"] == 0 and profile["blocked"] == "0"
    imported = await client.post("/import-m3u", json={"raw_m3u": "#EXTM3U\n#EXTINF:-1,Test MAG\nhttps://media.example/live.m3u8"})
    assert imported.status_code == 200
    result = await client.get(api, params={**mac, "type": "itv", "action": "get_all_channels"}, headers=auth)
    assert result.status_code == 200
    channel = next(item for item in result.json()["js"]["data"] if item["name"] == "Test MAG")
    assert channel["cmd"].startswith("ffmpeg channel_")
    link = await client.get(api, params={**mac, "type": "itv", "action": "create_link", "cmd": channel["cmd"]}, headers=auth)
    assert link.json()["js"]["cmd"] == "ffmpeg https://media.example/live.m3u8"
    movie_import = await client.post("/vod/import", json={"source_name": "authorized", "items": [
        {"title": "MAG Movie", "media_type": "movie", "stream_url": "https://media.example/movie.mp4"},
        {"title": "MAG Episode", "media_type": "tv", "stream_url": "https://media.example/episode.m3u8"},
    ]})
    assert movie_import.status_code == 200
    movies = await client.get(api, params={**mac, "type": "vod", "action": "get_ordered_list"}, headers=auth)
    assert movies.status_code == 200
    assert movies.json()["js"]["total_items"] == 1
    movie = movies.json()["js"]["data"][0]
    assert movie["name"] == "MAG Movie"
    assert (await client.get(api, params={**mac, "type": "vod", "action": "get_categories"}, headers=auth)).status_code == 200
    assert (await client.get(api, params={**mac, "type": "vod", "action": "get_ordered_list"})).status_code == 403
    assert (await client.get(api, params={**mac, "type": "vod", "action": "create_link", "cmd": movie["cmd"]}, headers=auth)).json()["js"]["cmd"] == "ffmpeg https://media.example/movie.mp4"
    episodes = await client.get(api, params={**mac, "type": "series", "action": "get_ordered_list"}, headers=auth)
    assert episodes.status_code == 200 and episodes.json()["js"]["total_items"] == 1
    episode = episodes.json()["js"]["data"][0]
    assert episode["name"] == "MAG Episode"
    assert (await client.get(api, params={**mac, "type": "series", "action": "get_categories"}, headers=auth)).json() == {
        "js": [{"id": "*", "title": "TV episodes", "alias": "all"}]}
    assert (await client.get(api, params={**mac, "type": "series", "action": "create_link", "cmd": episode["cmd"]}, headers=auth)).json()["js"]["cmd"] == "ffmpeg https://media.example/episode.m3u8"
    assert (await client.get(api, params={**mac, "type": "series", "action": "create_link", "cmd": movie["cmd"]}, headers=auth)).status_code == 404
    assert (await client.get(api, params={**mac, "type": "vod", "action": "create_link", "cmd": episode["cmd"]}, headers=auth)).status_code == 404
    assert (await client.get(api, params={**mac, "type": "series", "action": "create_link", "cmd": "ffmpeg https://evil.example"}, headers=auth)).status_code == 400
    assert (await client.get(api, params={**mac, "type": "series", "action": "get_ordered_list"})).status_code == 403
    assert (await client.get(api, params={**mac, "type": "vod", "action": "create_link", "cmd": "ffmpeg vod_999999"}, headers=auth)).status_code == 404
    assert (await client.get(api, params={**mac, "type": "vod", "action": "create_link", "cmd": "ffmpeg https://evil.example"}, headers=auth)).status_code == 400
    assert (await client.get(api, params={**mac, "type": "vod", "action": "get_ordered_list", "p": "invalid"}, headers=auth)).status_code == 400
    assert (await client.get(api, params={**mac, "type": "itv", "action": "create_link", "cmd": "ffmpeg https://evil.example"}, headers=auth)).status_code == 400
    assert (await client.get(api, params={**mac, "type": "itv", "action": "get_ordered_list", "p": "invalid"}, headers=auth)).status_code == 400
    assert (await client.get(api, params={**mac, "type": "itv", "action": "get_genres"}, headers=auth)).status_code == 200
    page = await client.get(api, params={**mac, "type": "itv", "action": "get_ordered_list", "p": "1"}, headers=auth)
    assert page.status_code == 200 and page.json()["js"]["total_items"] >= 1
    rotated = (await client.post(f"/admin/subscribers/{row['id']}/rotate", headers=headers)).json()
    assert rotated["mag_portal_url"] != row["mag_portal_url"]
    assert (await client.get(portal)).status_code == 404
    assert (await client.get(api, params={**mac, "type": "itv", "action": "get_all_channels"}, headers=auth)).status_code == 404
    assert (await client.get(api, params={**mac, "type": "vod", "action": "create_link", "cmd": movie["cmd"]}, headers=auth)).status_code == 404
    new_api = rotated["mag_portal_url"].removeprefix("http://127.0.0.1:8000").replace("/c/index.html", "/server/load.php")
    assert (await client.get(new_api, params={**mac, "type": "itv", "action": "get_all_channels"}, headers=auth)).status_code == 403
    assert (await client.get(new_api, params={**mac, "type": "vod", "action": "create_link", "cmd": movie["cmd"]}, headers=auth)).status_code == 403
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
    assert (await client.get(new_api, params={**mac, "type": "vod", "action": "create_link", "cmd": movie["cmd"]}, headers=auth)).status_code == 404


@pytest.mark.asyncio
async def test_opt_in_mac_stalker_server_uses_registered_active_account(client, monkeypatch, caplog):
    headers = {"X-Admin-Key": "long-admin-secret-for-subscriber-tests-1234567890"}
    monkeypatch.setattr(settings, "admin_api_key", headers["X-Admin-Key"])
    monkeypatch.setattr(settings, "tv_public_mode", True)
    monkeypatch.setattr(settings, "enable_mac_stalker_portal", False)
    mac = "AA:BB:CC:DD:EE:FF"
    query = {"type": "stb", "action": "handshake", "mac": mac}
    paths = ("/portal.php", "/server/load.php", "/server/portal.php",
             "/stalker_portal/portal.php", "/stalker_portal/server/load.php",
             "/stalker_portal/server/portal.php")
    for path in paths:
        assert (await client.get(path, params=query)).status_code == 404
    for path in ("/c/", "/c/index.html", "/stalker_portal/c/index.html"):
        assert (await client.get(path)).status_code == 404
    monkeypatch.setattr(settings, "enable_mac_stalker_portal", True)
    for path in ("/c/", "/c/index.html", "/stalker_portal/c/", "/stalker_portal/c/index.html"):
        page = await client.get(path)
        assert page.status_code == 200 and "mag.js" in page.text
        assert page.headers["cache-control"] == "no-store"
    assert (await client.get("/c/server/load.php", params=query)).status_code == 404
    for path in paths:
        assert (await client.get(path, params=query)).status_code == 403
    row = (await client.post("/admin/subscribers", headers=headers, json={
        "name": "iPhone", "mac_address": mac, "months": 1, "is_active": False,
    })).json()
    assert (await client.get(paths[0], params=query)).status_code == 403
    await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"is_active": True})
    assert (await client.get("/c/index.html")).status_code == 200
    assert (await client.get("/c/index.html", params={"mac": mac})).status_code == 200
    assert (await client.get(paths[0], params={**query, "mac": "invalid"})).status_code == 403
    assert (await client.get(paths[0], params=query, headers={"Cookie": "mac=11:22:33:44:55:66"})).status_code == 403
    handshake = await client.get(paths[0], params={"type": "stb", "action": "handshake"}, headers={"Cookie": "mac=AA%3ABB%3ACC%3ADD%3AEE%3AFF"})
    assert handshake.status_code == 200
    bearer = handshake.json()["js"]["token"]
    assert (await client.get(paths[-1], params={"type": "stb", "action": "handshake", "mac": mac,
                                                  "token": "", "JsHttpRequest": "1-xml"})).status_code == 200
    assert (await client.get(paths[1], params={"type": "itv", "action": "get_all_channels", "mac": mac})).status_code == 403
    auth = {"Authorization": f"Bearer {bearer}"}
    profile = (await client.get(paths[-1], params={"type": "stb", "action": "get_profile", "mac": mac,
                                                   "token": bearer})).json()["js"]
    assert profile["mac"] == mac and profile["token"] == bearer
    assert profile["status"] == 0 and profile["blocked"] == "0"
    assert "MAG request status=200 type=stb action=get_profile scope=shared endpoint=/stalker_portal/server/portal.php method=GET" in caplog.text
    assert mac not in caplog.text and bearer not in caplog.text
    assert (await client.post(paths[2], data={"type": "stb", "action": "get_profile", "mac": mac,
                                                  "token": bearer})).status_code == 200
    assert "MAG request status=200 type=stb action=get_profile scope=shared endpoint=/server/portal.php method=POST" in caplog.text
    assert (await client.get(paths[0], params={"type": "stb", "action": "get_profile", "mac": mac,
                                                  "token": "wrong"})).status_code == 403
    assert (await client.get(paths[0], params={"type": "stb", "action": "get_profile", "mac": mac,
                                                  "token": "wrong"}, headers=auth)).status_code == 403
    assert (await client.get(paths[0], params={"type": "stb", "action": "get_profile", "mac": "11:22:33:44:55:66",
                                                  "token": bearer})).status_code == 403
    assert (await client.get(paths[1], params={"type": "itv", "action": "get_profile", "mac": mac}, headers=auth)).status_code == 400
    await client.post("/import-m3u", headers=headers, json={"raw_m3u": (
        '#EXTM3U\n#EXTINF:-1 group-title="English News",Authorized News\nhttps://media.example/news.m3u8\n'
        '#EXTINF:-1 group-title="Sports",Authorized Sports\nhttps://media.example/sports.m3u8')})
    genres = (await client.get(paths[1], params={"type": "itv", "action": "get_genres", "mac": mac}, headers=auth)).json()["js"]
    assert {group["title"] for group in genres} >= {"English News", "Sports"}
    assert all(group["alias"] for group in genres)
    assert all(group["censored"] == "0" for group in genres)
    genre_ids = {group["title"]: group["id"] for group in genres}
    assert genre_ids["All channels"] == "*"
    assert genre_ids["English News"].isdigit() and genre_ids["Sports"].isdigit()
    categories = await client.get(paths[1], params={"type": "itv", "action": "get_categories", "mac": mac}, headers=auth)
    assert categories.status_code == 200 and categories.json()["js"] == genres
    assert all(0 < int(genre_ids[group]) <= 2_147_483_647 for group in ("English News", "Sports"))
    account = (await client.get(paths[1], params={"type": "account_info", "action": "get_main_info", "mac": mac}, headers=auth)).json()["js"]
    assert account["status"] == "active"
    assert datetime.fromisoformat(account["end_date"]) == datetime.fromisoformat(row["expires_at"])
    channel_list = (await client.post(paths[2], data={"type": "itv", "action": "get_all_channels", "mac": mac}, headers=auth)).json()["js"]
    assert channel_list["channels"] == channel_list["data"]
    channels = channel_list["data"]
    assert {channel["name"] for channel in channels} == {"Authorized News", "Authorized Sports"}
    news = next(channel for channel in channels if channel["name"] == "Authorized News")
    assert news["tv_genre_id"] == genre_ids["English News"]
    assert isinstance(news["id"], str) and isinstance(news["number"], str)
    assert news["xmltv_id"] == "" and news["tv_archive_duration"] == 0
    filtered = (await client.get(paths[3], params={"type": "itv", "action": "get_ordered_list", "genre": genre_ids["Sports"], "mac": mac}, headers=auth)).json()["js"]
    assert filtered["total_items"] == 1 and filtered["data"][0]["name"] == "Authorized Sports"
    assert filtered["data"][0]["tv_genre_id"] == genre_ids["Sports"]
    by_name = (await client.get(paths[3], params={"type": "itv", "action": "get_ordered_list", "genre": "Sports", "mac": mac}, headers=auth)).json()["js"]
    assert by_name["total_items"] == 1
    by_category = (await client.get(paths[3], params={"type": "itv", "action": "get_ordered_list", "category": genre_ids["Sports"], "mac": mac}, headers=auth)).json()["js"]
    assert by_category["total_items"] == 1
    first_page = (await client.get(paths[1], params={"type": "itv", "action": "get_ordered_list", "p": "0", "mac": mac}, headers=auth)).json()["js"]
    assert first_page["cur_page"] == 1 and first_page["max_page_items"] == 50
    assert len(first_page["data"]) == 2 and first_page["data"][0]["use_http_tmp_link"] == "1"
    assert (await client.get(paths[0], params={"type": "itv", "action": "create_link", "mac": mac, "cmd": news["cmd"]}, headers=auth)).json()["js"]["cmd"] == "ffmpeg https://media.example/news.m3u8"
    other_mac = "11:22:33:44:55:66"
    other = (await client.post("/admin/subscribers", headers=headers, json={
        "name": "Other iPhone", "mac_address": other_mac, "months": 1,
    })).json()
    assert other["mac_address"] == other_mac
    assert (await client.get(paths[0], params={"type": "itv", "action": "create_link", "mac": other_mac, "cmd": news["cmd"]}, headers=auth)).status_code == 403
    other_session = (await client.get(paths[0], params={"type": "stb", "action": "handshake", "mac": other_mac})).json()["js"]["token"]
    assert (await client.get(paths[1], params={"type": "itv", "action": "get_all_channels", "mac": mac},
                             headers={"Authorization": f"Bearer {other_session}"})).status_code == 403
    duplicate = (await client.post("/admin/subscribers", headers=headers, json={
        "name": "Duplicate", "mac_address": mac, "months": 1,
    })).json()
    assert (await client.get(paths[0], params=query)).status_code == 403
    await client.patch(f"/admin/subscribers/{duplicate['id']}", headers=headers, json={"mac_address": None})
    assert (await client.get(paths[0], params=query)).status_code == 200
    await client.post(f"/admin/subscribers/{row['id']}/rotate", headers=headers)
    assert (await client.get(paths[1], params={"type": "itv", "action": "get_all_channels", "mac": mac}, headers=auth)).status_code == 403
    assert (await client.get(paths[0], params=query)).status_code == 200
    async with AsyncSessionLocal() as session:
        subscriber = await session.get(Subscriber, row["id"])
        subscriber.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    assert (await client.get(paths[0], params=query)).status_code == 403
    await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"extend_months": 1})
    assert (await client.get(paths[0], params=query)).status_code == 200
    await client.patch(f"/admin/subscribers/{row['id']}", headers=headers, json={"is_active": False})
    assert (await client.get(paths[0], params=query)).status_code == 403
    assert (await client.get(paths[0], params={"type": "itv", "action": "get_all_channels", "mac": mac}, headers=auth)).status_code == 403


@pytest.mark.asyncio
async def test_mag_bootstrap_and_optional_catalog_probes_keep_session_required(client, monkeypatch, caplog):
    key = "long-admin-secret-for-subscriber-tests-1234567890"
    monkeypatch.setattr(settings, "admin_api_key", key)
    monkeypatch.setattr(settings, "enable_mac_stalker_portal", True)
    monkeypatch.setattr(settings, "tv_public_mode", True)
    mac = "AA:BB:CC:DD:EE:FF"
    created = (await client.post("/admin/subscribers", headers={"X-Admin-Key": key}, json={
        "name": "TV", "mac_address": mac, "months": 1,
    })).json()
    private = created["mag_portal_url"].removeprefix("http://127.0.0.1:8000").removesuffix("/c/index.html")
    for api in ("/server/load.php", private + "/portal.php"):
        handshake = await client.get(api, params={"type": "stb", "action": "handshake", "mac": mac})
        assert handshake.status_code == 200
        token = handshake.json()["js"]["token"]
        auth = {"Authorization": f"Bearer {token}"}
        for content_type, action in (("stb", "get_modules"), ("stb", "get_localization"),
                                     ("stb", "get_time"), ("stb", "do_auth"),
                                     ("radio", "get_categories"), ("radio", "get_ordered_list"),
                                     ("series", "get_categories"), ("itv", "get_short_epg"),
                                     ("itv", "get_epg_info"), ("watchdog", "get_events")):
            query = {"type": content_type, "action": action, "mac": mac}
            assert (await client.get(api, params=query)).status_code == 403
            with caplog.at_level("INFO", logger="app.main"):
                response = await client.get(api, params=query, headers=auth)
            assert response.status_code == 200, (content_type, action, response.text)
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert f"status=200 type={content_type} action={action}" in caplog.text
            assert "js" in response.json()
            if action == "get_modules":
                assert {"tv", "vclub", "sclub"}.issubset(response.json()["js"]["all_modules"])
            if action == "get_localization":
                assert response.json()["js"]["time_format"] == "{0}:{1}"
            if content_type == "radio":
                assert response.json()["js"] == ([] if action == "get_categories" else {
                    "data": [], "total_items": 0, "max_page_items": 50, "cur_page": 1})
            if action == "get_short_epg":
                assert response.json()["js"] == {"data": []}
            if action == "get_epg_info":
                assert response.json()["js"] == {"data": {}}
            if action == "do_auth":
                assert response.json()["js"] is True
            if action == "get_events":
                assert response.json()["js"]["data"]["msgs"] == 0
        assert (await client.get(api, params={"mac": mac, "type": "stb", "action": "do_auth",
                                              "password": "unsupported"}, headers=auth)).status_code == 403
        assert (await client.get(api, params={"mac": mac, "type": "stb", "action": "get_modules",
                                              "token": "wrong"}, headers=auth)).status_code == 403
        assert (await client.get(api, params={"mac": mac, "type": "stb", "action": "get_modules"},
                                 headers={"Authorization": "Bearer wrong"})).status_code == 403
        assert (await client.get(api, params={"mac": "11:22:33:44:55:66", "type": "stb",
                                              "action": "get_modules"}, headers=auth)).status_code == 403
        assert (await client.get(api, params={"mac": mac, "type": "stb", "action": "invented-action"},
                                 headers=auth)).status_code == 400
        assert mac not in caplog.text and token not in caplog.text


@pytest.mark.asyncio
async def test_shared_stalker_series_probe_returns_empty_catalog_and_safe_type_logs(client, monkeypatch, caplog):
    key = "long-admin-secret-for-subscriber-tests-1234567890"
    monkeypatch.setattr(settings, "admin_api_key", key)
    monkeypatch.setattr(settings, "enable_mac_stalker_portal", True)
    monkeypatch.setattr(settings, "tv_public_mode", True)
    mac = "AA:BB:CC:DD:EE:FF"
    path = "/server/load.php"
    created = await client.post("/admin/subscribers", headers={"X-Admin-Key": key}, json={
        "name": "TV", "mac_address": mac, "months": 1,
    })
    assert created.status_code == 201
    handshake = await client.get(path, params={"type": "stb", "action": "handshake", "mac": mac})
    assert handshake.status_code == 200
    token = handshake.json()["js"]["token"]
    auth = {"Authorization": f"Bearer {token}"}
    query = {"type": "series", "action": "get_categories", "mac": mac}
    assert (await client.get(path, params=query)).status_code == 403
    with caplog.at_level("INFO", logger="app.main"):
        categories = await client.get(path, params={**query, "token": token})
        series = await client.post(path, data={"type": "series", "action": "get_ordered_list",
                                               "p": "0", "mac": mac}, headers=auth)
        unknown = await client.get(path, params={"type": "secret-value", "action": "get_categories",
                                                 "mac": mac}, headers=auth)
    assert categories.status_code == 200 and categories.json() == {"js": []}
    assert categories.headers["cache-control"] == "no-store"
    assert series.status_code == 200
    assert series.json() == {"js": {"data": [], "total_items": 0, "max_page_items": 50, "cur_page": 1}}
    assert unknown.status_code == 400
    assert "status=200 type=series action=get_categories" in caplog.text
    assert "status=200 type=series action=get_ordered_list" in caplog.text
    assert "status=400 type=other action=get_categories" in caplog.text
    assert "secret-value" not in caplog.text and mac not in caplog.text and token not in caplog.text
    movies = await client.get(path, params={**query, "type": "vod"}, headers=auth)
    assert movies.status_code == 200 and movies.json()["js"][0]["title"] == "All movies"
    assert (await client.get(path, params={**query, "action": "create_link", "cmd": "ffmpeg vod_1"},
                             headers=auth)).status_code == 404
    assert (await client.get(path, params={**query, "action": "get_ordered_list", "p": "invalid"},
                             headers=auth)).status_code == 400
    assert (await client.get(path, params={**query, "action": "create_link", "cmd": "vod_1"},
                             headers=auth)).status_code == 404


@pytest.mark.asyncio
async def test_public_tv_logs_rejected_mag_paths_without_exposing_credentials(client, monkeypatch, caplog):
    monkeypatch.setattr(settings, "tv_public_mode", True)
    monkeypatch.setattr(settings, "enable_mac_stalker_portal", True)
    secret = "sensitive-path-or-action"
    with caplog.at_level("INFO", logger="app.main"):
        rejected = await client.get("/c/server/load.php", params={
            "type": "itv", "action": "get_categories", "mac": "AA:BB:CC:DD:EE:FF", "token": secret,
        })
        unmatched = await client.get(f"/{secret}/portal.php", params={
            "type": secret, "action": secret, "mac": "AA:BB:CC:DD:EE:FF",
        })
        disabled = await client.get("/server/load.php", params={
            "type": "itv", "action": "get_categories", "mac": "AA:BB:CC:DD:EE:FF",
        })
    assert rejected.status_code == unmatched.status_code == 404
    assert disabled.status_code == 403
    assert caplog.text.count("MAG request status=404 type=itv action=get_categories scope=unmatched") == 1
    assert "MAG request status=404 type=other action=other scope=unmatched" in caplog.text
    assert "MAG request status=403 type=itv action=get_categories scope=shared endpoint=/server/load.php" in caplog.text
    assert secret not in caplog.text and "AA:BB:CC:DD:EE:FF" not in caplog.text


@pytest.mark.asyncio
async def test_stalker_cookie_only_session_and_conflicting_credentials(client, monkeypatch, caplog):
    key = "long-admin-secret-for-subscriber-tests-1234567890"
    monkeypatch.setattr(settings, "admin_api_key", key)
    monkeypatch.setattr(settings, "enable_mac_stalker_portal", True)
    monkeypatch.setattr(settings, "tv_public_mode", True)
    mac = "AA:BB:CC:DD:EE:FF"
    created = (await client.post("/admin/subscribers", headers={"X-Admin-Key": key}, json={
        "name": "Cookie STB", "mac_address": mac, "months": 1,
    })).json()
    private = created["mag_portal_url"].removeprefix("http://127.0.0.1:8000").removesuffix("/c/index.html")
    for path in ("/portal.php", private + "/server/load.php"):
        handshake = await client.get(path, params={"type": "stb", "action": "handshake"},
                                     headers={"Cookie": "mac=AA%3ABB%3ACC%3ADD%3AEE%3AFF"})
        assert handshake.status_code == 200
        token = handshake.json()["js"]["token"]
        query = {"type": "stb", "action": "get_profile"}
        cookie = {"Cookie": f"mac=AA%3ABB%3ACC%3ADD%3AEE%3AFF; token={token}"}
        with caplog.at_level("INFO", logger="app.main"):
            profile = await client.get(path, params=query, headers=cookie)
            assert profile.status_code == 200
            assert profile.json()["js"]["token"] == token
            assert (await client.post(path, data={"type": "itv", "action": "get_genres"},
                                      headers=cookie)).status_code == 200
            assert (await client.get(path, params=query,
                                     headers={**cookie, "Authorization": f"Bearer {token}"})).status_code == 200
            assert (await client.get(path, params={**query, "token": token},
                                     headers=cookie)).status_code == 200
            assert (await client.get(path, params={**query, "token": "wrong"},
                                     headers=cookie)).status_code == 403
            assert (await client.get(path, params=query,
                                     headers={**cookie, "Authorization": "Bearer wrong"})).status_code == 403
            assert (await client.get(path, params=query,
                                     headers={"Cookie": "mac=AA%3ABB%3ACC%3ADD%3AEE%3AFF; token=wrong"})).status_code == 403
            assert (await client.get(path, params=query,
                                     headers={"Cookie": f"mac=11%3A22%3A33%3A44%3A55%3A66; token={token}"})).status_code in (403, 404)
        assert token not in caplog.text and mac not in caplog.text


@pytest.mark.asyncio
async def test_shared_portal_settings_are_admin_only_and_stable_across_accounts(client, monkeypatch):
    key = "long-admin-secret-for-subscriber-tests-1234567890"
    monkeypatch.setattr(settings, "admin_api_key", key)
    monkeypatch.setattr(settings, "tv_public_mode", True)
    monkeypatch.setattr(settings, "subscriber_base_url", "https://tv.example")
    monkeypatch.setattr(settings, "enable_mac_stalker_portal", True)
    headers = {"X-Admin-Key": key}
    path = "/admin/subscribers/portal-settings"
    assert (await client.get(path)).status_code == 401
    assert (await client.options(path)).status_code == 404
    first = await client.get(path, headers=headers)
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    expected = {"enabled": True, "server_url": "https://tv.example",
                "mag_portal_url": "https://tv.example/c/index.html"}
    assert first.json() == expected
    for mac in ("AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"):
        created = await client.post("/admin/subscribers", headers=headers,
                                    json={"name": "TV", "mac_address": mac, "months": 1})
        assert created.status_code == 201
        assert (await client.get(path, headers=headers)).json() == expected
        handshake = await client.get("/portal.php", params={"type": "stb", "action": "handshake", "mac": mac})
        assert handshake.status_code == 200
    monkeypatch.setattr(settings, "enable_mac_stalker_portal", False)
    assert (await client.get(path, headers=headers)).json()["enabled"] is False
    assert (await client.get("/c/index.html")).status_code == 404


@pytest.mark.asyncio
async def test_existing_customer_private_stalker_server_supports_portal_php_without_mac_only_login(client, monkeypatch):
    monkeypatch.setattr(settings, "tv_public_mode", True)
    monkeypatch.setattr(settings, "enable_mac_stalker_portal", False)
    admin = {"X-Admin-Key": "long-admin-secret-for-subscriber-tests-1234567890"}
    monkeypatch.setattr(settings, "admin_api_key", admin["X-Admin-Key"])
    mac = "00:1A:79:67:CB:47"
    row = (await client.post("/admin/subscribers", headers=admin, json={
        "name": "Existing TV", "mac_address": mac, "months": 1,
    })).json()
    private = row["mag_portal_url"].removeprefix("http://127.0.0.1:8000").removesuffix("/c/index.html")
    entry_points = ("/portal.php", "/server/load.php", "/stalker_portal/portal.php", "/stalker_portal/server/load.php")
    handshake = {"type": "stb", "action": "handshake", "mac": mac}
    assert (await client.get("/portal.php", params=handshake)).status_code == 404
    for entry in entry_points:
        assert (await client.get(private + entry, params={**handshake, "mac": "00:00:00:00:00:00"})).status_code == 403
        result = await client.get(private + entry, params=handshake)
        assert result.status_code == 200, entry
        assert result.json()["js"]["token"]
    cookie = await client.post(private + "/portal.php", data={"type": "stb", "action": "handshake"},
                               headers={"Cookie": "mac=00%3A1A%3A79%3A67%3ACB%3A47"})
    assert cookie.status_code == 200
    bearer = cookie.json()["js"]["token"]
    assert (await client.get(private + "/server/portal.php", params={"type": "stb", "action": "get_profile",
                                                                    "mac": mac, "token": bearer})).status_code == 200
    assert (await client.get(private + "/stalker_portal/server/portal.php",
                             params={"type": "stb", "action": "get_profile", "mac": mac,
                                     "token": bearer})).status_code == 200
    assert (await client.get(private + "/stalker_portal/server/load.php", params={
        "type": "stb", "action": "get_profile", "mac": mac,
    }, headers={"Authorization": f"Bearer {bearer}"})).json()["js"]["mac"] == mac
    assert (await client.get(private + "/server/load.php", params={
        "type": "series", "action": "get_categories", "mac": mac,
    }, headers={"Authorization": f"Bearer {bearer}"})).json() == {"js": []}
    assert (await client.get(private + "/portal.php", params={
        "type": "itv", "action": "get_all_channels", "mac": mac,
    })).status_code == 403
    assert (await client.get(private + "/portal.php", params=handshake,
                             headers={"Cookie": "mac=11:22:33:44:55:66"})).status_code == 403
    await client.patch(f"/admin/subscribers/{row['id']}", headers=admin, json={"is_active": False})
    assert (await client.get(private + "/portal.php", params=handshake)).status_code == 404
    await client.patch(f"/admin/subscribers/{row['id']}", headers=admin, json={"is_active": True})
    assert (await client.get(private + "/portal.php", params=handshake)).status_code == 200
    rotated = (await client.post(f"/admin/subscribers/{row['id']}/rotate", headers=admin)).json()
    assert (await client.get(private + "/portal.php", params=handshake)).status_code == 404
    replacement = rotated["mag_portal_url"].removeprefix("http://127.0.0.1:8000").removesuffix("/c/index.html")
    assert (await client.get(replacement + "/portal.php", params=handshake)).status_code == 200


@pytest.mark.asyncio
async def test_admin_portal_check_resolves_existing_token_and_mac_without_mutation(client, monkeypatch):
    monkeypatch.setattr(settings, "tv_public_mode", True)
    key = "long-admin-secret-for-subscriber-tests-1234567890"
    monkeypatch.setattr(settings, "admin_api_key", key)
    headers = {"X-Admin-Key": key}
    mac = "00:1A:79:67:CB:47"
    first = (await client.post("/admin/subscribers", headers=headers, json={
        "name": "Existing TV", "mac_address": mac, "months": 1,
    })).json()
    other = (await client.post("/admin/subscribers", headers=headers, json={
        "name": "Other TV", "mac_address": mac, "months": 1,
    })).json()
    token = first["mag_portal_url"].split("/stalker/")[1].split("/c/")[0]
    payload = {"token": token, "mac_address": mac}
    assert (await client.post("/admin/subscribers/portal-check", json=payload)).status_code == 401
    assert (await client.get("/admin/subscribers/portal-check", headers=headers)).status_code == 404
    assert (await client.post("/admin/subscribers/portal-check", headers=headers, json={
        **payload, "token": "invalid",
    })).status_code == 422
    assert (await client.post("/admin/subscribers/portal-check", headers=headers, json={
        **payload, "token": "a" * 43,
    })).status_code == 404
    checked = await client.post("/admin/subscribers/portal-check", headers=headers, json=payload)
    assert checked.status_code == 200
    assert checked.headers["cache-control"] == "no-store"
    assert checked.json()["id"] == first["id"] != other["id"]
    assert checked.json()["mac_matches"] is True
    assert checked.json()["is_active"] is True
    assert checked.json()["portal_origin"] == "http://127.0.0.1:8000"
    assert datetime.fromisoformat(checked.json()["expires_at"]) == datetime.fromisoformat(first["expires_at"])
    assert token not in checked.text and "token_hash" not in checked.text
    wrong = await client.post("/admin/subscribers/portal-check", headers=headers, json={
        **payload, "mac_address": "AA:BB:CC:DD:EE:FF",
    })
    assert wrong.json()["mac_matches"] is False
    await client.patch(f"/admin/subscribers/{first['id']}", headers=headers, json={"is_active": False})
    suspended = (await client.post("/admin/subscribers/portal-check", headers=headers, json=payload)).json()
    assert suspended["id"] == first["id"] and suspended["is_active"] is False
    async with AsyncSessionLocal() as session:
        row = await session.get(Subscriber, first["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    expired = (await client.post("/admin/subscribers/portal-check", headers=headers, json=payload)).json()
    assert datetime.fromisoformat(expired["expires_at"]) < datetime.now(timezone.utc)
    listed = (await client.get("/admin/subscribers", headers=headers)).json()
    assert len(listed) == 2
    assert (await client.get(first["mag_portal_url"].removeprefix("http://127.0.0.1:8000"))).status_code == 404
    replaced = (await client.post(f"/admin/subscribers/{first['id']}/rotate", headers=headers)).json()
    assert (await client.post("/admin/subscribers/portal-check", headers=headers, json=payload)).status_code == 404
    replacement_token = replaced["mag_portal_url"].split("/stalker/")[1].split("/c/")[0]
    assert (await client.post("/admin/subscribers/portal-check", headers=headers, json={
        **payload, "token": replacement_token,
    })).json()["id"] == first["id"]