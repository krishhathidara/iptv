import httpx
import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Channel
from app.services.channel_scraper import ChannelScraper, ParseResult, ScrapedChannel
from app.services.public_languages import (
    LANGUAGE_PLAYLIST_URL,
    LANGUAGES_URL,
    enrich_public_catalog_languages,
)


@pytest.mark.asyncio
async def test_language_enrichment_only_changes_known_validated_streams(monkeypatch):
    source = "IPTV-org Worldwide"
    async with AsyncSessionLocal() as session:
        async with ChannelScraper() as scraper:
            await scraper.persist_channels(
                session,
                [
                    ScrapedChannel(name="French TV", stream_url="https://example.com/french.m3u8", source_name=source),
                    ScrapedChannel(name="English TV", stream_url="https://example.com/english.m3u8", source_name=source),
                    ScrapedChannel(name="Unknown TV", stream_url="https://example.com/unknown.m3u8", source_name=source),
                    ScrapedChannel(name="Other Source", stream_url="https://example.com/other.m3u8", source_name="other"),
                ],
            )

        def handler(request):
            assert str(request.url) == LANGUAGES_URL
            return httpx.Response(200, json=[{"name": "French", "code": "fra"}, {"name": "English", "code": "eng"}])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async def fake_load(self, url, **kwargs):
                assert url == LANGUAGE_PLAYLIST_URL
                assert kwargs["source_name"] == source
                rows = [
                    ScrapedChannel(name="French TV", stream_url="https://example.com/french.m3u8", category="French"),
                    ScrapedChannel(name="English TV", stream_url="https://example.com/english.m3u8", category="English"),
                    ScrapedChannel(name="Other Source", stream_url="https://example.com/other.m3u8", category="French"),
                ]
                rows.extend(
                    ScrapedChannel(name="Not validated", stream_url=f"https://example.com/new-{n}.m3u8", category="French")
                    for n in range(1000)
                )
                return ParseResult(records=rows)

            monkeypatch.setattr(ChannelScraper, "load_from_url", fake_load)
            changed = await enrich_public_catalog_languages(session, source, client=client)
            assert changed == 2
            rows = (await session.scalars(select(Channel).order_by(Channel.id))).all()
            assert [(row.name, row.language_code) for row in rows] == [
                ("French TV", "fr"), ("English TV", "en"),
                ("Unknown TV", "und"), ("Other Source", "und"),
            ]
            assert all(row.is_active for row in rows)
            assert await enrich_public_catalog_languages(session, source, client=client) == 0


@pytest.mark.asyncio
async def test_language_enrichment_rejects_truncated_playlist(monkeypatch):
    async def fake_load(self, url, **kwargs):
        return ParseResult(records=[ScrapedChannel(name="Only one", stream_url="https://example.com/1.m3u8", category="English")])

    monkeypatch.setattr(ChannelScraper, "load_from_url", fake_load)
    async with AsyncSessionLocal() as session:
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[{"name": "English", "code": "eng"}])
        )) as client:
            with pytest.raises(ValueError, match="sharply truncated"):
                await enrich_public_catalog_languages(session, "IPTV-org Worldwide", client=client)