from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, Vod
from app.config import settings
from app.services.channel_scraper import normalize_name


DEMO_SOURCE = "Official HLS demo catalog"

DEMO_CHANNELS = (
    {
        "name": "Mux Live Lab",
        "stream_url": "https://stream.mux.com/v69RSHhFelSm4701snP22dYz2jICy4E4FUyk02rW4gxRM.m3u8",
        "language_code": "en",
        "country_code": "US",
        "category": "Live Demo",
        "logo_url": "/static/img/channels/mux-live.svg",
        "tvg_id": "demo.mux.live",
    },
    {
        "name": "Big Buck Bunny TV",
        "stream_url": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
        "language_code": "en",
        "country_code": "US",
        "category": "Animation",
        "logo_url": "/static/img/channels/bunny-tv.svg",
        "tvg_id": "demo.bunny.tv",
    },
    {
        "name": "Angel One Showcase",
        "stream_url": "https://storage.googleapis.com/shaka-demo-assets/angel-one-hls/hls.m3u8",
        "language_code": "en",
        "country_code": "GB",
        "category": "Showcase",
        "logo_url": "/static/img/channels/angel-one.svg",
        "tvg_id": "demo.angel.one",
    },
    {
        "name": "Elephants Dream Lab",
        "stream_url": "https://playertest.longtailvideo.com/adaptive/elephants_dream_v4/redundant.m3u8",
        "language_code": "en",
        "country_code": "NL",
        "category": "Technology",
        "logo_url": "/static/img/channels/elephants-dream.svg",
        "tvg_id": "demo.elephants.dream",
    },
    {
        "name": "ARTE Test Showcase",
        "stream_url": "https://test-streams.mux.dev/test_001/stream.m3u8",
        "language_code": "fr",
        "country_code": "FR",
        "category": "Culture",
        "logo_url": "/static/img/channels/arte-demo.svg",
        "tvg_id": "demo.arte.showcase",
    },
    {
        "name": "FDR Archive Demo",
        "stream_url": "https://cdn.jwplayer.com/manifests/pZxWPRg4.m3u8",
        "language_code": "en",
        "country_code": "US",
        "category": "Documentary",
        "logo_url": "/static/img/channels/archive-demo.svg",
        "tvg_id": "demo.fdr.archive",
    },
)

DEMO_VODS = (
    {
        "title": "Big Buck Bunny",
        "stream_url": "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
        "media_type": "movie",
        "release_year": 2008,
        "language_code": "en",
        "country_code": "NL",
        "poster_path": "/static/img/posters/big-buck-bunny.svg",
        "synopsis": "An open animated short used here as a public adaptive-streaming playback demonstration.",
    },
    {
        "title": "Angel One",
        "stream_url": "https://storage.googleapis.com/shaka-demo-assets/angel-one-hls/hls.m3u8",
        "media_type": "movie",
        "release_year": 2019,
        "language_code": "en",
        "country_code": "US",
        "poster_path": "/static/img/posters/angel-one.svg",
        "synopsis": "A public Shaka Player test asset with multiple audio tracks and fragmented MP4 HLS.",
    },
    {
        "title": "Elephants Dream",
        "stream_url": "https://playertest.longtailvideo.com/adaptive/elephants_dream_v4/redundant.m3u8",
        "media_type": "movie",
        "release_year": 2006,
        "language_code": "en",
        "country_code": "NL",
        "poster_path": "/static/img/posters/elephants-dream.svg",
        "synopsis": "An open animated short presented through a public HLS test manifest with redundant renditions.",
    },
    {
        "title": "ARTE Streaming Test",
        "stream_url": "https://test-streams.mux.dev/test_001/stream.m3u8",
        "media_type": "tv",
        "release_year": 2016,
        "language_code": "fr",
        "country_code": "FR",
        "poster_path": "/static/img/posters/arte-stream.svg",
        "synopsis": "A public adaptive-bitrate stream included for testing language, country, and TV filters.",
    },
)

SYNTHETIC_COUNTRIES = (
    ("US", "en"), ("GB", "en"), ("CA", "en"), ("MX", "es"),
    ("BR", "pt"), ("AR", "es"), ("FR", "fr"), ("DE", "de"),
    ("ES", "es"), ("IT", "it"), ("NL", "nl"), ("PL", "pl"),
    ("TR", "tr"), ("IN", "hi"), ("JP", "ja"), ("KR", "ko"),
    ("ID", "id"), ("PH", "en"), ("AU", "en"), ("ZA", "en"),
    ("EG", "ar"), ("SA", "ar"), ("NG", "en"), ("KE", "en"),
)
SYNTHETIC_CATEGORIES = (
    "News", "Sports", "Entertainment", "Movies", "Kids", "Music",
    "Documentary", "Culture", "Lifestyle", "Education", "Technology", "Travel",
)
SYNTHETIC_NETWORKS = (
    "World Desk", "Stadium", "Cinema Central", "Family Mix", "Discovery Lab",
    "Culture House", "Music Room", "Market Watch", "Travel Window", "Learning Hub",
)


