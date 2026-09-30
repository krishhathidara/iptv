"""Sync VidSrc's public ID lists and recent JSON feeds; never fetch video."""

from __future__ import annotations

import re
import json
import asyncio
import logging

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VidsrcTitle
from app.services.channel_scraper import normalize_name

ORIGIN = "https://vidsrc.sh"
FEEDS = {"movie": "movies", "tv": "tvshows"}
IMDB_ID = re.compile(r"tt\d{4,16}\Z")
MAX_PAGES = 5000
MAX_BYTES = 512_000
MAX_ID_BYTES = 2_000_000
IMDB_FILES = {"movie": "movie_imdb.txt", "tv": "tv_imdb.txt"}
logger = logging.getLogger(__name__)


async def _get_limited(client: httpx.AsyncClient, url: str, limit: int) -> bytes:
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        parts = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                raise ValueError("VidSrc response exceeded the size limit")
            parts.append(chunk)
    return b"".join(parts)


async def sync_vidsrc_catalog(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> tuple[int, int, int, int]:
    """Import ID lists and every advertised feed page; preserve data on failure."""
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20, follow_redirects=False)
    try:
        found: dict[str, tuple[str, str, str]] = {}
        for media_type, filename in IMDB_FILES.items():
            body = await _get_limited(client, f"{ORIGIN}/ids/{filename}", MAX_ID_BYTES)
            ids = body.decode("utf-8", errors="replace").splitlines()
            valid = {value.strip() for value in ids if IMDB_ID.fullmatch(value.strip())}
            if not valid:
                raise ValueError(f"VidSrc {media_type} ID list was empty")
            for imdb_id in sorted(valid):
                embed = f"{ORIGIN}/embed/{media_type}/{imdb_id}"
                found[embed] = (imdb_id, media_type, f"{media_type.title()} · {imdb_id}")

        latest: dict[str, tuple[str, str, str]] = {}
        for media_type, path in FEEDS.items():
            page_number = 1
            total_pages = None
            while total_pages is None or page_number <= total_pages:
                url = f"{ORIGIN}/{path}/latest/page-{page_number}.json"
                try:
                    payload = json.loads(await _get_limited(client, url, MAX_BYTES))
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 429:
                        raise
                    # Names are optional: the downloaded ID lists are the full index.
                    logger.warning("VidSrc rate-limited %s recent-feed metadata at page %s; indexing complete ID lists instead", media_type, page_number)
                    break
                pages = payload.get("pages")
                if not isinstance(pages, int) or isinstance(pages, bool) or not 1 <= pages <= MAX_PAGES:
                    raise ValueError("VidSrc returned an invalid page count")
                if total_pages is None:
                    total_pages = pages
                if not isinstance(payload.get("result"), list):
                    raise ValueError("VidSrc returned an invalid result list")
                for item in payload["result"]:
                    if not isinstance(item, dict):
                        continue
                    imdb_id = item.get("imdb_id")
                    title = item.get("title")
                    if not isinstance(imdb_id, str) or not IMDB_ID.fullmatch(imdb_id):
                        continue
                    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 512:
                        continue
                    # Construct from a validated ID instead of trusting arbitrary feed URLs.
                    embed = f"{ORIGIN}/embed/{media_type}/{imdb_id}"
                    if embed in found:
                        latest[embed] = (imdb_id, media_type, title.strip())
                page_number += 1
        # Preserve previously resolved titles; only latest-feed entries get new names.
        # Do not execute a query containing 100k URLs or load all ORM rows into memory.
        dialect = session.bind.dialect.name
        inserted = 0
        all_items = list(found.items())
        latest_items = list(latest.items())
        try:
            await session.execute(update(VidsrcTitle).values(is_active=False))
            for chunk_start in range(0, len(all_items), 400):
                batch = all_items[chunk_start:chunk_start + 400]
                values = [dict(embed_url=embed, imdb_id=imdb_id, media_type=media_type,
                               title=title, normalized_title=normalize_name(title), is_active=True)
                          for embed, (imdb_id, media_type, title) in batch]
                known = set((await session.scalars(select(VidsrcTitle.embed_url).where(
                    VidsrcTitle.embed_url.in_([row["embed_url"] for row in values])
                ))).all())
                inserted += len(values) - len(known)
                if dialect in {"sqlite", "postgresql"}:
                    factory = sqlite_insert if dialect == "sqlite" else postgresql_insert
                    statement = factory(VidsrcTitle).values(values)
                    await session.execute(statement.on_conflict_do_update(
                        index_elements=[VidsrcTitle.embed_url], set_={"is_active": True}
                    ))
                else:
                    for value in values:
                        if value["embed_url"] in known:
                            await session.execute(update(VidsrcTitle).where(VidsrcTitle.embed_url == value["embed_url"]).values(is_active=True))
                        else:
                            session.add(VidsrcTitle(**value))
                    await session.flush()
            for chunk_start in range(0, len(latest_items), 400):
                for embed, (_, _, title) in latest_items[chunk_start:chunk_start + 400]:
                    await session.execute(update(VidsrcTitle).where(VidsrcTitle.embed_url == embed).values(
                        title=title, normalized_title=normalize_name(title)
                    ))
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        total = await session.scalar(select(func.count(VidsrcTitle.id)).where(VidsrcTitle.is_active.is_(True))) or 0
        return len(found), inserted, len(found) - inserted, total
    finally:
        if own_client:
            await client.aclose()


async def resolve_vidsrc_titles(session: AsyncSession, ids: list[int], *, client: httpx.AsyncClient | None = None) -> list[VidsrcTitle]:
    """Resolve only requested visible ID-list entries from the provider's info API."""
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=10, follow_redirects=False)
    try:
        rows = (await session.scalars(select(VidsrcTitle).where(
            VidsrcTitle.id.in_(ids), VidsrcTitle.is_active.is_(True)
        ))).all()
        semaphore = asyncio.Semaphore(4)

        async def get_title(row: VidsrcTitle) -> tuple[VidsrcTitle, str | None]:
            if not row.title.endswith(f"· {row.imdb_id}"):
                return row, None
            async with semaphore:
                try:
                    body = await _get_limited(client, f"{ORIGIN}/info/{row.media_type}/{row.imdb_id}.json", 64_000)
                    info = json.loads(body)
                    title = info.get("title") if info.get("status_code") == 200 else None
                    return row, title.strip() if isinstance(title, str) and 1 <= len(title.strip()) <= 512 else None
                except (httpx.HTTPError, ValueError, KeyError):
                    return row, None

        for row, title in await asyncio.gather(*(get_title(row) for row in rows)):
            if title:
                row.title = title
                row.normalized_title = normalize_name(title)
        await session.commit()
        return rows
    finally:
        if own_client:
            await client.aclose()