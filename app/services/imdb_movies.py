"""Import IMDb's downloadable, personal-use movie subset without storing video."""

from __future__ import annotations

import gzip
import io
import re
import tempfile

import httpx
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImdbMovie
from app.services.channel_scraper import normalize_name
from app.services.vidsrc_titles import IMDB_BASICS_URL, MAX_DOWNLOAD_BYTES

IMDB_ID = re.compile(r"tt\d{4,16}\Z")
BATCH_SIZE = 1000


async def import_imdb_movies(session: AsyncSession, *, client: httpx.AsyncClient | None = None) -> int:
    """Upsert valid movie rows from title.basics atomically; leave unrelated titles alone."""
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=60, follow_redirects=False)
    imported = 0
    try:
        # Buffer compressed bytes to disk so the network cannot hold a DB transaction open.
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
                    columns = ("tconst", "titleType", "primaryTitle", "startYear", "isAdult")
                    if not all(column in header for column in columns):
                        raise ValueError("IMDb title dataset has an unexpected header")
                    id_col, type_col, title_col, year_col, adult_col = (header.index(column) for column in columns)
                    maximum = max(id_col, type_col, title_col, year_col, adult_col)
                    dialect = session.bind.dialect.name
                    if dialect in {"sqlite", "postgresql"}:
                        factory = sqlite_insert if dialect == "sqlite" else pg_insert
                        statement = factory(ImdbMovie.__table__)
                        statement = statement.on_conflict_do_update(
                            index_elements=[ImdbMovie.imdb_id],
                            set_={"title": statement.excluded.title,
                                  "normalized_title": statement.excluded.normalized_title,
                                  "release_year": statement.excluded.release_year},
                        )
                    else:
                        statement = insert(ImdbMovie.__table__)
                    batch = []
                    for line in text:
                        fields = line.rstrip("\r\n").split("\t")
                        if (len(fields) <= maximum or fields[type_col] != "movie"
                                or fields[adult_col] != "0" or not IMDB_ID.fullmatch(fields[id_col])):
                            continue
                        title = fields[title_col].strip()
                        if not title or title == r"\N" or len(title) > 512:
                            continue
                        raw_year = fields[year_col]
                        year = int(raw_year) if len(raw_year) == 4 and raw_year.isascii() and raw_year.isdigit() else None
                        batch.append({"imdb_id": fields[id_col], "title": title,
                                      "normalized_title": normalize_name(title), "release_year": year})
                        if len(batch) >= BATCH_SIZE:
                            await session.execute(statement, batch)
                            imported += len(batch)
                            batch.clear()
                    if batch:
                        await session.execute(statement, batch)
                        imported += len(batch)
        await session.commit()
        return imported
    except Exception:
        await session.rollback()
        raise
    finally:
        if own_client:
            await client.aclose()