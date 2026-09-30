"""Fill unresolved VidSrc display names from IMDb's non-commercial title dataset."""

from __future__ import annotations

import gzip
import io
import tempfile
from collections import defaultdict

import httpx
from sqlalchemy import bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VidsrcTitle
from app.services.channel_scraper import normalize_name

IMDB_BASICS_URL = "https://datasets.imdbws.com/title.basics.tsv.gz"
MAX_DOWNLOAD_BYTES = 350_000_000
BATCH_SIZE = 500


async def enrich_vidsrc_titles(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> int:
    """Stream the official TSV, matching only local unresolved IDs; apply names atomically."""
    rows = (await session.execute(select(
        VidsrcTitle.id, VidsrcTitle.imdb_id, VidsrcTitle.media_type, VidsrcTitle.title
    ).where(VidsrcTitle.is_active.is_(True)))).all()
    missing: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if row.title == f"{row.media_type.title()} · {row.imdb_id}":
            missing[row.imdb_id].append(row.id)
    if not missing:
        return 0

    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=60, follow_redirects=False)
    updates: list[dict[str, str | int]] = []
    try:
        # A temporary file bounds RAM use even as the IMDb dataset grows.
        with tempfile.TemporaryFile() as compressed:
            size = 0
            async with client.stream("GET", IMDB_BASICS_URL) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise ValueError("IMDb title dataset exceeded the download limit")
                    compressed.write(chunk)
            compressed.seek(0)
            with gzip.GzipFile(fileobj=compressed) as archive:
                with io.TextIOWrapper(archive, encoding="utf-8", errors="replace", newline="") as text:
                    header = next(text).rstrip("\r\n").split("\t")
                    if "tconst" not in header or "primaryTitle" not in header:
                        raise ValueError("IMDb title dataset has an unexpected header")
                    id_column, title_column = header.index("tconst"), header.index("primaryTitle")
                    for line in text:
                        fields = line.rstrip("\r\n").split("\t")
                        if len(fields) <= max(id_column, title_column):
                            continue
                        imdb_id = fields[id_column]
                        if imdb_id not in missing:
                            continue
                        title = fields[title_column].strip()
                        if not title or title == r"\N" or len(title) > 512:
                            continue
                        for record_id in missing.pop(imdb_id):
                            updates.append({
                                "record_id": record_id,
                                "resolved_title": title,
                                "resolved_normalized_title": normalize_name(title),
                            })

        statement = update(VidsrcTitle.__table__).where(VidsrcTitle.id == bindparam("record_id")).values(
            title=bindparam("resolved_title"), normalized_title=bindparam("resolved_normalized_title")
        )
        for start in range(0, len(updates), BATCH_SIZE):
            await session.execute(statement, updates[start:start + BATCH_SIZE])
        await session.commit()
        return len(updates)
    except Exception:
        await session.rollback()
        raise
    finally:
        if own_client:
            await client.aclose()