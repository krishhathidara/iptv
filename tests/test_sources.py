import pytest
from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models import Channel, PlaylistSource
from app.config import settings
from app.services.channel_scraper import ChannelScraper, ParseResult, ScrapedChannel
from app.services.demo_data import DEMO_SOURCE, build_demo_channels, seed_demo_catalog
from app.services.public_catalog import sync_public_catalog
from app.services.source_sync import SourceSyncResult, redact_url, sync_playlist_source


def test_builds_large_synthetic_catalog_with_unique_records():
    channels = build_demo_channels(10_001)

    assert len(channels) == 10_001
    assert len({item["stream_url"] for item in channels}) == 10_001
    assert len({item["tvg_id"] for item in channels}) == 10_001
    assert channels[-1]["synthetic"] is True


def test_redacts_playlist_credentials_and_query_values():
    preview = redact_url(
        "https://operator:secret@provider.example:8443/live/list.m3u?username=alice&password=hidden#fragment"
    )

    assert preview == "https://provider.example:8443/live/list.m3u?…"
    assert "operator" not in preview
    assert "secret" not in preview
    assert "alice" not in preview
    assert "hidden" not in preview


@pytest.mark.asyncio
async def test_worldwide_managed_refresh_updates_language_metadata(monkeypatch):
    async def fake_load(self, url, **kwargs):
        return ParseResult(records=[ScrapedChannel(
            name="Example World TV", stream_url="https://example.org/live.m3u8",
            tvg_id="ExampleWorldTV.fr", source_name=settings.public_catalog_name,
        )])

    async def fake_validate(self, records, **kwargs):
        return list(records), []

    calls = []

    async def fake_enrich(session, source_name):
        calls.append(source_name)
        return 1

    monkeypatch.setattr(ChannelScraper, "load_from_url", fake_load)
    monkeypatch.setattr(ChannelScraper, "validate_records", fake_validate)
    monkeypatch.setattr("app.services.source_sync.enrich_public_catalog_languages", fake_enrich)
    async with AsyncSessionLocal() as session:
        source = PlaylistSource(name=settings.public_catalog_name, playlist_url="https://example.org/world.m3u")
        session.add(source)
        await session.commit()
        result = await sync_playlist_source(session, source)
    assert result.snapshot_usable
    assert result.imported_count == 1
    assert calls == [settings.public_catalog_name]


@pytest.mark.asyncio
async def test_managed_source_sync_reconciles_and_deletes_channels(client, monkeypatch):
    snapshots = [
        [
            ScrapedChannel(
                name="Provider News",
                stream_url="https://streams.example/news.m3u8",
                source_name="Licensed Provider",
            ),
            ScrapedChannel(
                name="Provider Sports",
                stream_url="https://streams.example/sports.m3u8",
                source_name="Licensed Provider",
            ),
        ],
        [
            ScrapedChannel(
                name="Provider News HD",
                stream_url="https://streams.example/news.m3u8",
                source_name="Licensed Provider",
            )
        ],
    ]

    async def fake_load_from_url(self, source_url, **kwargs):
        assert source_url.endswith("password=hidden")
        return ParseResult(records=snapshots.pop(0))

    monkeypatch.setattr(ChannelScraper, "load_from_url", fake_load_from_url)

    created = await client.post(
        "/sources",
        json={
            "name": "Licensed Provider",
            "playlist_url": "https://operator:secret@provider.example/list.m3u?password=hidden",
            "default_language_code": "en",
            "default_country_code": "US",
            "default_category": "General",
            "replace_missing": True,
        },
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["url_preview"] == "https://provider.example/list.m3u?…"
    assert "secret" not in created.text
    assert "hidden" not in created.text

    first_sync = await client.post(f"/sources/{source['id']}/sync")
    assert first_sync.status_code == 200, first_sync.text
    assert first_sync.json()["result"]["inserted_count"] == 2
    assert first_sync.json()["result"]["deactivated_count"] == 0

    listed = await client.get("/sources")
    assert listed.status_code == 200
    assert listed.json()[0]["active_channel_count"] == 2
    assert "password=hidden" not in listed.text

    second_sync = await client.post(f"/sources/{source['id']}/sync")
    assert second_sync.status_code == 200, second_sync.text
    assert second_sync.json()["result"]["updated_count"] == 1
    assert second_sync.json()["result"]["deactivated_count"] == 1

    active = await client.get("/channels")
    assert active.json()["total"] == 1
    assert active.json()["items"][0]["name"] == "Provider News HD"

    removed = await client.delete(f"/sources/{source['id']}")
    assert removed.status_code == 200
    assert removed.json()["deactivated_count"] == 1
    assert (await client.get("/sources")).json() == []
    assert (await client.get("/channels")).json()["total"] == 0


@pytest.mark.asyncio
async def test_public_catalog_warning_preserves_demo_rows(monkeypatch):
    async def fake_sync(_session, _source):
        from app.services.channel_scraper import ScrapeFailure

        return SourceSyncResult(
            parsed_count=1,
            inserted_count=1,
            updated_count=0,
            deactivated_count=0,
            failures=[ScrapeFailure(reason="upstream warning")],
        )

    monkeypatch.setattr("app.services.public_catalog.sync_playlist_source", fake_sync)
    async with AsyncSessionLocal() as session:
        await seed_demo_catalog(session)
        result = await sync_public_catalog(session)
        demo_count = await session.scalar(
            select(func.count(Channel.id)).where(Channel.source_name == DEMO_SOURCE)
        )

    assert result.removed_demo_channels == 0
    assert demo_count == 6


@pytest.mark.asyncio
async def test_clean_public_catalog_sync_replaces_demo_rows(monkeypatch):
    async def fake_sync(_session, _source):
        return SourceSyncResult(
            parsed_count=2,
            inserted_count=2,
            updated_count=0,
            deactivated_count=0,
            failures=[],
            snapshot_usable=True,
        )

    monkeypatch.setattr("app.services.public_catalog.sync_playlist_source", fake_sync)
    async with AsyncSessionLocal() as session:
        await seed_demo_catalog(session)
        result = await sync_public_catalog(session)
        demo_count = await session.scalar(
            select(func.count(Channel.id)).where(Channel.source_name == DEMO_SOURCE)
        )

    assert result.removed_demo_channels == 6
    assert demo_count == 0