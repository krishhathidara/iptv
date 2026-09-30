from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ImportM3URequest(BaseModel):
    raw_m3u: str | None = None
    file_path: str | None = None
    source_url: str | None = None
    source_name: str = Field(default="user-import", min_length=1, max_length=255)
    default_language_code: str = Field(default="und", max_length=16)
    default_country_code: str = Field(default="ZZ", max_length=8)
    default_category: str = Field(default="Uncategorized", max_length=128)
    validate_urls: bool = False

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> "ImportM3URequest":
        supplied = sum(
            value is not None and value != ""
            for value in (self.raw_m3u, self.file_path, self.source_url)
        )
        if supplied != 1:
            raise ValueError("Provide exactly one of raw_m3u, file_path, or source_url")
        return self


class ImportFailure(BaseModel):
    line_number: int | None = None
    name: str | None = None
    stream_url: str | None = None
    reason: str


class ImportResponse(BaseModel):
    source_name: str
    parsed_count: int
    imported_count: int
    inserted_count: int
    updated_count: int
    deactivated_count: int = 0
    failure_count: int
    failures_truncated: bool = False
    failures: list[ImportFailure] = Field(default_factory=list)


class ChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    logo: str | None = None
    stream_url: str
    alternative_stream_urls: list[str] = Field(default_factory=list)
    language_code: str
    country_code: str
    category: str
    tvg_id: str | None = None
    source_name: str
    is_active: bool
    last_checked_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[ChannelResponse]


class ChannelPlaybackSourcesResponse(BaseModel):
    channel_id: int
    checked_at: datetime
    sources: list[str]
    rejected_count: int = 0


class ChannelPlaybackFailureRequest(BaseModel):
    stream_url: str = Field(min_length=8, max_length=8192)
    reason: str = Field(default="Browser playback failed", max_length=500)


class VodResponse(BaseModel):
    id: int
    title: str
    media_type: str
    release_year: int | None = None
    tmdb_id: int | None = None
    poster_path: str | None = None
    synopsis: str | None = None
    stream_url: str
    language_code: str
    country_code: str
    source_name: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class VodPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[VodResponse]


class VodImportItem(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    stream_url: str = Field(min_length=8, max_length=8192)
    media_type: Literal["movie", "tv"]
    language_code: str = Field(default="und", max_length=16)
    country_code: str = Field(default="ZZ", max_length=8)
    release_year: int | None = Field(default=None, ge=1888, le=2200)
    poster_path: str | None = None
    synopsis: str | None = None

    @field_validator("stream_url")
    @classmethod
    def require_direct_media_url(cls, value: str) -> str:
        cleaned = value.strip()
        try:
            parsed = urlsplit(cleaned)
        except ValueError as exc:
            raise ValueError("Stream URL is invalid") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or not parsed.path.lower().endswith((".m3u8", ".mp4", ".webm"))
        ):
            raise ValueError("Provide a direct HTTP(S) .m3u8, .mp4, or .webm media URL, not a website page")
        return cleaned


class VodImportRequest(BaseModel):
    source_name: str = Field(min_length=1, max_length=255)
    items: list[VodImportItem] = Field(min_length=1, max_length=200)

    @field_validator("source_name")
    @classmethod
    def clean_source_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Source name cannot be blank")
        return cleaned


class VodImportResponse(BaseModel):
    source_name: str
    inserted_count: int
    updated_count: int


class VidsrcTitleResponse(BaseModel):
    id: int
    title: str
    media_type: Literal["movie", "tv"]
    embed_url: str


class VidsrcTitlePage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[VidsrcTitleResponse]


class VidsrcSyncResponse(BaseModel):
    parsed_count: int
    inserted_count: int
    updated_count: int
    total: int


class VidsrcResolveRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=15)


class ImdbMovieResponse(BaseModel):
    imdb_id: str
    title: str
    release_year: int | None
    embed_url: str | None


class ImdbMoviePage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[ImdbMovieResponse]


class FacetItem(BaseModel):
    value: str
    count: int


class CatalogSectionFacets(BaseModel):
    total: int
    languages: list[FacetItem] = Field(default_factory=list)
    countries: list[FacetItem] = Field(default_factory=list)
    categories: list[FacetItem] = Field(default_factory=list)
    media_types: list[FacetItem] = Field(default_factory=list)


class CatalogFacets(BaseModel):
    demo_mode: bool = False
    live: CatalogSectionFacets
    vod: CatalogSectionFacets


class PlaylistSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    playlist_url: str = Field(min_length=8, max_length=4096)
    default_language_code: str = Field(default="und", max_length=16)
    default_country_code: str = Field(default="ZZ", max_length=8)
    default_category: str = Field(default="Uncategorized", max_length=128)
    validate_urls: bool = False
    replace_missing: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Source name cannot be blank")
        return cleaned

    @field_validator("playlist_url")
    @classmethod
    def require_http_url(cls, value: str) -> str:
        cleaned = value.strip()
        try:
            parsed = urlsplit(cleaned)
        except ValueError as exc:
            raise ValueError("Playlist URL is invalid") from exc
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Playlist URL must use HTTP or HTTPS")
        return cleaned


class PlaylistSourceResponse(BaseModel):
    id: int
    name: str
    url_preview: str
    default_language_code: str
    default_country_code: str
    default_category: str
    validate_urls: bool
    replace_missing: bool
    enabled: bool
    last_sync_status: str
    last_synced_at: datetime | None = None
    last_parsed_count: int
    last_imported_count: int
    last_failure_count: int
    last_error: str | None = None
    active_channel_count: int = 0
    created_at: datetime
    updated_at: datetime


class PlaylistSourceSyncResponse(BaseModel):
    source: PlaylistSourceResponse
    result: ImportResponse


ChannelSortField = Literal["name", "language", "country", "category", "created_at"]
VodSortField = Literal["title", "year", "created_at"]
SortDirection = Literal["asc", "desc"]
