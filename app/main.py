from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import asc, case, delete, desc, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.config import settings
from app.database import AsyncSessionLocal, engine, get_db, init_db
from app.models import Channel, ImdbMovie, PlaylistSource, Subscriber, VidsrcTitle, Vod
from app.schemas_subscribers import (PortalCheckRequest, PortalCheckResponse, SubscriberCreate,
                                     SubscriberCreated, SubscriberResponse, SubscriberRotated, SubscriberUpdate)
from app.services.subscribers import add_months, new_token, token_hash, utc_datetime
from app.services.mag_portal import issue_session, matching_mac, normalized_mac, valid_session
from app.schemas import (
    ChannelPage,
    ChannelPlaybackFailureRequest,
    ChannelPlaybackSourcesResponse,
    ChannelResponse,
    ChannelSortField,
    CatalogFacets,
    CatalogSectionFacets,
    FacetItem,
    ImdbMoviePage,
    ImdbMovieResponse,
    ImportFailure,
    ImportM3URequest,
    ImportResponse,
    PlaylistSourceCreate,
    PlaylistSourceResponse,
    PlaylistSourceSyncResponse,
    SortDirection,
    VodSortField,
    VodPage,
    VodResponse,
    VodImportRequest,
    VodImportResponse,
    VidsrcSyncResponse,
    VidsrcTitlePage,
    VidsrcTitleResponse,
    VidsrcResolveRequest,
)
from app.services.cache import cache
from app.services.channel_scraper import (
    ChannelScraper,
    ScrapedChannel,
    normalize_country,
    normalize_language,
    normalize_name,
)
from app.services.vidsrc_catalog import resolve_vidsrc_titles, sync_vidsrc_catalog
from app.services.demo_data import seed_demo_catalog
from app.services.public_catalog import sync_public_catalog
from app.services.source_sync import redact_url, sync_playlist_source

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"


def _live_category_filter(value: str):
    """Match a whole category token in semicolon-separated IPTV-org groups."""
    return or_(
        Channel.category == value,
        Channel.category.startswith(f"{value};", autoescape=True),
        Channel.category.contains(f";{value};", autoescape=True),
        Channel.category.endswith(f";{value}", autoescape=True),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.tv_public_mode:
        if not settings.admin_api_key or not settings.database_url.startswith("postgresql+"):
            raise RuntimeError("TV_PUBLIC_MODE requires ADMIN_API_KEY and a persistent PostgreSQL DATABASE_URL")
        if "*" in settings.cors_origins or not public_base_url().startswith("https://"):
            raise RuntimeError("TV_PUBLIC_MODE requires restricted CORS_ORIGINS and an HTTPS subscriber base URL")
    if settings.create_tables_on_startup:
        await init_db()
    if settings.seed_demo_data:
        async with AsyncSessionLocal() as session:
            channels_added, vods_added = await seed_demo_catalog(session)
            logger.info(
                "Demo catalog ready: %s live channels and %s VOD items added",
                channels_added,
                vods_added,
            )
    if settings.seed_public_catalog:
        async with AsyncSessionLocal() as session:
            already_seeded = settings.tv_public_mode and await session.scalar(select(func.count(Channel.id)))
            if not already_seeded:
                result = await sync_public_catalog(session)
                logger.info(
                    "Public worldwide catalog sync: %s parsed, %s browser-playable, %s rejected; "
                    "removed %s synthetic channels, %s demo VOD items, and deactivated %s legacy rows",
                    result.sync.parsed_count,
                    result.sync.imported_count,
                    len(result.sync.failures),
                    result.removed_demo_channels,
                    result.removed_demo_vods,
                    result.deactivated_legacy_channels,
                )
    yield
    await cache.close()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")

# This is an opt-in TV-only deployment. The original catalog/import/dashboard
# endpoints remain available for private/local installs, but not on the public host.
PUBLIC_ASSETS = {
    "/static/css/dashboard.css", "/static/css/watch.css", "/static/css/mag.css",
    "/static/js/watch.js", "/static/js/mag.js", "/static/vendor/hls.min.js",
    "/static/img/favicon.svg",
}
SHARED_STALKER_PATHS = {
    "/portal.php", "/server/load.php", "/server/portal.php",
    "/stalker_portal/portal.php", "/stalker_portal/server/load.php",
    "/stalker_portal/server/portal.php",
}
PRIVATE_STALKER_API_PATH = re.compile(
    r"/stalker/[A-Za-z0-9_-]{32,128}/(?P<endpoint>portal\.php|server/(?:load|portal)\.php|stalker_portal/(?:portal\.php|server/(?:load|portal)\.php))"
)
STALKER_ACTIONS = {
    "handshake", "get_profile", "get_main_info", "get_genres", "get_all_channels",
    "get_ordered_list", "get_categories", "create_link", "get_modules",
    "get_localization", "get_time", "do_auth", "get_short_epg", "get_epg_info", "get_events",
}
STALKER_TYPES = {"stb", "account_info", "itv", "vod", "series", "radio", "watchdog"}


def _log_stalker_request(request: Request, status_code: int) -> None:
    """Log only allowlisted protocol labels; never log URLs, credentials or MACs."""
    path = request.url.path
    private_match = PRIVATE_STALKER_API_PATH.fullmatch(path)
    if path in SHARED_STALKER_PATHS:
        scope, endpoint = "shared", path
    elif private_match:
        scope, endpoint = "private", "/" + private_match.group("endpoint")
    elif path.endswith(("/portal.php", "/load.php")):
        # A client may construct the wrong path. It must still be diagnosable
        # even if the public-TV gate rejects it before routing.
        scope, endpoint = "unmatched", "unmatched"
    else:
        return
    action = getattr(request.state, "stalker_action", request.query_params.get("action", ""))
    content_type = getattr(request.state, "stalker_type", request.query_params.get("type", ""))
    logger.info("MAG request status=%d type=%s action=%s scope=%s endpoint=%s method=%s",
                status_code, content_type if content_type in STALKER_TYPES else "other",
                action if action in STALKER_ACTIONS else "other", scope, endpoint, request.method)


@app.middleware("http")
async def restrict_public_tv(request: Request, call_next):
    if settings.tv_public_mode:
        path, method = request.url.path, request.method
        if method == "OPTIONS" and path.startswith(("/admin/subscribers", "/import-m3u", "/vod/import")):
            return Response(status_code=404)
        allowed = (
            (path in {"/", "/health"} and method in {"GET", "HEAD"})
            or (path in PUBLIC_ASSETS and method in {"GET", "HEAD"})
            or (re.fullmatch(r"/watch/[A-Za-z0-9_-]{32,128}", path) and method in {"GET", "HEAD"})
            or (re.fullmatch(r"/subscribers/playlist/[A-Za-z0-9_-]{32,128}\.m3u", path) and method in {"GET", "HEAD"})
            or (re.fullmatch(r"/stalker/[A-Za-z0-9_-]{32,128}/c/(?:index\.html)?", path) and method in {"GET", "HEAD"})
            or (path in {"/c/", "/c/index.html", "/stalker_portal/c/", "/stalker_portal/c/index.html"}
                and method in {"GET", "HEAD"} and settings.enable_mac_stalker_portal)
            or (PRIVATE_STALKER_API_PATH.fullmatch(path) and method in {"GET", "POST"})
            or (path in SHARED_STALKER_PATHS
                and method in {"GET", "POST"} and settings.enable_mac_stalker_portal)
            or (path in {"/import-m3u", "/vod/import"} and method == "POST")
            or (path == "/admin/subscribers" and method in {"GET", "POST"})
            or (path == "/admin/subscribers/portal-check" and method == "POST")
            or (path == "/admin/subscribers/portal-settings" and method == "GET")
            or (re.fullmatch(r"/admin/subscribers/[1-9][0-9]*", path) and method == "PATCH")
            or (re.fullmatch(r"/admin/subscribers/[1-9][0-9]*/rotate", path) and method == "POST")
        )
        if not allowed:
            _log_stalker_request(request, 404)
            return Response(status_code=404)
        if path in {"/import-m3u", "/vod/import"} and method == "POST":
            supplied = request.headers.get("x-admin-key", "")
            if not settings.admin_api_key or not secrets.compare_digest(supplied, settings.admin_api_key):
                return Response(status_code=401, headers={"WWW-Authenticate": "ApiKey"})
    response = await call_next(request)
    _log_stalker_request(request, response.status_code)
    return response

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


def require_admin(x_admin_key: Annotated[str | None, Header()] = None) -> None:
    """Fail closed until an administrator explicitly configures a long secret."""
    if not settings.admin_api_key or not x_admin_key or not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Administrator key required", headers={"WWW-Authenticate": "ApiKey"})


AdminAccess = Annotated[None, Depends(require_admin)]


def subscriber_link(token: str) -> str:
    return f"{public_base_url()}/subscribers/playlist/{token}.m3u"


def subscriber_portal_link(token: str) -> str:
    return f"{public_base_url()}/watch/{token}"


def subscriber_mag_link(token: str) -> str:
    return f"{public_base_url()}/stalker/{token}/c/index.html"


def public_base_url() -> str:
    """Render supplies this value; an explicit SUBSCRIBER_BASE_URL supports custom domains."""
    base = settings.subscriber_base_url
    if base == "http://127.0.0.1:8000" and settings.tv_public_mode:
        base = os.environ.get("RENDER_EXTERNAL_URL", base)
    return base.rstrip("/")


@app.get("/admin/subscribers/portal-settings", tags=["admin"])
async def portal_settings(response: Response, _: AdminAccess) -> dict[str, str | bool]:
    """Expose the configured shared URLs to the operator, never infer a host from request headers."""
    response.headers["Cache-Control"] = "no-store"
    base = public_base_url()
    return {"enabled": settings.enable_mac_stalker_portal,
            "server_url": base, "mag_portal_url": f"{base}/c/index.html"}


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIRECTORY / ("tv.html" if settings.tv_public_mode else "index.html"))


