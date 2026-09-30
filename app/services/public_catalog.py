from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Channel, PlaylistSource, Vod
from app.services.demo_data import DEMO_SOURCE
from app.services.source_sync import SourceSyncResult, sync_playlist_source


@dataclass(slots=True)
class PublicCatalogSyncResult:
    source: PlaylistSource
    sync: SourceSyncResult
    removed_demo_channels: int
    removed_demo_vods: int
    deactivated_legacy_channels: int


async def sync_public_catalog(session: AsyncSession) -> PublicCatalogSyncResult:
    """Synchronize the worldwide IPTV-org playlist used by the local runner.

    Synthetic and legacy rows are removed only after a complete, usable import.
    Validation rejections are expected, while a failed or truncated download never
    replaces a working local catalog with an empty one.
    """
    legacy_name = "IPTV-org United States"
    source = await session.scalar(
        select(PlaylistSource).where(PlaylistSource.name == settings.public_catalog_name)
    )
    if source is None:
        source = await session.scalar(select(PlaylistSource).where(PlaylistSource.name == legacy_name))
    if source is None:
        source = PlaylistSource(name=settings.public_catalog_name)
        session.add(source)

    source.name = settings.public_catalog_name
    source.playlist_url = settings.public_catalog_url
    source.default_language_code = "und"
    source.default_country_code = "ZZ"
    source.default_category = "General"
    source.validate_urls = True
    source.replace_missing = True
    source.enabled = True
    await session.commit()
    await session.refresh(source)

    result = await sync_playlist_source(session, source)
    removed_demo_channels = 0
    removed_demo_vods = 0
    deactivated_legacy_channels = 0
    if result.snapshot_usable and result.imported_count > 0:
        legacy_update = await session.execute(
            Channel.__table__.update()
            .where(Channel.source_name == legacy_name, Channel.is_active.is_(True))
            .values(is_active=False)
        )
        deactivated_legacy_channels = legacy_update.rowcount or 0
        channel_delete = await session.execute(
            delete(Channel).where(Channel.source_name == DEMO_SOURCE)
        )
        vod_delete = await session.execute(delete(Vod).where(Vod.source_name == DEMO_SOURCE))
        removed_demo_channels = channel_delete.rowcount or 0
        removed_demo_vods = vod_delete.rowcount or 0
        await session.commit()

    return PublicCatalogSyncResult(
        source=source,
        sync=result,
        removed_demo_channels=removed_demo_channels,
        removed_demo_vods=removed_demo_vods,
        deactivated_legacy_channels=deactivated_legacy_channels,
    )


# Backward-compatible import for callers that used the previous US-only helper name.
sync_public_us_catalog = sync_public_catalog