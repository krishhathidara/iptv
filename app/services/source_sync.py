from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Channel, PlaylistSource
from app.services.channel_scraper import ChannelScraper, ScrapeFailure
from app.services.public_languages import enrich_public_catalog_languages

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SourceSyncResult:
    parsed_count: int
    inserted_count: int
    updated_count: int
    deactivated_count: int
    failures: list[ScrapeFailure]
    snapshot_usable: bool = False

    @property
    def imported_count(self) -> int:
        return self.inserted_count + self.updated_count


def redact_url(value: str) -> str:
    """Return a display-safe source URL without credentials, query values, or fragments."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "Invalid URL"
    hostname = parsed.hostname or "unknown-host"
    try:
        if parsed.port:
            hostname = f"{hostname}:{parsed.port}"
    except ValueError:
        pass
    path = parsed.path or "/"
    if len(path) > 72:
        path = f"{path[:34]}…{path[-34:]}"
    suffix = "…" if parsed.query else ""
    return urlunsplit((parsed.scheme, hostname, path, suffix, ""))


async def _deactivate_missing_channels(
    session: AsyncSession,
    *,
    source_name: str,
    imported_urls: set[str],
) -> int:
    existing = (
        await session.execute(
            select(Channel.id, Channel.stream_url).where(
                Channel.source_name == source_name,
                Channel.is_active.is_(True),
            )
        )
    ).all()
    stale_ids = [channel_id for channel_id, stream_url in existing if stream_url not in imported_urls]
    for offset in range(0, len(stale_ids), 1000):
        await session.execute(
            update(Channel)
            .where(Channel.id.in_(stale_ids[offset : offset + 1000]))
            .values(is_active=False)
        )
    if stale_ids:
        await session.commit()
    return len(stale_ids)


async def sync_playlist_source(
    session: AsyncSession,
    source: PlaylistSource,
) -> SourceSyncResult:
    """Fetch and synchronize one operator-configured, authorized M3U source."""
    source.last_sync_status = "running"
    source.last_error = None
    previous_parsed_count = source.last_parsed_count
    previous_active_count = await session.scalar(
        select(func.count(Channel.id)).where(
            Channel.source_name == source.name,
            Channel.is_active.is_(True),
        )
    ) or 0
    await session.commit()

    try:
        async with ChannelScraper(
            validation_concurrency=settings.stream_validation_concurrency,
            playback_origin=settings.stream_validation_origin,
        ) as scraper:
            parsed = await scraper.load_from_url(
                source.playlist_url,
                source_name=source.name,
                default_language_code=source.default_language_code,
                default_country_code=source.default_country_code,
                default_category=source.default_category,
                max_bytes=settings.max_import_bytes,
            )
            parsed_count = len(parsed.records)
            source_failures = list(parsed.failures)
            if source.validate_urls and parsed.records:
                parsed.records, validation_failures = await scraper.validate_records(
                    parsed.records,
                    timeout_seconds=settings.stream_validation_timeout_seconds,
                )
                parsed.failures.extend(validation_failures)

            persistence = await scraper.persist_channels(
                session,
                parsed.records,
                batch_size=settings.import_batch_size,
            )
            parsed.failures.extend(persistence.failures)

        deactivated_count = 0
        minimum_expected = max(int(previous_parsed_count * 0.75), 1)
        minimum_active = max(int(previous_active_count * 0.5), 1)
        minimum_validation_yield = max(int(parsed_count * 0.05), 1)
        snapshot_usable = bool(
            parsed.records
            and not persistence.failures
            and (previous_parsed_count == 0 or parsed_count >= minimum_expected)
            and (previous_active_count == 0 or len(parsed.records) >= minimum_active)
            and (not source.validate_urls or len(parsed.records) >= minimum_validation_yield)
        )
        # Validation rejections are expected for public directories and should remove dead rows.
        # Empty, failed, or sharply truncated source downloads never reconcile the existing catalog.
        if source.replace_missing and snapshot_usable:
            deactivated_count = await _deactivate_missing_channels(
                session,
                source_name=source.name,
                imported_urls={record.stream_url for record in parsed.records},
            )

        if snapshot_usable and source.name == settings.public_catalog_name:
            try:
                updated = await enrich_public_catalog_languages(session, source.name)
                logger.info("Updated language metadata for %s worldwide channel records", updated)
            except Exception as exc:
                # Optional metadata must not discard a validated working catalog.
                await session.rollback()
                logger.warning("Worldwide language enrichment unavailable: %s", exc.__class__.__name__)

        source.last_synced_at = datetime.now(timezone.utc)
        source.last_parsed_count = parsed_count
        source.last_imported_count = persistence.inserted_count + persistence.updated_count
        source.last_failure_count = len(parsed.failures)
        operational_failures = source_failures + persistence.failures
        source.last_error = operational_failures[0].reason[:2000] if operational_failures else None
        if parsed_count == 0 and parsed.failures:
            source.last_sync_status = "error"
        elif not snapshot_usable or operational_failures:
            source.last_sync_status = "warning"
        else:
            source.last_sync_status = "success"
        await session.commit()
        await session.refresh(source)

        return SourceSyncResult(
            parsed_count=parsed_count,
            inserted_count=persistence.inserted_count,
            updated_count=persistence.updated_count,
            deactivated_count=deactivated_count,
            failures=parsed.failures,
            snapshot_usable=snapshot_usable,
        )
    except Exception as exc:
        await session.rollback()
        current = await session.get(PlaylistSource, source.id)
        if current is not None:
            current.last_sync_status = "error"
            current.last_synced_at = datetime.now(timezone.utc)
            current.last_error = f"Unexpected sync failure: {exc.__class__.__name__}"[:2000]
            await session.commit()
        raise