@app.get("/admin", include_in_schema=False)
async def admin_portal() -> FileResponse:
    return FileResponse(STATIC_DIRECTORY / "admin.html")


@app.get("/watch/{token}", include_in_schema=False)
async def subscriber_watch(token: str, session: DatabaseSession) -> FileResponse:
    """A TV-browser player for a valid playlist token, not a MAG/Stalker portal."""
    await active_subscriber(token, session)
    return FileResponse(STATIC_DIRECTORY / "watch.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/stalker/{token}/c/", include_in_schema=False)
@app.get("/stalker/{token}/c/index.html", include_in_schema=False)
async def subscriber_mag_portal(token: str, session: DatabaseSession) -> FileResponse:
    """External MAG web portal; requires gSTB and a matching recorded MAC at startup."""
    await active_subscriber(token, session)
    return FileResponse(STATIC_DIRECTORY / "mag.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/c/", include_in_schema=False)
@app.get("/c/index.html", include_in_schema=False)
@app.get("/stalker_portal/c/", include_in_schema=False)
@app.get("/stalker_portal/c/index.html", include_in_schema=False)
async def shared_mag_portal() -> FileResponse:
    """Common MAG web page. No catalog access until the reported MAC handshakes."""
    if not settings.enable_mac_stalker_portal:
        raise HTTPException(status_code=404, detail="Portal not enabled")
    return FileResponse(STATIC_DIRECTORY / "mag.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/health", tags=["system"])
async def health(session: DatabaseSession) -> dict[str, str]:
    # Select every subscriber column, as the customer list does. Selecting only
    # the ID misses schema drift (create_all does not add missing columns).
    try:
        await session.scalar(select(Subscriber).limit(1))
    except SQLAlchemyError:
        logger.exception("Customer database readiness check failed")
        raise HTTPException(status_code=503, detail="Customer database unavailable") from None
    return {"status": "ok", "mode": "demo" if settings.demo_mode else "standard"}


def _form_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


async def _parse_import_request(request: Request) -> tuple[ImportM3URequest, UploadFile | None]:
    content_type = request.headers.get("content-type", "").casefold()
    upload: UploadFile | None = None
    try:
        if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            form = await request.form()
            candidate = form.get("file")
            upload = candidate if isinstance(candidate, UploadFile) else None
            if upload is not None and any(
                form.get(field_name) for field_name in ("raw_m3u", "file_path", "source_url")
            ):
                raise ValueError(
                    "Provide the uploaded file alone; do not combine it with raw_m3u, file_path, or source_url"
                )
            payload = ImportM3URequest(
                raw_m3u=str(form["raw_m3u"]) if form.get("raw_m3u") else None,
                file_path=str(form["file_path"]) if form.get("file_path") else None,
                source_url=str(form["source_url"]) if form.get("source_url") else None,
                source_name=str(form.get("source_name") or "user-import"),
                default_language_code=str(form.get("default_language_code") or "und"),
                default_country_code=str(form.get("default_country_code") or "ZZ"),
                default_category=str(form.get("default_category") or "Uncategorized"),
                validate_urls=_form_bool(form.get("validate_urls")),
            ) if upload is None else ImportM3URequest.model_construct(
                source_name=str(form.get("source_name") or upload.filename or "uploaded-file"),
                default_language_code=str(form.get("default_language_code") or "und"),
                default_country_code=str(form.get("default_country_code") or "ZZ"),
                default_category=str(form.get("default_category") or "Uncategorized"),
                validate_urls=_form_bool(form.get("validate_urls")),
                raw_m3u=None,
                file_path=None,
                source_url=None,
            )
        elif "application/json" in content_type:
            payload = ImportM3URequest.model_validate(await request.json())
        else:
            body = await request.body()
            if len(body) > settings.max_import_bytes:
                raise HTTPException(status_code=413, detail="Import body exceeds configured size limit")
            payload = ImportM3URequest(raw_m3u=body.decode("utf-8-sig", errors="replace"))
    except (ValidationError, ValueError) as exc:
        if upload is not None:
            await upload.close()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return payload, upload


def _resolve_server_file(file_path: str) -> Path:
    if not settings.allow_server_file_import:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Server-side file imports are disabled; upload the file or enable ALLOW_SERVER_FILE_IMPORT",
        )
    try:
        candidate = Path(file_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=404, detail="Import file was not found") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail="Import path is not a file")
    if settings.allowed_import_root is not None:
        allowed_root = settings.allowed_import_root.expanduser().resolve()
        if candidate != allowed_root and allowed_root not in candidate.parents:
            raise HTTPException(status_code=403, detail="Import path is outside ALLOWED_IMPORT_ROOT")
    if candidate.stat().st_size > settings.max_import_bytes:
        raise HTTPException(status_code=413, detail="Import file exceeds configured size limit")
    return candidate


