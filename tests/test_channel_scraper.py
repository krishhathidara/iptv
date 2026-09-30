import httpx
import pytest

from app.services.channel_scraper import ChannelScraper, ScrapedChannel


def test_parses_and_normalizes_extended_m3u():
    raw = """#EXTM3U
#EXTINF:-1 tvg-id="news.example" tvg-logo="https://img.example/logo.png" tvg-language="English" tvg-country="us" group-title="News", Example News
https://stream.example/live/index.m3u8
#EXTINF:-1 tvg-language="Russian" tvg-country="TR",Second Channel
not-a-url
"""
    scraper = ChannelScraper()
    result = scraper.load_from_m3u_text(raw, source_name="test")

    assert len(result.records) == 1
    assert len(result.failures) == 1
    channel = result.records[0]
    assert channel.name == "Example News"
    assert channel.language_code == "en"
    assert channel.country_code == "US"
    assert channel.category == "News"
    assert channel.logo == "https://img.example/logo.png"


def test_uses_defaults_when_playlist_has_no_geographic_metadata():
    scraper = ChannelScraper()
    result = scraper.load_from_m3u_text(
        "#EXTM3U\n#EXTINF:-1,Local One\nhttps://example.com/one.m3u8",
        source_name="test",
        default_language_code="tr",
        default_country_code="TR",
        default_category="Local",
    )

    assert result.records[0].language_code == "tr"
    assert result.records[0].country_code == "TR"
    assert result.records[0].category == "Local"


def test_groups_tvg_variants_and_preserves_player_properties():
    scraper = ChannelScraper()
    result = scraper.load_from_m3u_text(
        """#EXTM3U
#EXTINF:-1 tvg-id="ExampleTV.us@East" http-referrer="https://example.com/player",Example TV East
#EXTVLCOPT:http-referrer=https://example.com/player
https://streams.example/east.m3u8
#EXTINF:-1 tvg-id="ExampleTV.us@West",Example TV West
https://streams.example/west.m3u8
""",
        source_name="test",
        default_country_code="US",
    )

    assert len(result.records) == 2
    assert result.records[0].channel_key == result.records[1].channel_key
    assert result.records[0].metadata["player_properties"]["http-referrer"] == (
        "https://example.com/player"
    )
    assert result.records[0].metadata["playlist_attributes"]["http-referrer"] == (
        "https://example.com/player"
    )


def test_infers_worldwide_country_from_iptv_org_tvg_id():
    scraper = ChannelScraper()
    result = scraper.load_from_m3u_text(
        "#EXTM3U\n#EXTINF:-1 tvg-id=\"BBCNews.uk@HD\",BBC News\nhttps://stream.example/live.m3u8",
        source_name="worldwide",
    )

    assert result.records[0].country_code == "GB"


@pytest.mark.asyncio
async def test_browser_validation_rejects_http_200_manifest_without_cors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "application/vnd.apple.mpegurl"},
            text="#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment.ts\n",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    scraper = ChannelScraper(client=client, playback_origin="http://127.0.0.1:8000")
    record = ScrapedChannel(name="No CORS", stream_url="https://stream.example/live.m3u8")
    try:
        error = await scraper.validate_record(record)
    finally:
        await client.aclose()

    assert error == "Upstream does not allow browser cross-origin playback (CORS)"
    assert record.metadata["playback_health"]["browser_playable"] is False
    assert record.last_checked_at is not None


@pytest.mark.asyncio
async def test_browser_validation_requires_a_reachable_media_segment():
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"Access-Control-Allow-Origin": "http://127.0.0.1:8000"}
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(
                200,
                request=request,
                headers=headers,
                text="#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment.ts\n",
            )
        return httpx.Response(404, request=request, headers=headers)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    scraper = ChannelScraper(client=client, playback_origin="http://127.0.0.1:8000")
    record = ScrapedChannel(name="Missing segment", stream_url="https://stream.example/live.m3u8")
    try:
        error = await scraper.validate_record(record)
    finally:
        await client.aclose()

    assert error == "HLS media segment failed: Stream returned HTTP 404"


@pytest.mark.asyncio
async def test_browser_validation_accepts_manifest_and_current_media_segment():
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"Access-Control-Allow-Origin": "*"}
        if request.url.path.endswith(".m3u8"):
            return httpx.Response(
                200,
                request=request,
                headers=headers,
                text="#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment.ts\n",
            )
        return httpx.Response(200, request=request, headers=headers, content=b"G" * 188)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    scraper = ChannelScraper(client=client, playback_origin="http://127.0.0.1:8000")
    record = ScrapedChannel(name="Live", stream_url="https://stream.example/live.m3u8")
    try:
        error = await scraper.validate_record(record)
    finally:
        await client.aclose()

    assert error is None
    assert record.metadata["playback_health"]["browser_playable"] is True


@pytest.mark.asyncio
async def test_browser_validation_rejects_vod_playlist_as_live_channel():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"Access-Control-Allow-Origin": "*"},
            text=(
                "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment.ts\n"
                "#EXT-X-ENDLIST\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    scraper = ChannelScraper(client=client, playback_origin="http://127.0.0.1:8000")
    record = ScrapedChannel(name="VOD", stream_url="https://stream.example/video.m3u8")
    try:
        error = await scraper.validate_record(record)
    finally:
        await client.aclose()

    assert error == "HLS playlist is on-demand media, not a live stream"


@pytest.mark.asyncio
async def test_browser_validation_follows_master_playlist_to_media_segment():
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        headers = {"Access-Control-Allow-Origin": "*"}
        if request.url.path == "/master.m3u8":
            return httpx.Response(
                200,
                request=request,
                headers=headers,
                text=(
                    "#EXTM3U\n"
                    "#EXT-X-STREAM-INF:BANDWIDTH=2000000\nmedia/high.m3u8\n"
                    "#EXT-X-STREAM-INF:BANDWIDTH=500000\nmedia/low.m3u8\n"
                ),
            )
        if request.url.path == "/media/low.m3u8":
            return httpx.Response(
                200,
                request=request,
                headers=headers,
                text="#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment.ts\n",
            )
        if request.url.path == "/media/segment.ts":
            return httpx.Response(200, request=request, headers=headers, content=b"G" * 188)
        return httpx.Response(404, request=request, headers=headers)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    scraper = ChannelScraper(client=client, playback_origin="http://127.0.0.1:8000")
    record = ScrapedChannel(name="Master", stream_url="https://stream.example/master.m3u8")
    try:
        error = await scraper.validate_record(record)
    finally:
        await client.aclose()

    assert error is None
    assert requested_paths == ["/master.m3u8", "/media/low.m3u8", "/media/segment.ts"]


@pytest.mark.asyncio
async def test_browser_validation_rejects_encrypted_hls():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"Access-Control-Allow-Origin": "*"},
            text=(
                "#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
                "#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\n"
                "#EXTINF:4,\nsegment.ts\n"
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    scraper = ChannelScraper(client=client, playback_origin="http://127.0.0.1:8000")
    record = ScrapedChannel(name="Encrypted", stream_url="https://stream.example/live.m3u8")
    try:
        error = await scraper.validate_record(record)
    finally:
        await client.aclose()

    assert error == "Encrypted or DRM-protected HLS streams are not imported"
