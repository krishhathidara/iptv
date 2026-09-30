from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


PrimaryKeyType = BigInteger().with_variant(Integer, "sqlite")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Channel(TimestampMixin, Base):
    __tablename__ = "channels"
    __table_args__ = (
        Index("ix_channels_language_country", "language_code", "country_code"),
        Index("ix_channels_country_language", "country_code", "language_code"),
        Index("ix_channels_category", "category"),
        Index("ix_channels_channel_key", "channel_key"),
        Index("ix_channels_active_name", "is_active", "normalized_name"),
        Index("ix_channels_source_active", "source_name", "is_active"),
    )

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    channel_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False)
    tvg_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    stream_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    language_code: Mapped[str] = mapped_column(
        String(16), nullable=False, default="und", server_default="und"
    )
    country_code: Mapped[str] = mapped_column(
        String(8), nullable=False, default="ZZ", server_default="ZZ"
    )
    category: Mapped[str] = mapped_column(
        String(128), nullable=False, default="Uncategorized", server_default="Uncategorized"
    )
    source_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default="user-import", server_default="user-import"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class PlaylistSource(TimestampMixin, Base):
    __tablename__ = "playlist_sources"
    __table_args__ = (
        Index("ix_playlist_sources_enabled", "enabled"),
        Index("ix_playlist_sources_last_synced", "last_synced_at"),
    )

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    playlist_url: Mapped[str] = mapped_column(Text, nullable=False)
    default_language_code: Mapped[str] = mapped_column(
        String(16), nullable=False, default="und", server_default="und"
    )
    default_country_code: Mapped[str] = mapped_column(
        String(8), nullable=False, default="ZZ", server_default="ZZ"
    )
    default_category: Mapped[str] = mapped_column(
        String(128), nullable=False, default="Uncategorized", server_default="Uncategorized"
    )
    validate_urls: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    replace_missing: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_sync_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="never", server_default="never"
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_parsed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Vod(TimestampMixin, Base):
    __tablename__ = "vods"
    __table_args__ = (
        Index("ix_vods_title", "normalized_title"),
        Index("ix_vods_language_country", "language_code", "country_code"),
        Index("ix_vods_media_type", "media_type"),
    )

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="movie", server_default="movie"
    )
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tmdb_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    poster_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    stream_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    language_code: Mapped[str] = mapped_column(
        String(16), nullable=False, default="und", server_default="und"
    )
    country_code: Mapped[str] = mapped_column(
        String(8), nullable=False, default="ZZ", server_default="ZZ"
    )
    source_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default="user-import", server_default="user-import"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class VidsrcTitle(TimestampMixin, Base):
    """VidSrc feed entry with a provider-controlled iframe, not a direct stream."""

    __tablename__ = "vidsrc_titles"
    __table_args__ = (
        Index("ix_vidsrc_type_title", "media_type", "normalized_title"),
        Index("ix_vidsrc_type_imdb_active", "media_type", "imdb_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    imdb_id: Mapped[str] = mapped_column(String(24), nullable=False)
    media_type: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(512), nullable=False)
    embed_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ImdbMovie(Base):
    """Local, non-commercial copy of movie rows in IMDb's public title.basics subset."""

    __tablename__ = "imdb_movies"
    __table_args__ = (Index("ix_imdb_movies_normalized_title", "normalized_title"),)

    imdb_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(512), nullable=False)
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Subscriber(TimestampMixin, Base):
    """A revocable portal/playlist entitlement; a MAC is only a spoofable device check."""

    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(PrimaryKeyType, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mac_address: Mapped[str | None] = mapped_column(String(17), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