async def _save_upload_temporarily(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "playlist.m3u").suffix or ".m3u"
    total = 0
    temporary = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    path = Path(temporary.name)
    try:
        with temporary:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_import_bytes:
                    raise HTTPException(status_code=413, detail="Uploaded file exceeds configured size limit")
                temporary.write(chunk)
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


@app.post("/import-m3u", response_model=ImportResponse, tags=["channels"])
async def import_m3u(request: Request, session: DatabaseSession) -> ImportResponse:
    """Import an uploaded file, pasted M3U body, permitted server file, or remote M3U URL."""
    payload, upload = await _parse_import_request(request)
    temporary_path: Path | None = None

    async with ChannelScraper(
        validation_concurrency=settings.stream_validation_concurrency,
        playback_origin=settings.stream_validation_origin,
    ) as scraper:
        try:
            if upload is not None:
                temporary_path = await _save_upload_temporarily(upload)
                parsed = scraper.load_from_m3u_file(
                    temporary_path,
                    source_name=payload.source_name,
                    default_language_code=payload.default_language_code,
                    default_country_code=payload.default_country_code,
                    default_category=payload.default_category,
                )
            elif payload.raw_m3u is not None:
                if len(payload.raw_m3u.encode("utf-8")) > settings.max_import_bytes:
                    raise HTTPException(status_code=413, detail="M3U text exceeds configured size limit")
                parsed = scraper.load_from_m3u_text(
                    payload.raw_m3u,
                    source_name=payload.source_name,
                    default_language_code=payload.default_language_code,
                    default_country_code=payload.default_country_code,
                    default_category=payload.default_category,
                )
            elif payload.file_path is not None:
                parsed = scraper.load_from_m3u_file(
                    _resolve_server_file(payload.file_path),
                    source_name=payload.source_name,
                    default_language_code=payload.default_language_code,
                    default_country_code=payload.default_country_code,
                    default_category=payload.default_category,
                )
            elif payload.source_url is not None:
                parsed = await scraper.load_from_url(
                    payload.source_url,
                    source_name=payload.source_name,
                    default_language_code=payload.default_language_code,
                    default_country_code=payload.default_country_code,
                    default_category=payload.default_category,
                    max_bytes=settings.max_import_bytes,
                )
            else:  # Defensive: request validation should make this unreachable.
                raise HTTPException(status_code=422, detail="No import source provided")

            parsed_count = len(parsed.records)
            if payload.validate_urls and parsed.records:
                valid_records, validation_failures = await scraper.validate_records(
                    parsed.records,
                    timeout_seconds=settings.stream_validation_timeout_seconds,
                )
                parsed.records = valid_records
                parsed.failures.extend(validation_failures)

            persistence = await scraper.persist_channels(
                session,
                parsed.records,
                batch_size=settings.import_batch_size,
            )
            parsed.failures.extend(persistence.failures)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            if upload is not None:
                await upload.close()

    await cache.delete_pattern("channels:*")
    failure_count = len(parsed.failures)
    visible_failures = parsed.failures[: settings.max_failure_details]
    return ImportResponse(
        source_name=payload.source_name,
        parsed_count=parsed_count,
        imported_count=persistence.inserted_count + persistence.updated_count,
        inserted_count=persistence.inserted_count,
        updated_count=persistence.updated_count,
        deactivated_count=0,
        failure_count=failure_count,
        failures_truncated=failure_count > len(visible_failures),
        failures=[
            ImportFailure(
                line_number=item.line_number,
                name=item.name,
                stream_url=item.stream_url,
                reason=item.reason,
            )
            for item in visible_failures
        ],
    )