def _channel_key(tvg_id: str) -> str:
    return hashlib.sha256(f"tvg:{tvg_id.casefold()}".encode("utf-8")).hexdigest()


def _variant_stream_url(stream_url: str, sequence: int) -> str:
    parsed = urlsplit(stream_url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("nexastream_demo", str(sequence)))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def build_demo_channels(channel_count: int) -> list[dict[str, object]]:
    channel_count = max(len(DEMO_CHANNELS), channel_count)
    records: list[dict[str, object]] = [dict(item) for item in DEMO_CHANNELS]
    for index in range(len(DEMO_CHANNELS), channel_count):
        base = DEMO_CHANNELS[index % len(DEMO_CHANNELS)]
        country, language = SYNTHETIC_COUNTRIES[index % len(SYNTHETIC_COUNTRIES)]
        category = SYNTHETIC_CATEGORIES[index % len(SYNTHETIC_CATEGORIES)]
        network = SYNTHETIC_NETWORKS[index % len(SYNTHETIC_NETWORKS)]
        sequence = index + 1
        records.append(
            {
                "name": f"{network} {country} {sequence:05d}",
                "stream_url": _variant_stream_url(str(base["stream_url"]), sequence),
                "language_code": language,
                "country_code": country,
                "category": category,
                "logo_url": str(base["logo_url"]),
                "tvg_id": f"demo.synthetic.{sequence:05d}",
                "synthetic": True,
            }
        )
    return records


async def seed_demo_catalog(
    session: AsyncSession,
    channel_count: int | None = None,
) -> tuple[int, int]:
    """Synchronize public playback-test records without replacing user catalog data."""
    channels_added = 0
    vods_added = 0

    demo_channels = build_demo_channels(channel_count or settings.demo_channel_count)
    channel_urls_to_keep = [str(item["stream_url"]) for item in demo_channels]
    vod_urls_to_keep = [str(item["stream_url"]) for item in DEMO_VODS]
    existing_demo_rows = (
        await session.scalars(select(Channel).where(Channel.source_name == DEMO_SOURCE))
    ).all()
    channel_urls_set = set(channel_urls_to_keep)
    stale_channel_ids = [row.id for row in existing_demo_rows if row.stream_url not in channel_urls_set]
    for offset in range(0, len(stale_channel_ids), 500):
        await session.execute(
            delete(Channel).where(Channel.id.in_(stale_channel_ids[offset : offset + 500]))
        )
    await session.execute(
        delete(Vod).where(
            Vod.source_name == DEMO_SOURCE,
            Vod.stream_url.not_in(vod_urls_to_keep),
        )
    )

    existing_channels = {row.stream_url: row for row in existing_demo_rows}
    base_urls = [str(item["stream_url"]) for item in DEMO_CHANNELS]
    for row in (
        await session.scalars(
            select(Channel).where(
                Channel.stream_url.in_(base_urls),
                Channel.source_name != DEMO_SOURCE,
            )
        )
    ).all():
        existing_channels[row.stream_url] = row
    for item in demo_channels:
        existing = existing_channels.get(str(item["stream_url"]))
        if existing is not None and existing.source_name != DEMO_SOURCE:
            continue
        tvg_id = str(item["tvg_id"])
        channel = existing or Channel(stream_url=str(item["stream_url"]))
        channel.channel_key = _channel_key(tvg_id)
        channel.name = str(item["name"])
        channel.normalized_name = normalize_name(str(item["name"]))
        channel.tvg_id = tvg_id
        channel.logo_url = str(item["logo_url"])
        channel.language_code = str(item["language_code"])
        channel.country_code = str(item["country_code"])
        channel.category = str(item["category"])
        channel.source_name = DEMO_SOURCE
        channel.is_active = True
        channel.extra_metadata = {
            "demo": True,
            "synthetic": bool(item.get("synthetic", False)),
            "notice": "Public HLS playback-test asset; synthetic metadata demonstrates catalog scale and is not a commercial channel listing.",
        }
        if existing is None:
            session.add(channel)
            channels_added += 1

    existing_vods = {
        row.stream_url: row
        for row in (
            await session.scalars(select(Vod).where(Vod.stream_url.in_(vod_urls_to_keep)))
        ).all()
    }
    for item in DEMO_VODS:
        existing = existing_vods.get(str(item["stream_url"]))
        if existing is not None and existing.source_name != DEMO_SOURCE:
            continue
        vod = existing or Vod(stream_url=str(item["stream_url"]))
        vod.title = str(item["title"])
        vod.normalized_title = normalize_name(str(item["title"]))
        vod.media_type = str(item["media_type"])
        vod.release_year = int(item["release_year"]) if item["release_year"] is not None else None
        vod.poster_path = str(item["poster_path"])
        vod.synopsis = str(item["synopsis"])
        vod.language_code = str(item["language_code"])
        vod.country_code = str(item["country_code"])
        vod.source_name = DEMO_SOURCE
        vod.is_active = True
        vod.extra_metadata = {"demo": True}
        if existing is None:
            session.add(vod)
            vods_added += 1

    await session.commit()
    return channels_added, vods_added