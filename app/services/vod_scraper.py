from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from app.config import settings
from app.services.channel_scraper import normalize_country, normalize_language, normalize_name


YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2})\b")


@dataclass(slots=True)
class ScrapedVod:
    title: str
    stream_url: str
    media_type: Literal["movie", "tv"] = "movie"
    release_year: int | None = None
    tmdb_id: int | None = None
    poster_path: str | None = None
    synopsis: str | None = None
    language_code: str = "und"
    country_code: str = "ZZ"
    source_name: str = "user-import"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_title(self) -> str:
        return normalize_name(self.title)


class VodScraper:
    """Map user-supplied VOD streams to optional TMDB metadata."""

    def __init__(
        self,
        *,
        api_key: str | None = settings.tmdb_api_key,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=15, follow_redirects=True)

    async def __aenter__(self) -> "VodScraper":
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self.client.aclose()

    def normalize_stream_entry(
        self,
        *,
        title: str,
        stream_url: str,
        media_type: Literal["movie", "tv"] = "movie",
        language_code: str = "und",
        country_code: str = "ZZ",
        source_name: str = "user-import",
    ) -> ScrapedVod:
        year_match = YEAR_PATTERN.search(title)
        release_year = int(year_match.group(1)) if year_match else None
        clean_title = YEAR_PATTERN.sub("", title).strip(" -_.()[]")
        return ScrapedVod(
            title=clean_title or title.strip(),
            stream_url=stream_url.strip(),
            media_type=media_type,
            release_year=release_year,
            language_code=normalize_language(language_code),
            country_code=normalize_country(country_code),
            source_name=source_name,
        )

    async def enrich_with_tmdb(self, vod: ScrapedVod) -> ScrapedVod:
        if not self.api_key:
            return vod
        endpoint = "search/movie" if vod.media_type == "movie" else "search/tv"
        params: dict[str, Any] = {"api_key": self.api_key, "query": vod.title}
        if vod.release_year:
            params["year" if vod.media_type == "movie" else "first_air_date_year"] = vod.release_year
        response = await self.client.get(f"{settings.tmdb_base_url}/{endpoint}", params=params)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            return vod

        match = results[0]
        vod.tmdb_id = match.get("id")
        vod.poster_path = match.get("poster_path")
        vod.synopsis = match.get("overview")
        date_value = match.get("release_date") or match.get("first_air_date") or ""
        if not vod.release_year and len(date_value) >= 4 and date_value[:4].isdigit():
            vod.release_year = int(date_value[:4])
        vod.metadata["tmdb_vote_average"] = match.get("vote_average")
        return vod
