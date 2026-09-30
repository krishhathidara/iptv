from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel


ATTRIBUTE_PATTERN = re.compile(
    r"([A-Za-z0-9_-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s,]+)"
)
WHITESPACE_PATTERN = re.compile(r"\s+")
HLS_STREAM_INF_PATTERN = re.compile(r"^#EXT-X-STREAM-INF:(.*)$", re.IGNORECASE)
HLS_KEY_PATTERN = re.compile(r"^#EXT-X-KEY:(.*)$", re.IGNORECASE)
HLS_MAP_PATTERN = re.compile(r"^#EXT-X-MAP:(.*)$", re.IGNORECASE)
PROTECTED_HEADER_KEYS = {
    "authorization",
    "cookie",
    "http-referrer",
    "http-user-agent",
    "referer",
    "referrer",
    "user-agent",
}

LANGUAGE_ALIASES = {
    "english": "en",
    "russian": "ru",
    "turkish": "tr",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "arabic": "ar",
    "hindi": "hi",
    "portuguese": "pt",
    "italian": "it",
}
COUNTRY_ALIASES = {
    "usa": "US",
    "united states": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "russia": "RU",
    "turkey": "TR",
}


@dataclass(slots=True)
class ScrapedChannel:
    name: str
    stream_url: str
    logo: str | None = None
    language_code: str = "und"
    country_code: str = "ZZ"
    category: str = "Uncategorized"
    tvg_id: str | None = None
    source_name: str = "user-import"
    line_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    last_checked_at: datetime | None = None

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)

    @property
    def channel_key(self) -> str:
        # IPTV-org uses suffixes such as @SD, @East, and @LiveEvent for
        # alternate broadcasts of the same channel. Keep the full tvg-id for
        # display, but group those variants so the player can fail over.
        normalized_tvg_id = self.tvg_id.strip().casefold().split("@", 1)[0] if self.tvg_id else ""
        identity = (
            f"tvg:{normalized_tvg_id}"
            if normalized_tvg_id
            else f"name:{self.normalized_name}|{self.language_code}|{self.country_code}"
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def to_database_values(self) -> dict[str, Any]:
        return {
            "channel_key": self.channel_key,
            "name": self.name,
            "normalized_name": self.normalized_name,
            "tvg_id": self.tvg_id,
            "logo_url": self.logo,
            "stream_url": self.stream_url,
            "language_code": self.language_code,
            "country_code": self.country_code,
            "category": self.category,
            "source_name": self.source_name,
            "is_active": True,
            "last_checked_at": self.last_checked_at,
            "extra_metadata": self.metadata,
        }


@dataclass(slots=True)
class ScrapeFailure:
    reason: str
    line_number: int | None = None
    name: str | None = None
    stream_url: str | None = None


@dataclass(slots=True)
class ParseResult:
    records: list[ScrapedChannel] = field(default_factory=list)
    failures: list[ScrapeFailure] = field(default_factory=list)

    def extend(self, other: "ParseResult") -> None:
        self.records.extend(other.records)
        self.failures.extend(other.failures)


@dataclass(slots=True)
class PersistenceResult:
    inserted_count: int = 0
    updated_count: int = 0
    failures: list[ScrapeFailure] = field(default_factory=list)


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return WHITESPACE_PATTERN.sub(" ", normalized)


def normalize_language(value: str | None, default: str = "und") -> str:
    candidate = (value or default or "und").split(";")[0].split(",")[0].strip().casefold()
    candidate = LANGUAGE_ALIASES.get(candidate, candidate)
    if re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", candidate):
        return candidate[:16]
    return "und"


def normalize_country(value: str | None, default: str = "ZZ") -> str:
    candidate = (value or default or "ZZ").split(";")[0].split(",")[0].strip()
    candidate = COUNTRY_ALIASES.get(candidate.casefold(), candidate).upper()
    if re.fullmatch(r"[A-Z]{2,3}", candidate):
        return candidate[:8]
    return "ZZ"


def infer_country_from_tvg_id(tvg_id: str | None) -> str | None:
    """Infer IPTV-org's country suffix from IDs such as ``BBCNews.uk@HD``."""
    if not tvg_id:
        return None
    base_id = tvg_id.strip().split("@", 1)[0]
    if "." not in base_id:
        return None
    candidate = base_id.rsplit(".", 1)[-1]
    if len(candidate) != 2 and candidate.casefold() not in COUNTRY_ALIASES:
        return None
    normalized = normalize_country(candidate)
    return normalized if normalized != "ZZ" else None


def is_supported_stream_url(value: str) -> bool:
    request_url = value.split("|", 1)[0].strip()
    try:
        parsed = urlsplit(request_url)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc) and " " not in request_url


