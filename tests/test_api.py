import pytest

from datetime import datetime, timezone

from app.services.channel_scraper import ChannelScraper, ScrapeFailure


@pytest.mark.asyncio
async def test_import_and_filter_channels(client):
    playlist = """#EXTM3U
#EXTINF:-1 tvg-id="one" tvg-language="en" tvg-country="US" group-title="News",Channel One
https://streams.example/one.m3u8
#EXTINF:-1 tvg-id="two" tvg-language="ru" tvg-country="TR" group-title="Sports",Channel Two
https://streams.example/two.m3u8
"""
    imported = await client.post(
        "/import-m3u",
        json={"raw_m3u": playlist, "source_name": "api-test"},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["inserted_count"] == 2

    response = await client.get("/channels", params={"lang": "ru", "country": "tr"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Channel Two"
    assert body["items"][0]["stream_url"] == "https://streams.example/two.m3u8"


@pytest.mark.asyncio
async def test_india_facets_and_playlist_only_include_imported_indian_streams(client):
    playlist = """#EXTM3U
#EXTINF:-1 tvg-country="IN" tvg-language="hi" group-title="News",India News
https://streams.example/india-news.m3u8
#EXTINF:-1 tvg-country="IN" tvg-language="ta" group-title="Movies;Entertainment",Tamil Movies
https://streams.example/tamil.m3u8
#EXTINF:-1 tvg-country="US" group-title="News",US News
https://streams.example/us.m3u8
"""
    imported = await client.post("/import-m3u", json={"raw_m3u": playlist})
    assert imported.status_code == 200, imported.text

    worldwide = (await client.get("/catalog/facets")).json()["live"]
    indian = (await client.get("/catalog/facets", params={"country": "in"})).json()["live"]
    assert worldwide["total"] == 3
    assert indian["total"] == 2
    assert {entry["value"] for entry in indian["languages"]} == {"hi", "ta"}
    assert {entry["value"] for entry in indian["countries"]} == {"IN"}
    assert {entry["value"]: entry["count"] for entry in indian["categories"]} == {
        "News": 1, "Movies": 1, "Entertainment": 1,
    }
    indian_channels = await client.get("/channels", params={"country": "IN", "category": "News"})
    assert indian_channels.json()["total"] == 1
    assert indian_channels.json()["items"][0]["source_name"] == "user-import"
    exported = await client.get("/playlist.m3u", params={"country": "IN"})
    assert "https://streams.example/india-news.m3u8" in exported.text
    assert "https://streams.example/tamil.m3u8" in exported.text
    assert "https://streams.example/us.m3u8" not in exported.text
    assert "yupptv.com" not in exported.text


@pytest.mark.asyncio
async def test_authorized_vod_import_is_idempotent_and_filterable(client):
    payload = {"source_name": "My licensed library", "items": [
        {"title": "My Hindi Movie", "stream_url": "https://media.example/film.mp4?token=demo",
         "media_type": "movie", "language_code": "hi", "country_code": "IN", "release_year": 2024},
        {"title": "My French Show S01E01", "stream_url": "https://media.example/episode.m3u8",
         "media_type": "tv", "language_code": "fr", "country_code": "FR"},
    ]}
    first = await client.post("/vod/import", json=payload)
    assert first.status_code == 200, first.text
    assert first.json() == {"source_name": "My licensed library", "inserted_count": 2, "updated_count": 0}
    again = await client.post("/vod/import", json=payload)
    assert again.json()["inserted_count"] == 0
    assert again.json()["updated_count"] == 2

    facets = (await client.get("/catalog/facets")).json()["vod"]
    assert facets["total"] == 2
    assert {row["value"]: row["count"] for row in facets["languages"]} == {"hi": 1, "fr": 1}
    assert {row["value"] for row in facets["media_types"]} == {"movie", "tv"}
    movie = (await client.get("/vod/search", params={"media_type": "movie", "lang": "hi"})).json()
    assert movie["total"] == 1
    assert movie["items"][0]["stream_url"] == payload["items"][0]["stream_url"]
    tv = (await client.get("/vod/search", params={"media_type": "tv", "lang": "fr"})).json()
    assert tv["total"] == 1
    assert tv["items"][0]["title"] == "My French Show S01E01"


@pytest.mark.asyncio
async def test_vod_import_rejects_webpages_duplicates_and_invalid_metadata(client):
    item = {"title": "Not a media file", "media_type": "tv",
            "stream_url": "https://catalog.example/tv/125988-silo?streaming=true"}
    assert (await client.post("/vod/import", json={"source_name": "source", "items": [item]})).status_code == 422
    item["stream_url"] = "https://media.example/episode.webm"
    assert (await client.post("/vod/import", json={"source_name": "source", "items": [item, item]})).status_code == 422
    assert (await client.post("/vod/import", json={"source_name": " ", "items": [item]})).status_code == 422
    item["media_type"] = "film"
    assert (await client.post("/vod/import", json={"source_name": "source", "items": [item]})).status_code == 422
    assert (await client.get("/vod/search")).json()["total"] == 0


@pytest.mark.asyncio
async def test_category_sections_include_multigroup_channels_and_export_active_m3u(client):
    playlist = """#EXTM3U
#EXTINF:-1 tvg-id="news.us" tvg-country="US" group-title="News",World News
https://streams.example/news.m3u8
#EXTINF:-1 tvg-id="kids.us" tvg-country="US" group-title="Animation;Kids",Kids World
https://streams.example/kids.m3u8
#EXTINF:-1 tvg-id="sport.fr" tvg-country="FR" group-title="Kids;Sports",Youth Sports
https://streams.example/sport.m3u8
#EXTINF:-1 tvg-id="movie.us" tvg-country="US" group-title="Movies",Cinema
https://streams.example/movie.m3u8
"""
    result = await client.post("/import-m3u", json={"raw_m3u": playlist})
    assert result.status_code == 200, result.text

    facets = (await client.get("/catalog/facets")).json()["live"]["categories"]
    counts = {entry["value"]: entry["count"] for entry in facets}
    assert counts["Kids"] == 2
    assert counts["News"] == 1
    assert counts["Sports"] == 1
    assert counts["Animation"] == 1

    kids = await client.get("/channels", params={"category": "Kids"})
    assert kids.status_code == 200
    assert {entry["name"] for entry in kids.json()["items"]} == {"Kids World", "Youth Sports"}
    assert (await client.get("/channels", params={"category": "Sports"})).json()["total"] == 1

    exported = await client.get("/playlist.m3u", params={"category": "Kids", "country": "US"})
    assert exported.status_code == 200
    assert exported.headers["content-type"].lower().startswith("application/x-mpegurl")
    assert "attachment" in exported.headers["content-disposition"]
    assert 'group-title="Animation",Kids World' in exported.text
    assert 'group-title="Kids",Kids World' in exported.text
    assert "https://streams.example/kids.m3u8" in exported.text
    assert "sport.m3u8" not in exported.text
    assert "news.m3u8" not in exported.text

    ids = [entry["id"] for entry in kids.json()["items"]]
    failed = await client.post(
        f"/channels/{ids[0]}/playback-failure",
        json={"stream_url": kids.json()["items"][0]["stream_url"]},
    )
    assert failed.status_code == 200
    assert kids.json()["items"][0]["stream_url"] not in (await client.get("/playlist.m3u")).text


@pytest.mark.asyncio
async def test_reimport_updates_existing_stream(client):
    first = "#EXTM3U\n#EXTINF:-1 tvg-language=\"en\",Old Name\nhttps://example.com/live.m3u8"
    second = "#EXTM3U\n#EXTINF:-1 tvg-language=\"en\",New Name\nhttps://example.com/live.m3u8"

    assert (await client.post("/import-m3u", json={"raw_m3u": first})).status_code == 200
    result = await client.post("/import-m3u", json={"raw_m3u": second})
    assert result.json()["updated_count"] == 1

    channels = (await client.get("/channels", params={"search": "New Name"})).json()
    assert channels["total"] == 1


@pytest.mark.asyncio
async def test_import_uploaded_m3u_file(client):
    playlist = (
        "#EXTM3U\n"
        "#EXTINF:-1 tvg-language=\"tr\" tvg-country=\"TR\",Uploaded Channel\n"
        "https://example.com/uploaded.m3u8\n"
    )
    result = await client.post(
        "/import-m3u",
        files={"file": ("uploaded.m3u", playlist, "audio/x-mpegurl")},
        data={"source_name": "multipart-test"},
    )

    assert result.status_code == 200, result.text
    assert result.json()["inserted_count"] == 1
    channels = (await client.get("/channels", params={"country": "TR"})).json()
    assert channels["items"][0]["source_name"] == "multipart-test"


@pytest.mark.asyncio
async def test_rejects_multiple_multipart_sources(client):
    result = await client.post(
        "/import-m3u",
        files={"file": ("uploaded.m3u", "#EXTM3U\n", "audio/x-mpegurl")},
        data={"source_url": "https://example.com/playlist.m3u"},
    )

    assert result.status_code == 422
    assert "uploaded file alone" in result.json()["detail"]


@pytest.mark.asyncio
async def test_playback_sources_rechecks_alternatives_and_deactivates_failures(client, monkeypatch):
    playlist = """#EXTM3U
#EXTINF:-1 tvg-id="ExampleTV.us@East",Example TV East
https://streams.example/east.m3u8
#EXTINF:-1 tvg-id="ExampleTV.us@West",Example TV West
https://streams.example/west.m3u8
"""
    imported = await client.post(
        "/import-m3u",
        json={"raw_m3u": playlist, "source_name": "playback-test"},
    )
    assert imported.status_code == 200
    channels = (await client.get("/channels")).json()["items"]

    async def fake_validate_records(self, records, **kwargs):
        checked_at = datetime.now(timezone.utc)
        for record in records:
            record.last_checked_at = checked_at
            record.metadata["playback_health"] = {
                "checked_at": checked_at.isoformat(),
                "browser_playable": record.stream_url.endswith("west.m3u8"),
            }
        return (
            [record for record in records if record.stream_url.endswith("west.m3u8")],
            [
                ScrapeFailure(
                    name=record.name,
                    stream_url=record.stream_url,
                    reason="Upstream does not allow browser cross-origin playback (CORS)",
                )
                for record in records
                if record.stream_url.endswith("east.m3u8")
            ],
        )

    monkeypatch.setattr(ChannelScraper, "validate_records", fake_validate_records)
    checked = await client.post(f"/channels/{channels[0]['id']}/playback-sources")

    assert checked.status_code == 200, checked.text
    assert checked.json()["sources"] == ["https://streams.example/west.m3u8"]
    assert checked.json()["rejected_count"] == 1
    active = (await client.get("/channels")).json()
    assert active["total"] == 1
    assert active["items"][0]["stream_url"].endswith("west.m3u8")


@pytest.mark.asyncio
async def test_browser_playback_failure_deactivates_only_reported_source(client):
    playlist = """#EXTM3U
#EXTINF:-1 tvg-id="ExampleTV.us@East",Example TV East
https://streams.example/east.m3u8
#EXTINF:-1 tvg-id="ExampleTV.us@West",Example TV West
https://streams.example/west.m3u8
"""
    assert (
        await client.post(
            "/import-m3u",
            json={"raw_m3u": playlist, "source_name": "browser-failure-test"},
        )
    ).status_code == 200
    channels = (await client.get("/channels")).json()["items"]
    primary = next(item for item in channels if item["stream_url"].endswith("east.m3u8"))

    reported = await client.post(
        f"/channels/{primary['id']}/playback-failure",
        json={
            "stream_url": primary["stream_url"],
            "reason": "The source connected but did not deliver playable video in time.",
        },
    )

    assert reported.status_code == 200, reported.text
    active = (await client.get("/channels")).json()
    assert active["total"] == 1
    assert active["items"][0]["stream_url"].endswith("west.m3u8")
