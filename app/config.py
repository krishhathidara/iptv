from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "IPTV Aggregator API"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://iptv:iptv@localhost:5432/iptv"
    redis_url: str | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    create_tables_on_startup: bool = True
    sql_echo: bool = False
    database_pool_size: int = Field(default=20, ge=1)
    database_max_overflow: int = Field(default=30, ge=0)

    import_batch_size: int = Field(default=500, ge=1, le=5000)
    max_import_bytes: int = Field(default=50 * 1024 * 1024, ge=1024)
    max_failure_details: int = Field(default=200, ge=0, le=5000)
    stream_validation_concurrency: int = Field(default=150, ge=1, le=500)
    stream_validation_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    stream_validation_origin: str = "http://127.0.0.1:8000"
    channel_cache_ttl_seconds: int = Field(default=60, ge=0)

    demo_mode: bool = False
    seed_demo_data: bool = False
    demo_channel_count: int = Field(default=6, ge=6, le=100_000)
    seed_public_catalog: bool = False
    tv_public_mode: bool = False
    # MAC-only Stalker players have no private URL credential. Opt in deliberately:
    # a reported MAC can be spoofed and grants access to this catalog.
    enable_mac_stalker_portal: bool = False
    public_catalog_name: str = "IPTV-org Worldwide"
    public_catalog_url: str = "https://iptv-org.github.io/iptv/index.m3u"

    allow_server_file_import: bool = False
    allowed_import_root: Path | None = Path("./imports")

    admin_api_key: str | None = None
    subscriber_base_url: str = "http://127.0.0.1:8000"

    tmdb_api_key: str | None = None
    tmdb_base_url: str = "https://api.themoviedb.org/3"

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("sqlite:///") and not value.startswith("sqlite+aiosqlite:///"):
            return value.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return value

    @field_validator("admin_api_key")
    @classmethod
    def validate_admin_api_key(cls, value: str | None) -> str | None:
        if not value:
            return None
        if len(value) < 32:
            raise ValueError("ADMIN_API_KEY must be at least 32 characters")
        return value

    @field_validator("tmdb_api_key")
    @classmethod
    def blank_api_key_is_none(cls, value: str | None) -> str | None:
        return value or None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