def _split_extinf(value: str) -> tuple[str, str]:
    quote: str | None = None
    for index, character in enumerate(value):
        if character in {'"', "'"}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
        elif character == "," and quote is None:
            return value[:index], value[index + 1 :]
    return value, ""


def _parse_attributes(value: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for match in ATTRIBUTE_PATTERN.finditer(value):
        raw_value = match.group(2)
        if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in {'"', "'"}:
            raw_value = raw_value[1:-1]
        attributes[match.group(1).casefold()] = raw_value.strip()
    return attributes


class ChannelScraper:
    """Parse playlists and verify that HLS media is playable by the browser UI."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        validation_concurrency: int = 50,
        playback_origin: str = "http://127.0.0.1:8000",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.validation_concurrency = validation_concurrency
        self.playback_origin = playback_origin.rstrip("/")
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0 Safari/537.36"
                )
            },
            limits=httpx.Limits(
                max_connections=max(validation_concurrency, 20),
                max_keepalive_connections=max(min(validation_concurrency, 100), 20),
            ),
        )

    async def __aenter__(self) -> "ChannelScraper":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def load_from_m3u_file(
        self,
        file_path: str | Path,
        *,
        source_name: str = "user-file",
        default_language_code: str = "und",
        default_country_code: str = "ZZ",
        default_category: str = "Uncategorized",
    ) -> ParseResult:
        path = Path(file_path).expanduser().resolve(strict=True)
        with path.open("r", encoding="utf-8-sig", errors="replace") as playlist:
            return self.parse_m3u_lines(
                playlist,
                source_name=source_name,
                default_language_code=default_language_code,
                default_country_code=default_country_code,
                default_category=default_category,
            )

    def load_from_m3u_text(
        self,
        raw_m3u: str,
        *,
        source_name: str = "user-paste",
        default_language_code: str = "und",
        default_country_code: str = "ZZ",
        default_category: str = "Uncategorized",
    ) -> ParseResult:
        return self.parse_m3u_lines(
            raw_m3u.splitlines(),
            source_name=source_name,
            default_language_code=default_language_code,
            default_country_code=default_country_code,
            default_category=default_category,
        )

    async def load_from_url(
        self,
        source_url: str,
        *,
        source_name: str = "remote-playlist",
        default_language_code: str = "und",
        default_country_code: str = "ZZ",
        default_category: str = "Uncategorized",
        max_bytes: int = 50 * 1024 * 1024,
    ) -> ParseResult:
        if not is_supported_stream_url(source_url):
            return ParseResult(failures=[ScrapeFailure(reason="Invalid source URL", stream_url=source_url)])

        try:
            async with self.client.stream("GET", source_url) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                    return ParseResult(
                        failures=[
                            ScrapeFailure(
                                reason="Remote playlist exceeds configured size limit",
                                stream_url=source_url,
                            )
                        ]
                    )
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        return ParseResult(
                            failures=[
                                ScrapeFailure(
                                    reason="Remote playlist exceeds configured size limit",
                                    stream_url=source_url,
                                )
                            ]
                        )
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            return ParseResult(
                failures=[ScrapeFailure(reason=f"Could not fetch source: {exc}", stream_url=source_url)]
            )

        return self.parse_m3u_lines(
            b"".join(chunks).decode("utf-8-sig", errors="replace").splitlines(),
            source_name=source_name,
            default_language_code=default_language_code,
            default_country_code=default_country_code,
            default_category=default_category,
        )

    async def load_from_source_config(self, config_path: str | Path) -> ParseResult:
        """Load multiple remote M3U sources from a JSON configuration file."""
        entries = json.loads(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("Source config must contain a JSON array")

        combined = ParseResult()
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("url"):
                combined.failures.append(ScrapeFailure(reason="Source config entry has no URL"))
                continue
            result = await self.load_from_url(
                str(entry["url"]),
                source_name=str(entry.get("source_name") or "configured-source"),
                default_language_code=str(entry.get("language_code") or "und"),
                default_country_code=str(entry.get("country_code") or "ZZ"),
                default_category=str(entry.get("category") or "Uncategorized"),
            )
            combined.extend(result)
        return combined

    def load_from_json(
        self,
        payload: str | bytes | list[dict[str, Any]] | dict[str, Any],
        *,
        source_name: str = "json-import",
        default_language_code: str = "und",
        default_country_code: str = "ZZ",
        default_category: str = "Uncategorized",
    ) -> ParseResult:
        if isinstance(payload, (str, bytes)):
            payload = json.loads(payload)
        entries = payload.get("channels", []) if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise ValueError("JSON input must be an array or an object containing a channels array")

        result = ParseResult()
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                result.failures.append(
                    ScrapeFailure(
                        reason="Channel entry is not an object",
                        line_number=index,
                    )
                )
                continue
            stream_url = str(entry.get("stream_url") or entry.get("url") or "").strip()
            name = str(entry.get("name") or entry.get("title") or "").strip()
            if not name or not is_supported_stream_url(stream_url):
                result.failures.append(
                    ScrapeFailure(
                        line_number=index,
                        name=name or None,
                        stream_url=stream_url or None,
                        reason="Missing name or invalid HTTP(S) stream URL",
                    )
                )
                continue
            result.records.append(
                ScrapedChannel(
                    name=name,
                    stream_url=stream_url,
                    logo=str(entry.get("logo") or entry.get("logo_url") or "").strip() or None,
                    language_code=normalize_language(
                        entry.get("language_code") or entry.get("language"), default_language_code
                    ),
                    country_code=normalize_country(
                        entry.get("country_code") or entry.get("country"), default_country_code
                    ),
                    category=str(entry.get("category") or default_category).strip()[:128],
                    tvg_id=str(entry.get("tvg_id") or "").strip() or None,
                    source_name=source_name,
                    line_number=index,
                    metadata={
                        key: value
                        for key, value in entry.items()
                        if key
                        not in {
                            "name",
                            "title",
                            "stream_url",
                            "url",
                            "logo",
                            "logo_url",
                            "language_code",
                            "language",
                            "country_code",
                            "country",
                            "category",
                            "tvg_id",
                        }
                    },
                )
            )
        return result

    def parse_m3u_lines(
        self,
        lines: Iterable[str],
        *,
        source_name: str,
        default_language_code: str = "und",
        default_country_code: str = "ZZ",
        default_category: str = "Uncategorized",
    ) -> ParseResult:
        result = ParseResult()
        pending_attributes: dict[str, str] = {}
        pending_name = ""
        pending_line: int | None = None
        pending_group = ""
        pending_properties: dict[str, str] = {}

        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip().lstrip("\ufeff")
            if not line:
                continue
            if line.startswith("#EXTINF:"):
                attribute_text, pending_name = _split_extinf(line[len("#EXTINF:") :])
                pending_attributes = _parse_attributes(attribute_text)
                pending_name = pending_name.strip()
                pending_line = line_number
                pending_group = ""
                pending_properties = {}
                continue
            if line.startswith("#EXTGRP:"):
                pending_group = line.partition(":")[2].strip()
                continue
            if line.startswith("#KODIPROP:") or line.startswith("#EXTVLCOPT:"):
                key, _, value = line.partition(":")[2].partition("=")
                if key:
                    pending_properties[key.strip()] = value.strip()
                continue
            if line.startswith("#"):
                continue

            stream_url = line
            name = (
                pending_name
                or pending_attributes.get("tvg-name")
                or self._name_from_url(stream_url)
            ).strip()
            if not is_supported_stream_url(stream_url):
                result.failures.append(
                    ScrapeFailure(
                        line_number=line_number,
                        name=name or None,
                        stream_url=stream_url,
                        reason="Invalid or unsupported stream URL; expected HTTP(S)",
                    )
                )
            elif not name:
                result.failures.append(
                    ScrapeFailure(
                        line_number=line_number,
                        stream_url=stream_url,
                        reason="Channel name could not be determined",
                    )
                )
            else:
                language = (
                    pending_attributes.get("tvg-language")
                    or pending_attributes.get("language")
                    or pending_attributes.get("language-code")
                )
                tvg_id = pending_attributes.get("tvg-id") or None
                country = (
                    pending_attributes.get("tvg-country")
                    or pending_attributes.get("country")
                    or pending_attributes.get("country-code")
                    or infer_country_from_tvg_id(tvg_id)
                )
                category = (
                    pending_attributes.get("group-title")
                    or pending_group
                    or default_category
                    or "Uncategorized"
                )
                known_attributes = {
                    "tvg-id",
                    "tvg-name",
                    "tvg-logo",
                    "tvg-language",
                    "language",
                    "language-code",
                    "tvg-country",
                    "country",
                    "country-code",
                    "group-title",
                }
                result.records.append(
                    ScrapedChannel(
                        name=WHITESPACE_PATTERN.sub(" ", name)[:512],
                        logo=pending_attributes.get("tvg-logo") or None,
                        stream_url=stream_url,
                        language_code=normalize_language(language, default_language_code),
                        country_code=normalize_country(country, default_country_code),
                        category=WHITESPACE_PATTERN.sub(" ", category.strip())[:128]
                        or "Uncategorized",
                        tvg_id=tvg_id,
                        source_name=source_name[:255],
                        line_number=pending_line or line_number,
                        metadata={
                            "playlist_attributes": {
                                key: value
                                for key, value in pending_attributes.items()
                                if key not in known_attributes
                            },
                            "player_properties": pending_properties,
                        },
                    )
                )

            pending_attributes = {}
            pending_name = ""
            pending_line = None
            pending_group = ""
            pending_properties = {}

        return result

    @staticmethod
    def _name_from_url(stream_url: str) -> str:
        path = urlsplit(stream_url.split("|", 1)[0]).path.rstrip("/")
        leaf = unquote(path.rsplit("/", 1)[-1]) if path else ""
        return re.sub(r"\.(m3u8?|ts|mpd)$", "", leaf, flags=re.IGNORECASE)

    async def validate_records(
        self,
        records: Sequence[ScrapedChannel],
        *,
        timeout_seconds: float = 8.0,
    ) -> tuple[list[ScrapedChannel], list[ScrapeFailure]]:
        semaphore = asyncio.Semaphore(self.validation_concurrency)

        async def validate(record: ScrapedChannel) -> tuple[ScrapedChannel, str | None]:
            async with semaphore:
                error = await self.validate_record(record, timeout_seconds=timeout_seconds)
                return record, error

        valid: list[ScrapedChannel] = []
        failures: list[ScrapeFailure] = []
        chunk_size = max(self.validation_concurrency * 4, 1)
        for offset in range(0, len(records), chunk_size):
            checked = await asyncio.gather(
                *(validate(record) for record in records[offset : offset + chunk_size])
            )
            for record, error in checked:
                if error is None:
                    valid.append(record)
                else:
                    failures.append(
                        ScrapeFailure(
                            line_number=record.line_number,
                            name=record.name,
                            stream_url=record.stream_url,
                            reason=error,
                        )
                    )
        return valid, failures

    async def validate_record(
        self,
        record: ScrapedChannel,
        *,
        timeout_seconds: float = 8.0,
    ) -> str | None:
        """Confirm an unencrypted HLS manifest and media segment are browser-readable."""
        checked_at = datetime.now(timezone.utc)
        record.last_checked_at = checked_at
        metadata = dict(record.metadata or {})
        health = dict(metadata.get("playback_health") or {})
        health.update({"checked_at": checked_at.isoformat(), "browser_playable": False})
        metadata["playback_health"] = health
        record.metadata = metadata

        error = self._unsupported_browser_headers(record)
        if error is None:
            error = await self._validate_hls_url(
                record.stream_url.split("|", 1)[0].strip(),
                timeout_seconds=timeout_seconds,
            )

        if error is None:
            health["browser_playable"] = True
            health.pop("error", None)
        else:
            health["error"] = error[:500]
        return error

    @staticmethod
    def _unsupported_browser_headers(record: ScrapedChannel) -> str | None:
        if "|" in record.stream_url:
            return "Stream requires URL-supplied request headers that the browser player cannot set"
        metadata = record.metadata or {}
        configured: dict[str, Any] = {}
        configured.update(metadata.get("playlist_attributes") or {})
        configured.update(metadata.get("player_properties") or {})
        if any(str(key).casefold() in PROTECTED_HEADER_KEYS for key in configured):
            return "Stream requires protected referrer, user-agent, cookie, or authorization headers"
        return None

    @staticmethod
    def _origin(value: str) -> tuple[str, str, int | None]:
        parsed = urlsplit(value)
        return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port

    def _cors_error(self, response: httpx.Response) -> str | None:
        if self._origin(str(response.url)) == self._origin(self.playback_origin):
            return None
        allowed_origin = response.headers.get("access-control-allow-origin", "").strip()
        if allowed_origin == "*" or allowed_origin.rstrip("/") == self.playback_origin:
            return None
        return "Upstream does not allow browser cross-origin playback (CORS)"

    async def _read_limited(
        self,
        response: httpx.Response,
        *,
        max_bytes: int,
    ) -> bytes:
        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            remaining = max_bytes - received
            chunks.append(chunk[:remaining])
            received += min(len(chunk), remaining)
            if received >= max_bytes:
                break
        return b"".join(chunks)

    async def _fetch_browser_resource(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> tuple[httpx.Response | None, bytes, str | None]:
        try:
            async with self.client.stream(
                "GET",
                url,
                headers={
                    "Accept": "application/vnd.apple.mpegurl, application/x-mpegURL, */*",
                    "Origin": self.playback_origin,
                },
                timeout=timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    return response, b"", f"Stream returned HTTP {response.status_code}"
                cors_error = self._cors_error(response)
                if cors_error:
                    return response, b"", cors_error
                body = await self._read_limited(response, max_bytes=max_bytes)
                if not body:
                    return response, b"", "Stream returned an empty response"
                return response, body, None
        except httpx.HTTPError as exc:
            return None, b"", f"Stream validation failed: {exc.__class__.__name__}"

    @staticmethod
    def _master_variants(lines: list[str]) -> list[str]:
        variants: list[tuple[int, str]] = []
        for index, line in enumerate(lines[:-1]):
            match = HLS_STREAM_INF_PATTERN.match(line)
            if not match:
                continue
            attributes = _parse_attributes(match.group(1))
            try:
                bandwidth = int(attributes.get("bandwidth", "0"))
            except ValueError:
                bandwidth = 0
            for candidate in lines[index + 1 :]:
                if candidate and not candidate.startswith("#"):
                    variants.append((bandwidth, candidate))
                    break
        return [uri for _, uri in sorted(variants, key=lambda item: item[0])]

    async def _validate_hls_url(
        self,
        url: str,
        *,
        timeout_seconds: float,
        depth: int = 0,
    ) -> str | None:
        if depth > 2:
            return "HLS playlist nesting is unsupported"
        if (
            urlsplit(self.playback_origin).scheme.casefold() == "https"
            and urlsplit(url).scheme.casefold() == "http"
        ):
            return "HTTPS dashboards cannot load insecure HTTP streams (mixed content)"
        response, body, error = await self._fetch_browser_resource(
            url,
            timeout_seconds=timeout_seconds,
            max_bytes=1024 * 1024,
        )
        if error:
            return error
        assert response is not None
        text = body.decode("utf-8-sig", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or lines[0].upper() != "#EXTM3U":
            return "Stream is not an HLS manifest supported by the browser player"

        variants = self._master_variants(lines)
        if variants:
            variant_errors: list[str] = []
            for variant in variants[:3]:
                variant_error = await self._validate_hls_url(
                    urljoin(str(response.url), variant),
                    timeout_seconds=timeout_seconds,
                    depth=depth + 1,
                )
                if variant_error is None:
                    return None
                variant_errors.append(variant_error)
            return f"No browser-playable HLS variant: {variant_errors[0]}"

        for line in lines:
            key_match = HLS_KEY_PATTERN.match(line)
            if not key_match:
                continue
            method = _parse_attributes(key_match.group(1)).get("method", "").upper()
            if method and method != "NONE":
                return "Encrypted or DRM-protected HLS streams are not imported"

        if any(line.upper() == "#EXT-X-ENDLIST" for line in lines):
            return "HLS playlist is on-demand media, not a live stream"

        for line in lines:
            map_match = HLS_MAP_PATTERN.match(line)
            if not map_match:
                continue
            map_uri = _parse_attributes(map_match.group(1)).get("uri")
            if not map_uri:
                return "HLS initialization segment URI is missing"
            _, map_body, map_error = await self._fetch_browser_resource(
                urljoin(str(response.url), map_uri),
                timeout_seconds=timeout_seconds,
                max_bytes=32 * 1024,
            )
            if map_error:
                return f"HLS initialization segment failed: {map_error}"
            if len(map_body) < 16:
                return "HLS initialization segment did not contain playable media"
            break

        segments = [line for line in lines if not line.startswith("#")]
        if not segments:
            return "HLS media playlist contains no current segments"
        segment = segments[-2] if len(segments) > 1 else segments[-1]
        _, segment_body, segment_error = await self._fetch_browser_resource(
            urljoin(str(response.url), segment),
            timeout_seconds=timeout_seconds,
            max_bytes=32 * 1024,
        )
        if segment_error:
            return f"HLS media segment failed: {segment_error}"
        if len(segment_body) < 16:
            return "HLS media segment did not contain playable media"
        return None

    async def persist_channels(
        self,
        session: AsyncSession,
        records: Sequence[ScrapedChannel],
        *,
        batch_size: int = 500,
    ) -> PersistenceResult:
        result = PersistenceResult()
        deduplicated = list({record.stream_url: record for record in records}.values())

        for offset in range(0, len(deduplicated), batch_size):
            batch = deduplicated[offset : offset + batch_size]
            try:
                inserted, updated = await self._persist_batch(session, batch)
                await session.commit()
                result.inserted_count += inserted
                result.updated_count += updated
            except Exception:
                await session.rollback()
                # Isolate a malformed row so one failure never discards the rest of a large import.
                for record in batch:
                    try:
                        inserted, updated = await self._persist_batch(session, [record])
                        await session.commit()
                        result.inserted_count += inserted
                        result.updated_count += updated
                    except Exception as exc:
                        await session.rollback()
                        result.failures.append(
                            ScrapeFailure(
                                line_number=record.line_number,
                                name=record.name,
                                stream_url=record.stream_url,
                                reason=f"Database write failed: {exc.__class__.__name__}",
                            )
                        )
        return result

    async def _persist_batch(
        self, session: AsyncSession, records: Sequence[ScrapedChannel]
    ) -> tuple[int, int]:
        values = [record.to_database_values() for record in records]
        urls = [record.stream_url for record in records]
        existing = set(
            (await session.scalars(select(Channel.stream_url).where(Channel.stream_url.in_(urls)))).all()
        )

        dialect_name = session.bind.dialect.name if session.bind is not None else ""
        if dialect_name == "postgresql":
            statement = postgresql_insert(Channel).values(values)
            excluded = statement.excluded
            statement = statement.on_conflict_do_update(
                index_elements=[Channel.stream_url],
                set_={
                    "channel_key": excluded.channel_key,
                    "name": excluded.name,
                    "normalized_name": excluded.normalized_name,
                    "tvg_id": excluded.tvg_id,
                    "logo_url": excluded.logo_url,
                    "language_code": excluded.language_code,
                    "country_code": excluded.country_code,
                    "category": excluded.category,
                    "source_name": excluded.source_name,
                    "is_active": True,
                    "last_checked_at": func.coalesce(
                        excluded.last_checked_at, Channel.last_checked_at
                    ),
                    "extra_metadata": excluded.extra_metadata,
                },
            )
            await session.execute(statement)
        elif dialect_name == "sqlite":
            statement = sqlite_insert(Channel).values(values)
            excluded = statement.excluded
            statement = statement.on_conflict_do_update(
                index_elements=[Channel.stream_url],
                set_={
                    "channel_key": excluded.channel_key,
                    "name": excluded.name,
                    "normalized_name": excluded.normalized_name,
                    "tvg_id": excluded.tvg_id,
                    "logo_url": excluded.logo_url,
                    "language_code": excluded.language_code,
                    "country_code": excluded.country_code,
                    "category": excluded.category,
                    "source_name": excluded.source_name,
                    "is_active": True,
                    "last_checked_at": func.coalesce(
                        excluded.last_checked_at, Channel.last_checked_at
                    ),
                    "extra_metadata": excluded.extra_metadata,
                },
            )
            await session.execute(statement)
        else:
            for value in values:
                current = await session.scalar(
                    select(Channel).where(Channel.stream_url == value["stream_url"])
                )
                if current is None:
                    session.add(Channel(**value))
                else:
                    for key, item in value.items():
                        setattr(current, key, item)

        return len(records) - len(existing), len(existing)