async def _source_response(
    session: AsyncSession,
    source: PlaylistSource,
    active_count: int | None = None,
) -> PlaylistSourceResponse:
    if active_count is None:
        active_count = await session.scalar(
            select(func.count(Channel.id)).where(
                Channel.source_name == source.name,
                Channel.is_active.is_(True),
            )
        ) or 0
    return PlaylistSourceResponse(
        id=source.id,
        name=source.name,
        url_preview=redact_url(source.playlist_url),
        default_language_code=source.default_language_code,
        default_country_code=source.default_country_code,
        default_category=source.default_category,
        validate_urls=source.validate_urls,
        replace_missing=source.replace_missing,
        enabled=source.enabled,
        last_sync_status=source.last_sync_status,
        last_synced_at=source.last_synced_at,
        last_parsed_count=source.last_parsed_count,
        last_imported_count=source.last_imported_count,
        last_failure_count=source.last_failure_count,
        last_error=source.last_error,
        active_channel_count=active_count,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


@app.get("/sources", response_model=list[PlaylistSourceResponse], tags=["sources"])
async def get_sources(session: DatabaseSession) -> list[PlaylistSourceResponse]:
    sources = (
        await session.scalars(select(PlaylistSource).order_by(PlaylistSource.name.asc()))
    ).all()
    if not sources:
        return []
    names = [source.name for source in sources]
    count_rows = (
        await session.execute(
            select(Channel.source_name, func.count(Channel.id))
            .where(Channel.source_name.in_(names), Channel.is_active.is_(True))
            .group_by(Channel.source_name)
        )
    ).all()
    counts = {name: count for name, count in count_rows}
    return [
        await _source_response(session, source, counts.get(source.name, 0))
        for source in sources
    ]


@app.post(
    "/sources",
    response_model=PlaylistSourceResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["sources"],
)
async def create_source(
    payload: PlaylistSourceCreate,
    session: DatabaseSession,
) -> PlaylistSourceResponse:
    used_name = await session.scalar(
        select(Channel.id).where(Channel.source_name == payload.name).limit(1)
    )
    if used_name is not None:
        raise HTTPException(
            status_code=409,
            detail="Source name is already used by catalog records; choose a unique provider name",
        )
    source = PlaylistSource(
        name=payload.name,
        playlist_url=payload.playlist_url,
        default_language_code=normalize_language(payload.default_language_code),
        default_country_code=normalize_country(payload.default_country_code),
        default_category=payload.default_category.strip() or "Uncategorized",
        validate_urls=payload.validate_urls,
        replace_missing=payload.replace_missing,
    )
    session.add(source)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A source with this name already exists") from exc
    await session.refresh(source)
    return await _source_response(session, source, 0)


@app.post(
    "/sources/{source_id}/sync",
    response_model=PlaylistSourceSyncResponse,
    tags=["sources"],
)
async def sync_source(source_id: int, session: DatabaseSession) -> PlaylistSourceSyncResponse:
    source = await session.get(PlaylistSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Playlist source was not found")
    result = await sync_playlist_source(session, source)
    await cache.delete_pattern("channels:*")
    failure_count = len(result.failures)
    visible_failures = result.failures[: settings.max_failure_details]
    return PlaylistSourceSyncResponse(
        source=await _source_response(session, source),
        result=ImportResponse(
            source_name=source.name,
            parsed_count=result.parsed_count,
            imported_count=result.imported_count,
            inserted_count=result.inserted_count,
            updated_count=result.updated_count,
            deactivated_count=result.deactivated_count,
            failure_count=failure_count,
            failures_truncated=failure_count > len(visible_failures),
            failures=[
                ImportFailure(
                    line_number=item.line_number,
                    name=item.name,
                    stream_url=item.stream_url,
                    reason=item.reason,
                )
                for item in visible_failures
            ],
        ),
    )


@app.delete("/sources/{source_id}", tags=["sources"])
async def delete_source(source_id: int, session: DatabaseSession) -> dict[str, int | str]:
    source = await session.get(PlaylistSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Playlist source was not found")
    deactivated_count = await session.scalar(
        select(func.count(Channel.id)).where(
            Channel.source_name == source.name,
            Channel.is_active.is_(True),
        )
    ) or 0
    await session.execute(
        update(Channel)
        .where(Channel.source_name == source.name)
        .values(is_active=False)
    )
    await session.execute(delete(PlaylistSource).where(PlaylistSource.id == source.id))
    await session.commit()
    await cache.delete_pattern("channels:*")
    return {
        "source_name": source.name,
        "deactivated_count": deactivated_count,
        "status": "deleted",
    }


@app.get("/channels", response_model=ChannelPage, tags=["channels"])
async def get_channels(
    session: DatabaseSession,
    lang: Annotated[str | None, Query(max_length=16)] = None,
    country: Annotated[str | None, Query(max_length=8)] = None,
    category: Annotated[str | None, Query(max_length=128)] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    active_only: bool = True,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=500)] = 100,
    sort_by: ChannelSortField = "name",
    sort_direction: SortDirection = "asc",
) -> ChannelPage:
    normalized_lang = lang.casefold() if lang else None
    normalized_country = country.upper() if country else None
    cache_key = (
        f"channels:{normalized_lang}:{normalized_country}:{category}:{search}:{active_only}:"
        f"{page}:{page_size}:{sort_by}:{sort_direction}"
    )
    cached = await cache.get_json(cache_key)
    if cached is not None:
        return ChannelPage.model_validate(cached)

    filters = []
    if normalized_lang:
        filters.append(Channel.language_code == normalized_lang)
    if normalized_country:
        filters.append(Channel.country_code == normalized_country)
    if category:
        filters.append(_live_category_filter(category.strip()))
    if search:
        term = f"%{search.strip()}%"
        filters.append(or_(Channel.name.ilike(term), Channel.tvg_id.ilike(term)))
    if active_only:
        filters.append(Channel.is_active.is_(True))

    total = await session.scalar(select(func.count(Channel.id)).where(*filters)) or 0
    sort_columns = {
        "name": Channel.normalized_name,
        "language": Channel.language_code,
        "country": Channel.country_code,
        "category": Channel.category,
        "created_at": Channel.created_at,
    }
    order = asc(sort_columns[sort_by]) if sort_direction == "asc" else desc(sort_columns[sort_by])
    rows = (
        await session.scalars(
            select(Channel)
            .where(*filters)
            .order_by(order, Channel.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    alternatives: dict[str, list[str]] = {}
    channel_keys = {row.channel_key for row in rows}
    if channel_keys:
        alternative_rows = (
            await session.execute(
                select(Channel.channel_key, Channel.stream_url)
                .where(Channel.channel_key.in_(channel_keys), Channel.is_active.is_(True))
                .order_by(Channel.id.asc())
            )
        ).all()
        for channel_key, stream_url in alternative_rows:
            alternatives.setdefault(channel_key, []).append(stream_url)

    response = ChannelPage(
        page=page,
        page_size=page_size,
        total=total,
        items=[
            ChannelResponse(
                id=row.id,
                name=row.name,
                logo=row.logo_url,
                stream_url=row.stream_url,
                alternative_stream_urls=[
                    url for url in alternatives.get(row.channel_key, []) if url != row.stream_url
                ][:5],
                language_code=row.language_code,
                country_code=row.country_code,
                category=row.category,
                tvg_id=row.tvg_id,
                source_name=row.source_name,
                is_active=row.is_active,
                last_checked_at=row.last_checked_at,
                metadata=row.extra_metadata,
            )
            for row in rows
        ],
    )
    await cache.set_json(
        cache_key,
        response.model_dump(mode="json"),
        settings.channel_cache_ttl_seconds,
    )
    return response


@app.get("/playlist.m3u", tags=["channels"])
async def export_live_playlist(
    session: DatabaseSession,
    lang: Annotated[str | None, Query(max_length=16)] = None,
    country: Annotated[str | None, Query(max_length=8)] = None,
    category: Annotated[str | None, Query(max_length=128)] = None,
) -> Response:
    """Export active channel URLs for TV apps that can open an M3U playlist."""
    return await _live_playlist_response(session, lang=lang, country=country, category=category)


async def _live_playlist_response(
    session: AsyncSession, *, lang: str | None = None,
    country: str | None = None, category: str | None = None, include_vod: bool = False,
) -> Response:
    """Return direct upstream URLs; this is not an authenticated stream proxy."""
    filters = [Channel.is_active.is_(True)]
    if lang:
        filters.append(Channel.language_code == lang.casefold())
    if country:
        filters.append(Channel.country_code == country.upper())
    if category:
        filters.append(_live_category_filter(category.strip()))
    rows = (
        await session.execute(
            select(Channel.name, Channel.tvg_id, Channel.logo_url, Channel.category, Channel.stream_url)
            .where(*filters)
            .order_by(Channel.normalized_name, Channel.id)
        )
    ).all()

    def clean(value: str | None) -> str:
        return (value or "").replace("\r", " ").replace("\n", " ").replace('"', "'").strip()

    playlist = ["#EXTM3U"]
    for name, tvg_id, logo, groups, stream_url in rows:
        # IPTV apps generally treat group-title as a single folder, so publish
        # one entry in each group rather than a literal "Animation;Kids" folder.
        for group in dict.fromkeys(part.strip() for part in (groups or "Uncategorized").split(";") if part.strip()):
            playlist.append(
                f'#EXTINF:-1 tvg-id="{clean(tvg_id)}" tvg-logo="{clean(logo)}" '
                f'group-title="{clean(group)}",{clean(name)}'
            )
            playlist.append(stream_url)
    if include_vod:
        vod_rows = (await session.execute(
            select(Vod.title, Vod.media_type, Vod.stream_url)
            .where(Vod.is_active.is_(True))
            .order_by(Vod.normalized_title, Vod.id)
        )).all()
        for title, media_type, stream_url in vod_rows:
            group = "Movies" if media_type == "movie" else "TV Shows"
            playlist.append(f'#EXTINF:-1 group-title="{group}",{clean(title)}')
            playlist.append(stream_url)
    return Response(
        content="\n".join(playlist) + "\n",
        media_type="application/x-mpegURL",
        headers={"Content-Disposition": 'attachment; filename="nexastream-live.m3u"',
                 "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@app.get("/subscribers/playlist/{token}.m3u", tags=["subscribers"])
async def subscriber_playlist(token: str, session: DatabaseSession) -> Response:
    """Authorize each playlist fetch, not the direct upstream media URLs it contains."""
    await active_subscriber(token, session)
    return await _live_playlist_response(session, include_vod=True)


async def active_subscriber(token: str, session: AsyncSession) -> Subscriber:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise HTTPException(status_code=404, detail="Playlist not found")
    subscriber = await session.scalar(select(Subscriber).where(Subscriber.token_hash == token_hash(token)))
    if subscriber is None or not subscriber.is_active or utc_datetime(subscriber.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail="Playlist not found")
    return subscriber


async def _stalker_params(request: Request) -> dict[str, str]:
    params = dict(request.query_params)
    if request.method == "POST":
        form = await request.form()
        params.update({key: str(value) for key, value in form.items() if isinstance(value, str)})
    request.state.stalker_action = params.get("action", "")
    request.state.stalker_type = params.get("type", "")
    return params


def _request_mac(request: Request, params: dict[str, str]) -> str | None:
    # Reject conflicting identities rather than accepting whichever field came first.
    # Stalker clients often percent-encode colons in the Cookie header.
    cookie_mac = request.cookies.get("mac")
    values = [params.get("mac"), unquote(cookie_mac) if cookie_mac else None,
              request.headers.get("x-device-mac")]
    supplied = [value for value in values if value]
    macs = [normalized_mac(value) for value in supplied]
    return macs[0] if macs and all(value == macs[0] for value in macs) else None


async def _stalker_genres(session: AsyncSession) -> dict[str, str]:
    categories = (await session.scalars(
        select(Channel.category).where(Channel.is_active.is_(True)).distinct()
    )).all()
    groups = sorted({part.strip() for category in categories
                     for part in (category or "Uncategorized").split(";") if part.strip()})
    # Stable numeric IDs match the genre references on channels, even after imports.
    ids: dict[str, str] = {}
    used: set[str] = set()
    for group in groups:
        candidate = int.from_bytes(hashlib.sha256(group.encode("utf-8")).digest()[:4], "big") & 0x7FFFFFFF
        genre_id = str(candidate or 1)
        while genre_id in used:
            genre_id = str((int(genre_id) % 0x7FFFFFFF) + 1)
        ids[group] = genre_id
        used.add(genre_id)
    return ids


@app.api_route("/portal.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/server/load.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/server/portal.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/stalker_portal/portal.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/stalker_portal/server/load.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/stalker_portal/server/portal.php", methods=["GET", "POST"], include_in_schema=False)
async def mac_stalker_api(request: Request, session: DatabaseSession) -> Response:
    """Opt-in shared Stalker entry point for players configured with server + MAC."""
    if not settings.enable_mac_stalker_portal:
        raise HTTPException(status_code=404, detail="Portal not enabled")
    params = await _stalker_params(request)
    mac = _request_mac(request, params)
    if not mac:
        raise HTTPException(status_code=403, detail="A registered MAC is required")
    # A duplicate registered MAC must never arbitrarily select another account.
    matches = (await session.scalars(select(Subscriber).where(Subscriber.mac_address == mac).limit(2))).all()
    if len(matches) != 1:
        raise HTTPException(status_code=403, detail="MAC is not uniquely registered")
    subscriber = matches[0]
    if not subscriber.is_active or utc_datetime(subscriber.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="Account inactive or expired")
    return await _stalker_response(subscriber, request, session, params)


@app.api_route("/stalker/{token}/portal.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/stalker/{token}/stalker_portal/portal.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/stalker/{token}/stalker_portal/server/load.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/stalker/{token}/stalker_portal/server/portal.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/stalker/{token}/server/load.php", methods=["GET", "POST"], include_in_schema=False)
@app.api_route("/stalker/{token}/server/portal.php", methods=["GET", "POST"], include_in_schema=False)
async def stalker_api(token: str, request: Request, session: DatabaseSession) -> Response:
    """Private Stalker entry points for players configured with a tokenized server + MAC."""
    subscriber = await active_subscriber(token, session)
    return await _stalker_response(subscriber, request, session, await _stalker_params(request))


async def _stalker_response(subscriber: Subscriber, request: Request, session: AsyncSession,
                            params: dict[str, str]) -> Response:
    action = params.get("action", "")
    mac = matching_mac(subscriber, _request_mac(request, params))
    if mac is None:
        raise HTTPException(status_code=403, detail="A matching registered MAG MAC is required")
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    if action == "handshake" and params.get("type") == "stb":
        return Response(content=json.dumps({"js": {"token": issue_session(subscriber, mac)}}),
                        media_type="application/json", headers=headers)

    authorization = request.headers.get("authorization", "")
    bearer = authorization[7:] if authorization.lower().startswith("bearer ") else None
    # Native Stalker clients may forward the issued token in a cookie rather
    # than an Authorization header. Reject conflicting session identities.
    param_token = params.get("token")
    cookie_token = request.cookies.get("token")
    supplied_tokens = [token for token in (bearer, param_token, cookie_token) if token]
    if supplied_tokens and any(not secrets.compare_digest(supplied_tokens[0], token)
                               for token in supplied_tokens[1:]):
        raise HTTPException(status_code=403, detail="Conflicting MAG session tokens")
    bearer = supplied_tokens[0] if supplied_tokens else None
    if not valid_session(subscriber, mac, bearer):
        raise HTTPException(status_code=403, detail="MAG session required; handshake again")
    if (params.get("type"), action) not in {
        ("stb", "get_profile"), ("stb", "get_modules"), ("stb", "get_localization"),
        ("stb", "get_time"), ("stb", "do_auth"), ("account_info", "get_main_info"),
        ("itv", "get_genres"), ("itv", "get_categories"), ("itv", "get_all_channels"),
        ("itv", "get_ordered_list"), ("itv", "create_link"),
        ("itv", "get_short_epg"), ("itv", "get_epg_info"),
        ("vod", "get_categories"), ("vod", "get_ordered_list"), ("vod", "create_link"),
        ("series", "get_categories"), ("series", "get_ordered_list"), ("series", "create_link"),
        ("radio", "get_categories"), ("radio", "get_ordered_list"),
        ("watchdog", "get_events"),
    }:
        raise HTTPException(status_code=400, detail="Unsupported MAG action")

    data: Any
    if action == "get_profile":
        # Native Stalker clients may refresh their bearer from get_profile.
        # Return the validated session, not a new identity or an anonymous token.
        data = {"id": subscriber.id, "name": subscriber.name, "mac": mac, "status": 0,
                "blocked": "0", "token": bearer}
    elif action == "get_modules":
        # Advertise only catalog modules we actually serve. Stalker clients
        # inspect these keys during startup before loading content.
        data = {"all_modules": ["tv", "vclub", "sclub", "account"],
                "switchable_modules": [], "disabled_modules": [],
                "restricted_modules": [], "template": "default"}
    elif action == "get_localization":
        data = {"time_format": "{0}:{1}"}
    elif action == "get_time":
        data = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}
    elif action == "do_auth":
        # This MAC-only portal has no subscriber login/password credentials.
        # An explicit password must not be silently treated as authenticated.
        if params.get("login") or params.get("password"):
            raise HTTPException(status_code=403, detail="Account credentials are not supported")
        data = True
    elif action == "get_events":
        data = {"data": {"msgs": 0, "additional_services_on": 0}}
    elif action == "get_short_epg":
        data = {"data": []}
    elif action == "get_epg_info":
        data = {"data": {}}
    elif action == "get_main_info":
        data = {"status": "active", "end_date": utc_datetime(subscriber.expires_at).isoformat()}
    elif params["type"] == "itv" and action in ("get_genres", "get_categories"):
        group_ids = await _stalker_genres(session)
        data = [{"id": "*", "title": "All channels", "alias": "all", "censored": "0"}] + [
            {"id": genre_id, "title": group, "alias": group, "censored": "0"}
            for group, genre_id in group_ids.items()
        ]
    elif params["type"] == "vod" and action == "get_categories":
        data = [{"id": "*", "title": "All movies", "alias": "all"}]
    elif params["type"] == "series" and action == "get_categories":
        # Direct-stream TV imports are episodes; there is no season/show hierarchy.
        data = ([{"id": "*", "title": "TV episodes", "alias": "all"}]
                if await session.scalar(select(func.count(Vod.id)).where(
                    Vod.is_active.is_(True), Vod.media_type == "tv")) else [])
    elif params["type"] == "radio":
        # No radio catalog is imported; return valid empty shapes, not 400s.
        data = [] if action == "get_categories" else {
            "data": [], "total_items": 0, "max_page_items": 50, "cur_page": 1}
    elif action in ("get_all_channels", "get_ordered_list"):
        if params["type"] in {"vod", "series"}:
            try:
                page = int(params.get("p", "1"))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid page") from None
            if not 0 <= page <= 100000:
                raise HTTPException(status_code=400, detail="Invalid page")
            page = max(1, page)
            filters = [Vod.is_active.is_(True), Vod.media_type == ("movie" if params["type"] == "vod" else "tv")]
            rows = (await session.execute(
                select(Vod.id, Vod.title, Vod.poster_path).where(*filters)
                .order_by(Vod.normalized_title, Vod.id).limit(50).offset((page - 1) * 50)
            )).all()
            data = {"data": [{"id": str(vod_id), "name": title, "title": title,
                              "screenshot_uri": poster or "", "cmd": f"ffmpeg vod_{vod_id}"}
                             for vod_id, title, poster in rows],
                    "total_items": await session.scalar(select(func.count(Vod.id)).where(*filters)) or 0,
                    "max_page_items": 50, "cur_page": page}
            return Response(content=json.dumps({"js": data}), media_type="application/json", headers=headers)
        group_ids = await _stalker_genres(session)
        filters = [Channel.is_active.is_(True)]
        genre = params.get("genre", params.get("category", "*"))
        if action == "get_ordered_list" and genre not in {"*", "all", ""}:
            category = next((name for name, genre_id in group_ids.items() if genre_id == genre), genre)
            filters.append(_live_category_filter(category))
        else:
            category = None
        query = select(Channel.id, Channel.name, Channel.logo_url, Channel.category, Channel.tvg_id).where(*filters).order_by(Channel.normalized_name, Channel.id)
        if action == "get_ordered_list":
            try:
                page = int(params.get("p", "1"))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid page") from None
            if not 0 <= page <= 100000:
                raise HTTPException(status_code=400, detail="Invalid page")
            # Some Stalker clients request p=0 for the first page.
            page = max(1, page)
            query = query.limit(50).offset((page - 1) * 50)
        rows = (await session.execute(query)).all()
        channels = []
        for index, (channel_id, name, logo, channel_category, tvg_id) in enumerate(
            rows, start=(page - 1) * 50 if action == "get_ordered_list" else 0
        ):
            selected_group = category or (channel_category or "Uncategorized").split(";")[0].strip()
            channels.append({"id": str(channel_id), "name": name, "number": str(index + 1),
                             "logo": logo or "", "cmd": f"ffmpeg channel_{channel_id}",
                             "use_http_tmp_link": "1", "xmltv_id": tvg_id or "", "tv_archive_duration": 0,
                             "tv_genre_id": group_ids.get(selected_group, "*")})
        if action == "get_ordered_list":
            data = {"data": channels, "total_items": await session.scalar(select(func.count(Channel.id)).where(*filters)) or 0,
                    "max_page_items": 50, "cur_page": page}
        else:
            # Both js.data and js.channels are used for get_all_channels by
            # Stalker clients. Keep the existing data shape for older clients.
            data = {"data": channels, "channels": channels, "total_items": len(channels)}
    elif action == "create_link":
        if params["type"] in {"vod", "series"}:
            match = re.fullmatch(r"(?:ffmpeg )?vod_([1-9][0-9]{0,18})", params.get("cmd", ""))
            if not match:
                raise HTTPException(status_code=400, detail="Invalid title command")
            movie = await session.get(Vod, int(match.group(1)))
            if movie is None or not movie.is_active or movie.media_type != ("movie" if params["type"] == "vod" else "tv"):
                raise HTTPException(status_code=404, detail="Title not found")
            return Response(content=json.dumps({"js": {"cmd": f"ffmpeg {movie.stream_url}"}}),
                            media_type="application/json", headers=headers)
        match = re.fullmatch(r"(?:ffmpeg )?channel_([1-9][0-9]{0,18})", params.get("cmd", ""))
        if not match:
            raise HTTPException(status_code=400, detail="Invalid channel command")
        channel = await session.get(Channel, int(match.group(1)))
        if channel is None or not channel.is_active:
            raise HTTPException(status_code=404, detail="Channel not found")
        data = {"cmd": f"ffmpeg {channel.stream_url}"}
    else:
        raise HTTPException(status_code=400, detail="Unsupported MAG action")
    return Response(content=json.dumps({"js": data}), media_type="application/json", headers=headers)


@app.get("/admin/subscribers", response_model=list[SubscriberResponse], tags=["admin"])
async def list_subscribers(session: DatabaseSession, _: AdminAccess) -> list[Subscriber]:
    try:
        return list((await session.scalars(select(Subscriber).order_by(Subscriber.id.desc()))).all())
    except SQLAlchemyError:
        logger.exception("Customer list database query failed")
        raise HTTPException(status_code=503, detail="Customer database unavailable") from None


@app.post("/admin/subscribers/portal-check", response_model=PortalCheckResponse, tags=["admin"])
async def check_subscriber_portal(payload: PortalCheckRequest, response: Response, session: DatabaseSession,
                                  _: AdminAccess) -> PortalCheckResponse:
    """Read-only: resolve an existing private link without replacing its credential."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    try:
        row = await session.scalar(select(Subscriber).where(Subscriber.token_hash == token_hash(payload.token)))
    except SQLAlchemyError:
        logger.exception("Customer portal check database query failed")
        raise HTTPException(status_code=503, detail="Customer database unavailable") from None
    if row is None:
        raise HTTPException(status_code=404, detail="Private portal URL not found")
    return PortalCheckResponse(id=row.id, name=row.name, is_active=row.is_active,
                               expires_at=utc_datetime(row.expires_at),
                               mac_matches=matching_mac(row, payload.mac_address) is not None,
                               portal_origin=public_base_url())


@app.post("/admin/subscribers", response_model=SubscriberCreated, status_code=201, tags=["admin"])
async def create_subscriber(payload: SubscriberCreate, session: DatabaseSession, _: AdminAccess) -> SubscriberCreated:
    token = new_token()
    row = Subscriber(name=payload.name, mac_address=payload.mac_address, notes=payload.notes,
                     token_hash=token_hash(token), expires_at=add_months(datetime.now(timezone.utc), payload.months),
                     is_active=payload.is_active)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return SubscriberCreated(**SubscriberResponse.model_validate(row).model_dump(),
                             playlist_url=subscriber_link(token), portal_url=subscriber_portal_link(token),
                             mag_portal_url=subscriber_mag_link(token))


@app.patch("/admin/subscribers/{subscriber_id}", response_model=SubscriberResponse, tags=["admin"])
async def update_subscriber(subscriber_id: int, payload: SubscriberUpdate, session: DatabaseSession, _: AdminAccess) -> Subscriber:
    row = await session.get(Subscriber, subscriber_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    if payload.is_active is None and payload.extend_months is None and "mac_address" not in payload.model_fields_set:
        raise HTTPException(status_code=422, detail="Provide a status, MAC, or extension")
    if "mac_address" in payload.model_fields_set:
        row.mac_address = payload.mac_address
    if payload.is_active is not None:
        row.is_active = payload.is_active
    if payload.extend_months is not None:
        now = datetime.now(timezone.utc)
        row.expires_at = add_months(max(utc_datetime(row.expires_at), now), payload.extend_months)
    await session.commit()
    await session.refresh(row)
    return row


@app.post("/admin/subscribers/{subscriber_id}/rotate", response_model=SubscriberRotated, tags=["admin"])
async def rotate_subscriber_token(subscriber_id: int, session: DatabaseSession, _: AdminAccess) -> SubscriberRotated:
    row = await session.get(Subscriber, subscriber_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Subscriber not found")
    token = new_token()
    row.token_hash = token_hash(token)
    await session.commit()
    return SubscriberRotated(playlist_url=subscriber_link(token), portal_url=subscriber_portal_link(token),
                             mag_portal_url=subscriber_mag_link(token))


@app.post(
    "/channels/{channel_id}/playback-sources",
    response_model=ChannelPlaybackSourcesResponse,
    tags=["channels"],
)
async def validate_channel_playback_sources(
    channel_id: int,
    session: DatabaseSession,
) -> ChannelPlaybackSourcesResponse:
    """Recheck matching HLS sources immediately before browser playback."""
    selected = await session.get(Channel, channel_id)
    if selected is None or not selected.is_active:
        raise HTTPException(status_code=404, detail="Channel was not found or is no longer active")

    candidates = (
        await session.scalars(
            select(Channel)
            .where(Channel.channel_key == selected.channel_key, Channel.is_active.is_(True))
            .order_by(Channel.id.asc())
        )
    ).all()
    candidates.sort(key=lambda item: item.id != selected.id)
    records = [
        ScrapedChannel(
            name=item.name,
            stream_url=item.stream_url,
            logo=item.logo_url,
            language_code=item.language_code,
            country_code=item.country_code,
            category=item.category,
            tvg_id=item.tvg_id,
            source_name=item.source_name,
            metadata=dict(item.extra_metadata or {}),
        )
        for item in candidates
    ]

    async with ChannelScraper(
        validation_concurrency=min(settings.stream_validation_concurrency, max(len(records), 1)),
        playback_origin=settings.stream_validation_origin,
    ) as scraper:
        valid_records, failures = await scraper.validate_records(
            records,
            timeout_seconds=settings.stream_validation_timeout_seconds,
        )

    valid_urls = {record.stream_url for record in valid_records}
    for channel, record in zip(candidates, records, strict=True):
        channel.last_checked_at = record.last_checked_at
        channel.extra_metadata = record.metadata
        channel.is_active = channel.stream_url in valid_urls
    await session.commit()
    await cache.delete_pattern("channels:*")

    sources = [record.stream_url for record in valid_records]
    checked_at = max(record.last_checked_at for record in records if record.last_checked_at is not None)
    if not sources:
        raise HTTPException(
            status_code=409,
            detail="No matching source is delivering browser-playable live media right now",
        )
    return ChannelPlaybackSourcesResponse(
        channel_id=channel_id,
        checked_at=checked_at,
        sources=sources[:6],
        rejected_count=len(failures),
    )


@app.post("/channels/{channel_id}/playback-failure", tags=["channels"])
async def report_channel_playback_failure(
    channel_id: int,
    payload: ChannelPlaybackFailureRequest,
    session: DatabaseSession,
) -> dict[str, int | str]:
    """Deactivate one matching source after the browser fails to produce video."""
    selected = await session.get(Channel, channel_id)
    if selected is None:
        raise HTTPException(status_code=404, detail="Channel was not found")
    failed = await session.scalar(
        select(Channel).where(
            Channel.channel_key == selected.channel_key,
            Channel.stream_url == payload.stream_url,
        )
    )
    if failed is None:
        raise HTTPException(status_code=404, detail="Matching playback source was not found")

    failed.is_active = False
    metadata = dict(failed.extra_metadata or {})
    health = dict(metadata.get("playback_health") or {})
    health.update(
        {
            "browser_playable": False,
            "error": payload.reason,
            "browser_failure_reported_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    metadata["playback_health"] = health
    failed.extra_metadata = metadata
    await session.commit()
    await cache.delete_pattern("channels:*")
    return {"channel_id": failed.id, "status": "deactivated"}


async def _facet_items(
    session: AsyncSession,
    column: Any,
    *filters: Any,
) -> list[FacetItem]:
    rows = (
        await session.execute(
            select(column, func.count())
            .where(*filters)
            .group_by(column)
            .order_by(func.count().desc(), column.asc())
        )
    ).all()
    return [
        FacetItem(value=str(value), count=count)
        for value, count in rows
        if value is not None and str(value).strip()
    ]


@app.get("/catalog/facets", response_model=CatalogFacets, tags=["catalog"])
async def catalog_facets(
    session: DatabaseSession,
    country: Annotated[str | None, Query(max_length=8)] = None,
) -> CatalogFacets:
    """Return live group counts, optionally scoped to a country, plus VOD totals."""
    live_filter = Channel.is_active.is_(True)
    live_filters = [live_filter]
    if country:
        live_filters.append(Channel.country_code == country.upper())
    vod_filter = Vod.is_active.is_(True)
    live_total = await session.scalar(select(func.count(Channel.id)).where(*live_filters)) or 0
    vod_total = await session.scalar(select(func.count(Vod.id)).where(vod_filter)) or 0

    category_rows = (
        await session.execute(
            select(Channel.category, func.count()).where(*live_filters).group_by(Channel.category)
        )
    ).all()
    category_counts: dict[str, int] = {}
    for group, count in category_rows:
        for label in set(part.strip() for part in (group or "").split(";")):
            if label:
                category_counts[label] = category_counts.get(label, 0) + count

    return CatalogFacets(
        demo_mode=settings.demo_mode,
        live=CatalogSectionFacets(
            total=live_total,
            languages=await _facet_items(session, Channel.language_code, *live_filters),
            countries=await _facet_items(session, Channel.country_code, *live_filters),
            categories=[
                FacetItem(value=label, count=count)
                for label, count in sorted(category_counts.items(), key=lambda pair: (-pair[1], pair[0]))
            ],
        ),
        vod=CatalogSectionFacets(
            total=vod_total,
            languages=await _facet_items(session, Vod.language_code, vod_filter),
            countries=await _facet_items(session, Vod.country_code, vod_filter),
            media_types=await _facet_items(session, Vod.media_type, vod_filter),
        ),
    )


@app.post("/vod/import", response_model=VodImportResponse, tags=["vod"])
async def import_vod(payload: VodImportRequest, session: DatabaseSession) -> VodImportResponse:
    """Index user-supplied direct, authorized VOD URLs; never scrape playback sites."""
    urls = [item.stream_url for item in payload.items]
    if len(urls) != len(set(urls)):
        raise HTTPException(status_code=422, detail="Duplicate stream URLs in the same import")
    existing = {
        vod.stream_url: vod
        for vod in (await session.scalars(select(Vod).where(Vod.stream_url.in_(urls)))).all()
    }
    inserted = 0
    for item in payload.items:
        vod = existing.get(item.stream_url)
        if vod is None:
            vod = Vod(stream_url=item.stream_url)
            session.add(vod)
            inserted += 1
        vod.title = item.title.strip()
        vod.normalized_title = normalize_name(vod.title)
        vod.media_type = item.media_type
        vod.language_code = normalize_language(item.language_code)
        vod.country_code = normalize_country(item.country_code)
        vod.release_year = item.release_year
        vod.poster_path = item.poster_path
        vod.synopsis = item.synopsis
        vod.source_name = payload.source_name.strip()
        vod.is_active = True
        vod.extra_metadata = {}
    await session.commit()
    return VodImportResponse(
        source_name=payload.source_name.strip(),
        inserted_count=inserted,
        updated_count=len(payload.items) - inserted,
    )


@app.post("/vod/vidsrc/sync", response_model=VidsrcSyncResponse, tags=["vod"])
async def sync_vidsrc(session: DatabaseSession) -> VidsrcSyncResponse:
    """Import VidSrc's advertised movie and show pages, without downloading video."""
    try:
        parsed, inserted, updated, total = await sync_vidsrc_catalog(session)
    except Exception as exc:
        logger.warning("VidSrc feed sync failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="VidSrc feeds could not be synchronized; existing entries were preserved") from exc
    return VidsrcSyncResponse(parsed_count=parsed, inserted_count=inserted, updated_count=updated, total=total)


@app.get("/vod/vidsrc/search", response_model=VidsrcTitlePage, tags=["vod"])
async def search_vidsrc(
    session: DatabaseSession,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    media_type: Annotated[str | None, Query(pattern="^(movie|tv)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 15,
) -> VidsrcTitlePage:
    filters = [VidsrcTitle.is_active.is_(True)]
    if q:
        query = normalize_name(q)
        name_match = VidsrcTitle.normalized_title.ilike(f"%{query}%")
        filters.append(or_(name_match, VidsrcTitle.imdb_id == query) if query.startswith("tt") and query[2:].isdigit() else name_match)
    if media_type:
        filters.append(VidsrcTitle.media_type == media_type)
    total = await session.scalar(select(func.count(VidsrcTitle.id)).where(*filters)) or 0
    rows = (await session.scalars(
        select(VidsrcTitle).where(*filters)
        .order_by(case((VidsrcTitle.title.like("Movie · tt%"), 1),
                       (VidsrcTitle.title.like("Tv · tt%"), 1), else_=0),
                  VidsrcTitle.normalized_title, VidsrcTitle.id)
        .offset((page - 1) * page_size).limit(page_size)
    )).all()
    return VidsrcTitlePage(page=page, page_size=page_size, total=total, items=[
        VidsrcTitleResponse(id=row.id, title=row.title, media_type=row.media_type, embed_url=row.embed_url)
        for row in rows
    ])


@app.post("/vod/vidsrc/resolve", response_model=list[VidsrcTitleResponse], tags=["vod"])
async def resolve_vidsrc(payload: VidsrcResolveRequest, session: DatabaseSession) -> list[VidsrcTitleResponse]:
    """Fetch display titles from VidSrc's info API for the visible page only."""
    rows = await resolve_vidsrc_titles(session, payload.ids)
    return [VidsrcTitleResponse(id=row.id, title=row.title, media_type=row.media_type, embed_url=row.embed_url)
            for row in rows]


@app.get("/vod/imdb/search", response_model=ImdbMoviePage, tags=["vod"])
async def search_imdb_movies(
    session: DatabaseSession,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    vidsrc_only: bool = True,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 15,
) -> ImdbMoviePage:
    """Browse a local IMDb movie subset; an embed means an ID match, not verified video."""
    matched_embed = (select(VidsrcTitle.embed_url).where(
        VidsrcTitle.imdb_id == ImdbMovie.imdb_id,
        VidsrcTitle.media_type == "movie",
        VidsrcTitle.is_active.is_(True),
    ).limit(1).correlate(ImdbMovie).scalar_subquery())
    filters = []
    if q:
        term = normalize_name(q)
        # Escape SQL wildcards so searches for titles containing % or _ are literal.
        # Exact IMDb IDs can use the primary-key index instead of scanning every title.
        if re.fullmatch(r"tt\d{4,16}", term):
            filters.append(ImdbMovie.imdb_id == term)
        else:
            filters.append(ImdbMovie.normalized_title.contains(term, autoescape=True))
    if vidsrc_only:
        filters.append(exists(select(VidsrcTitle.id).where(
            VidsrcTitle.imdb_id == ImdbMovie.imdb_id,
            VidsrcTitle.media_type == "movie",
            VidsrcTitle.is_active.is_(True),
        )))
    total = await session.scalar(select(func.count()).select_from(ImdbMovie).where(*filters)) or 0
    rows = (await session.execute(select(ImdbMovie, matched_embed).where(*filters)
            .order_by(ImdbMovie.normalized_title, ImdbMovie.imdb_id)
            .offset((page - 1) * page_size).limit(page_size))).all()
    return ImdbMoviePage(page=page, page_size=page_size, total=total, items=[
        ImdbMovieResponse(imdb_id=movie.imdb_id, title=movie.title,
                          release_year=movie.release_year, embed_url=embed)
        for movie, embed in rows
    ])


@app.get("/vod/search", response_model=VodPage, tags=["vod"])
async def search_vod(
    session: DatabaseSession,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    media_type: Annotated[str | None, Query(pattern="^(movie|tv)$")] = None,
    lang: Annotated[str | None, Query(max_length=16)] = None,
    country: Annotated[str | None, Query(max_length=8)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    sort_by: VodSortField = "title",
    sort_direction: SortDirection = "asc",
) -> VodPage:
    filters = [Vod.is_active.is_(True)]
    if q:
        filters.append(Vod.normalized_title.ilike(f"%{normalize_name(q)}%"))
    if media_type:
        filters.append(Vod.media_type == media_type)
    if lang:
        filters.append(Vod.language_code == lang.casefold())
    if country:
        filters.append(Vod.country_code == country.upper())

    total = await session.scalar(select(func.count(Vod.id)).where(*filters)) or 0
    sort_columns = {
        "title": Vod.normalized_title,
        "year": Vod.release_year,
        "created_at": Vod.created_at,
    }
    order = asc(sort_columns[sort_by]) if sort_direction == "asc" else desc(sort_columns[sort_by])
    rows = (
        await session.scalars(
            select(Vod)
            .where(*filters)
            .order_by(order, Vod.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return VodPage(
        page=page,
        page_size=page_size,
        total=total,
        items=[
            VodResponse(
                id=row.id,
                title=row.title,
                media_type=row.media_type,
                release_year=row.release_year,
                tmdb_id=row.tmdb_id,
                poster_path=row.poster_path,
                synopsis=row.synopsis,
                stream_url=row.stream_url,
                language_code=row.language_code,
                country_code=row.country_code,
                source_name=row.source_name,
                metadata=row.extra_metadata,
            )
            for row in rows
        ],
    )
