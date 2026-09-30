"""Enrich validated worldwide streams using IPTV-org's language directory.

The language playlist refers to the same stream URLs as the worldwide catalog;
no media is downloaded and no unvalidated channels are added by this module.
"""

from __future__ import annotations

from collections import Counter

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel
from app.services.channel_scraper import ChannelScraper, normalize_language

LANGUAGE_PLAYLIST_URL = "https://iptv-org.github.io/iptv/index.language.m3u"
LANGUAGES_URL = "https://iptv-org.github.io/api/languages.json"

# Prefer familiar ISO 639-1 codes where they exist; preserve the upstream ISO
# 639-3 code for other languages instead of guessing from country or channel name.
ISO_639_1 = {
    "ara": "ar", "aze": "az", "ben": "bn", "bul": "bg", "cat": "ca",
    "ces": "cs", "dan": "da", "deu": "de", "ell": "el", "eng": "en",
    "est": "et", "fas": "fa", "fin": "fi", "fra": "fr", "guj": "gu",
    "heb": "he", "hin": "hi", "hrv": "hr", "hun": "hu", "hye": "hy",
    "ind": "id", "ita": "it", "jpn": "ja", "kan": "kn", "kor": "ko",
    "lit": "lt", "mal": "ml", "mar": "mr", "nld": "nl", "nor": "no",
    "pan": "pa", "pol": "pl", "por": "pt", "ron": "ro", "rus": "ru",
    "sin": "si", "slk": "sk", "slv": "sl", "spa": "es", "sqi": "sq",
    "srp": "sr", "swe": "sv", "tam": "ta", "tel": "te", "tha": "th",
    "tur": "tr", "ukr": "uk", "urd": "ur", "vie": "vi", "zho": "zh",
}


async def enrich_public_catalog_languages(
    session: AsyncSession,
    source_name: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Label existing source rows by URL, without changing their playback status.

    Network failures propagate to the caller so a failed metadata fetch cannot
    accidentally replace valid language codes with unspecified values.
    """
    async with ChannelScraper(client=client) as scraper:
        language_response = await scraper.client.get(LANGUAGES_URL, timeout=45)
        language_response.raise_for_status()
        language_codes = {
            item["name"].casefold(): ISO_639_1.get(item["code"], item["code"])
            for item in language_response.json()
            if isinstance(item, dict) and isinstance(item.get("name"), str)
            and isinstance(item.get("code"), str)
        }
        directory = await scraper.load_from_url(
            LANGUAGE_PLAYLIST_URL,
            source_name=source_name,
            max_bytes=50 * 1024 * 1024,
        )
    # A few directory entries may contain non-HTTP URLs; they do not prevent
    # matching valid entries already present in the verified stream catalog.
    if len(directory.records) < 1000:
        raise ValueError("IPTV-org language directory is empty or sharply truncated")

    by_url: dict[str, Counter[str]] = {}
    for record in directory.records:
        code = language_codes.get(record.category.casefold())
        if code:
            by_url.setdefault(record.stream_url, Counter())[normalize_language(code)] += 1

    if not by_url:
        raise ValueError("IPTV-org language directory has no recognized languages")

    existing = (
        await session.execute(
            select(Channel.id, Channel.stream_url, Channel.language_code).where(
                Channel.source_name == source_name,
            )
        )
    ).all()
    changes = []
    for channel_id, url, current in existing:
        if url in by_url:
            code = by_url[url].most_common(1)[0][0]
            if current != code:
                changes.append((channel_id, code))
    for offset in range(0, len(changes), 500):
        await session.execute(
            update(Channel),
            [{"id": channel_id, "language_code": code} for channel_id, code in changes[offset : offset + 500]],
        )
        await session.commit()
    return len(changes)