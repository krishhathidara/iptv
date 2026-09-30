# NexaStream IPTV Aggregator

## Public TV trial (separate management dashboard later)

The root GitHub repository can be connected to Render as a **Blueprint** using `render.yaml`. Creating a GitHub repository alone does **not** host the Python application or supply a public URL. The Blueprint creates a TV-only Docker web service and a persistent-across-redeploys PostgreSQL database; both use Render's **free trial plans**. In the Render dashboard choose **New → Blueprint**, authorize the GitHub repository, select this repository, review the resources, and apply. When the deployment succeeds, copy the service's actual `https://…onrender.com` URL and verify `/health` says `ok` and `/` displays the TV welcome page. Do not use a guessed URL. Render's free database expires after **30 days** and can be deleted; upgrade/back up before creating real accounts. A free web service sleeps when idle and may have a long first load, especially on a TV; upgrade to an always-on plan for real viewers. Render generates a secret `ADMIN_API_KEY` in the service's environment; do not commit it or share it with customers. If you attach a custom domain, configure `SUBSCRIBER_BASE_URL=https://your-domain` in Render *before* issuing customer URLs.

`TV_PUBLIC_MODE=true` hides the original local dashboard, API docs, public playlist, static admin assets and unprotected catalog/source/VOD management endpoints. It allows only customer TV/MAG pages, the live playlist, basic static assets, admin-key-protected subscriber endpoints, and admin-key-protected `POST /import-m3u`. The public homepage is **not** a free watch link; each customer gets a separate secret URL created by the administrator. This deployment intentionally has **no public administration webpage**: the management dashboard will need its own deployment and must call the keyed customer API only after origin/security design. For a trial *before* the separate dashboard, create and toggle accounts using an HTTPS API client with `X-Admin-Key` (never enter the key on the customer's TV):

```text
POST https://YOUR-RENDER-URL/admin/subscribers
X-Admin-Key: YOUR-SECRET
Content-Type: application/json

{"name":"My TV","mac_address":"AA:BB:CC:DD:EE:FF","months":1,"is_active":false}

PATCH https://YOUR-RENDER-URL/admin/subscribers/1
X-Admin-Key: YOUR-SECRET
Content-Type: application/json

{"is_active":true}
```

Save the private `portal_url` (TV browser), `mag_portal_url` (compatible MAG external portal), or `playlist_url` (compatible IPTV player) returned when the customer is created. To suspend, repeat the PATCH with `{"is_active":false}`. To load only streams you are allowed to distribute, send an authorized M3U to `POST /import-m3u` with the admin key. The public Blueprint starts with **no channels**: do not assume local demo data or its very large SQLite database is deployed. Trial setup does not guarantee any stream will play on a particular TV: upstream access, codec, player compatibility and licensing still matter. The existing player fetches upstream streams directly: already-copied upstream links keep working when an account is disabled. A MAC is self-reported, not secure identity. Do **not** sell/provision access until distribution rights, revocable stream delivery and real-device testing are in place.

FastAPI service and responsive browser dashboard for indexing metadata and stream URLs from authorized M3U/JSON sources. The application stores catalog data only; it does not download or host video content.

## Run the worldwide live-TV catalog

The local runner uses SQLite, synchronizes IPTV-org's worldwide playlist when the database is first created, validates browser playback, starts FastAPI, and opens the dashboard in your default browser. PostgreSQL and Redis are not required for this mode. The first run is slower because every candidate must provide an HLS manifest, a current media segment, and compatible CORS headers before it becomes active. Normal restarts reuse the validated database; use `-RefreshCatalog` to recheck the full public catalog manually. A separate language-directory enrichment maps validated stream URLs to upstream language labels without adding unchecked streams.

Each catalog row retains its upstream channel name, logo, category, identifier, and distinct stream URL. NexaStream does not download or restream the video; playback connects directly to the channel operator or public distribution platform.

```powershell
cd "d:\iptv new"
.\scripts\run_demo.ps1
```

To refresh every worldwide source (can take a long time):

```powershell
.\scripts\stop_demo.ps1
.\scripts\run_demo.ps1 -RefreshCatalog
```

The old generated 12,000-row catalog remains available only as an explicit performance-test profile. Those rows deliberately reuse a small number of public test videos and are not live channels:

```powershell
.\scripts\run_demo.ps1 -SyntheticCatalog -ChannelCount 12000
```

Open `http://127.0.0.1:8000` if the browser does not open automatically. Stop it with:

```powershell
.\scripts\stop_demo.ps1
```

The dashboard has independent **Live TV**, **VOD Library**, and **Import Sources** sections. Live TV includes visible category sections (News, Sports, Kids, etc.) covering multi-category channels, and can also be filtered by language and country. Selecting a channel immediately opens its live stream in the built-in HLS player, with fallback restricted to the same channel. TV-browser viewers can use the remote's direction keys to navigate the sidebar, top controls and content; Select/Enter to play, and Back/Escape to close the player or menu. Focused controls are enlarged and highlighted when using remote arrows. Text entry needs the TV's on-screen keyboard. Full-screen video is supported where the browser allows it. Some TVs require one additional Play press to allow autoplay. Remote/browser key codes and iframe player behavior can differ by TV model.

To run the optional desktop-browser remote interaction smoke check on Windows, with Node.js, Chrome or Edge, and the project `.venv` installed: `node scripts/smoke_tv_remote.cjs`. It creates a temporary SQLite database and admin key rather than changing the demo accounts; testing on a physical TV is still recommended.

### Subscriber administration (local-only prototype)

The **Subscribers** navigation link opens `/admin`. To enable account management, generate an administrator secret (e.g. `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"`) and set `ADMIN_API_KEY` to that secret in your server environment or a private `.env` file, then restart the server. Set `SUBSCRIBER_BASE_URL` to the actual address the device can reach (not `127.0.0.1`); use HTTPS on untrusted networks. The base URL is configured by the operator, never inferred from request headers. Enter the secret in the admin portal to create an account with 1–120 calendar months of access, a MAC record for MAG devices, and optional notes. The admin page supports arrows, OK/Enter and Back/Escape. New accounts made in the admin form are **inactive by default**: create a customer, record/edit their MAC, select **Activate**, then save the three private URLs shown once on creation or rotation. Rotating invalidates all three. The token is stored as a SHA-256 hash, not plaintext. Admin access uses `X-Admin-Key`; without a configured key it fails closed. The demo runner and Dockerfile disable access logs containing token URLs.

**MAG external portal (trusted-LAN prototype):** On a compatible Infomir MAG Linux/WebKit box, open *System settings → Servers → Portals* and enter the customer's **MAG external portal URL** (`.../stalker/<private-token>/c/index.html`), not the browser or M3U URL. Record that device's MAC on the customer and activate them first. On the same trusted LAN, run `.\scripts\stop_demo.ps1` then `.\scripts\run_demo.ps1 -LanAccess -NoBrowser`; check `SUBSCRIBER_BASE_URL` is the reachable LAN address. The portal calls `gSTB.GetDeviceMacAddress`, `InitPlayer`, `Play`, and `Stop` to show/play active live channels. A restricted `/stalker/<token>/server/load.php` endpoint implements `handshake`, `get_profile`, `get_genres`, `get_all_channels`, paginated `get_ordered_list` and `create_link` for live TV only. A registered MAC and a short-lived bearer session are required for catalog and link calls. **This is not full Stalker/Ministra middleware; third-party Stalker apps and particular MAG firmware versions may require more actions or different playback behavior. Physical device testing is required.** VOD, EPG, archives, timeshift, DRM and firmware/app updates are not provided by the MAG portal. Stream URLs go directly to the upstream host; codec, network, format and geographical restrictions still apply.

The MAG page loads live channels 50 at a time; select **Load more channels** with the remote to continue. You can check the portal's remote and playback *calls* with a software mock using `node scripts/smoke_mag_portal.cjs`. This does not test real video output or firmware compatibility. The optional Chrome TV-browser/admin check is `node scripts/smoke_tv_remote.cjs`.

**Important limitations:** A MAC sent in an HTTP request can be spoofed: matching a recorded MAC is only an additional check, **not secure hardware authentication**. The private URL controls future portal/API/M3U requests, not previously obtained direct upstream streams. Expiry/suspension cannot stop an already playing/copied upstream URL or control simultaneous devices. The M3U contains active live channels and directly imported, authorized VOD URLs, but not VidSrc/IMDb iframes. The original `/playlist.m3u` and other catalog/import endpoints remain unauthenticated in the **local** application; **do not expose the local app directly to the public internet or publish its private .env/database.** The opt-in `TV_PUBLIC_MODE` restricts the public TV host to subscriber routes and keyed operations, but does not provide revocable streams or hardware authentication. Use HTTPS, stronger access controls on management endpoints and an authorized, revocable stream delivery mechanism before a commercial release. Verify distribution rights for every channel. Never sell access to the IMDb non-commercial dataset or unlicensed streams. The portal URL is a bearer credential: keep it private, rotate if compromised, and redact reverse-proxy access logs. A device on another network cannot reach localhost or a private LAN IP.

**Indian TV** (`/#india`) is a dedicated country-scoped view of the active, independently sourced Indian channels already in the catalog. Its language and category counts and the TV playlist export are scoped to India. YuppTV's `/livetv` page exposes page metadata rather than an authorized public M3U or direct playback URLs; its account/subscription channels are **not** copied into this playable catalog. The page links to YuppTV for viewers who have access to its service. An authorized provider playlist can be added through Import Sources with browser validation enabled. We cannot guarantee that every provider channel starts instantly: playback depends on the provider, location, supported codecs, and the viewer's browser/TV.

The **VOD library** offers visible language, country, Movies and TV Shows controls. Its **Add authorized VOD** form accepts a JSON array of direct `.m3u8`, `.mp4`, or `.webm` HTTP(S) media URLs, one entry per movie or playable TV episode. Example: `[{"title":"My Film","stream_url":"https://media.example/film.mp4","media_type":"movie","language_code":"en","country_code":"US"}]`. You may also call `POST /vod/import` with `{"source_name":"My licensed library","items":[...]}` (up to 200 per request); imports are idempotent by media URL. **Import does not verify reachability, licenses, browser CORS, or codec support.** Click-to-play starts the provided media URL immediately with native file playback for MP4/WebM and HLS support where available; actual startup speed is controlled by the upstream host, network, TV and browser.

**VidSrc embed index:** `POST /vod/vidsrc/sync` loads the published movie and TV IMDb ID lists as well as every page advertised by the `/movies/latest/page-{N}.json` and `/tvshows/latest/page-{N}.json` feeds. The counts are **indexed IDs, not guaranteed playable movies**. Recent-feed entries have display names; older entries initially show IMDb IDs and cannot be found by movie name until enriched. For **personal, non-commercial local use only**, set `DATABASE_URL=sqlite+aiosqlite:///./demo_iptv.sqlite3` and run `.\.venv\Scripts\python.exe -m scripts.enrich_vidsrc_titles` from the project root. This explicitly downloads IMDb's official `title.basics.tsv.gz` once, matches existing unresolved IDs, and makes their names searchable without altering the playback links or overriding existing titles. It requires around 230 MB of network data and may take a few minutes. Check IMDb's terms before use; do not use the resulting database commercially or republish it. Information courtesy of IMDb (https://www.imdb.com). Used with permission. `GET /vod/vidsrc/search` searches stored names and IDs; visible unnamed cards may also resolve through `POST /vod/vidsrc/resolve`. Press **Watch here** for the provider's iframe; TV embeds have a season/episode picker. The service does not host or verify media, playback, rights, or completeness. Refresh the upstream ID list manually as needed. The sync and resolve endpoints are unauthenticated and should not be exposed on public networks.

To prepopulate the local demo catalog without opening the dashboard, run ` $env:DATABASE_URL="sqlite+aiosqlite:///./demo_iptv.sqlite3"; .\.venv\Scripts\python.exe -m scripts.sync_vidsrc ` from the project root. This is a large import and may take several minutes. You can also press **Sync VidSrc catalog** on the VOD page.

**IMDb movie directory (explicit opt-in):** For a *personal, non-commercial local copy*, run `$env:DATABASE_URL="sqlite+aiosqlite:///./demo_iptv.sqlite3"; .\.venv\Scripts\python.exe -m scripts.sync_imdb_movies` from the project root. This downloads IMDb's official `title.basics.tsv.gz` (~230 MB) and indexes its non-adult `titleType=movie` rows, including IMDb IDs and release years. It is a **subset, not all movies in IMDb**. `GET /vod/imdb/search?q=avengers` defaults to movies whose IDs also appear in the **active VidSrc movie ID list**; set `vidsrc_only=false` for every imported movie, including those with no VidSrc match. The VOD page displays both views, and shows **Watch via VidSrc** only for matched IDs. This uses the existing VidSrc iframe, **not** an imported media file or verified full-length stream. Neither IMDb nor the ID match establishes playback availability or distribution rights. IMDb restricts use of the dataset to personal, non-commercial purposes and disallows republishing a movie database. Information courtesy of IMDb (https://www.imdb.com). Used with permission.

Use **Download TV playlist (M3U)** to import the live catalog into an IPTV player on a TV. The playlist preserves channel groups (channels with multiple categories appear in each relevant TV group) and follows the currently selected language, country and category filters; it contains direct provider links, not proxied video. The TV and playlist player must support each stream's media format and any geographic/provider requirements. To use the dashboard or a playlist URL from a TV on the same trusted network, stop the local server and run `.\scripts\run_demo.ps1 -LanAccess`; it prints the LAN TV dashboard and IPTV playlist URLs. Windows Firewall may require allowing the port. This option also exposes unauthenticated admin/import endpoints on the LAN: never port-forward it or expose it to the public Internet.

Remote M3U URLs can be saved as managed playlist providers. A managed provider can be refreshed from the dashboard, reports its last status and active-channel count, and can deactivate records removed from a complete playlist snapshot. Credential-bearing source URLs are redacted in API/dashboard responses. The worldwide public catalog is registered this way and can be refreshed from **Import Sources**.

IPTV-org describes its repository as a collection of user-submitted links to publicly available streams and does not host video. NexaStream imports only unencrypted HLS entries that deliver a current media segment and permit browser CORS access at validation time. Playback starts immediately from a previously validated source and tries only same-channel alternatives on failure. If local attempts all fail, the server revalidates the upstream sources; only failed server-side checks deactivate feeds. A browser's lack of HLS or support for a particular codec does not deactivate the upstream feed. No public stream can be guaranteed permanently: an upstream can still stop, change, or become geo-restricted after a successful check. NexaStream never substitutes an unrelated video.

Upstream request URLs are not emitted through the default application logs, because public manifests can contain temporary signed query parameters.

## Architecture

- **FastAPI + async HTTPX** for high-concurrency API and source access.
- **PostgreSQL + async SQLAlchemy** for indexed language/country/category queries and batched upserts.
- **Redis** for optional channel-list response caching. Cache failures are non-fatal.
- **Streaming/chunked imports** for uploaded files and batched database writes. There is no channel-count hard limit.
- **Managed provider synchronization** with status tracking, safe URL previews, and optional stale-record reconciliation.
- **Browser-playback validation** with bounded concurrency: HLS parsing, current-segment retrieval, CORS checks, and protected-header rejection.
- **TMDB-ready VOD enrichment** through `app/services/vod_scraper.py` when `TMDB_API_KEY` is configured.
- **Built-in browser player** using a vendored Apache-2.0 hls.js 1.7.3 build with native-HLS fallback.

Only aggregate streams you are authorized to access. This project does not bypass provider authentication, extract account credentials, defeat DRM, or authorize rebroadcasting. A browser player still depends on the upstream host permitting cross-origin playback; API CORS headers cannot override the upstream stream server's CORS policy.

## Local setup

```powershell
cd "d:\iptv new"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
docker compose up -d postgres redis
uvicorn main:app --reload
```

The web dashboard is available at `/`, and interactive API documentation is available at `/docs`. To run everything in containers, use `docker compose up --build`.

## Import examples

Upload a playlist:

```powershell
curl.exe -X POST http://localhost:8000/import-m3u `
  -F "file=@D:\playlists\channels.m3u" `
  -F "source_name=my-playlist" `
  -F "default_language_code=en" `
  -F "default_country_code=US"
```

Paste playlist text as JSON:

```json
POST /import-m3u
{
  "raw_m3u": "#EXTM3U\n#EXTINF:-1 tvg-language=\"en\" tvg-country=\"US\",Example\nhttps://example.org/live.m3u8",
  "source_name": "manual-import",
  "validate_urls": true
}
```

Import from a remote repository or source URL:

```json
POST /import-m3u
{
  "source_url": "https://example.org/authorized-playlist.m3u",
  "source_name": "authorized-source"
}
```

Save a remote URL as a managed provider and synchronize it:

```json
POST /sources
{
  "name": "licensed-provider",
  "playlist_url": "https://provider.example/authorized-playlist.m3u",
  "default_language_code": "en",
  "default_country_code": "US",
  "default_category": "General",
  "validate_urls": true,
  "replace_missing": true
}
```

```text
POST /sources/{source_id}/sync
GET /sources
DELETE /sources/{source_id}
```

`replace_missing=true` is intended for complete playlist snapshots. Stale records are reconciled only after a non-empty sync with no parse/validation/database warnings, so a failed or questionable refresh does not wipe the existing catalog.

Server file paths are disabled by default. To use `file_path`, set `ALLOW_SERVER_FILE_IMPORT=true` and place files under `ALLOWED_IMPORT_ROOT`.

## Queries

```text
GET /channels?lang=ru&country=TR&page=1&page_size=100&sort_by=name
GET /channels?category=News&search=international
GET /vod/search?q=matrix&media_type=movie&lang=en
GET /catalog/facets
GET /catalog/facets?country=IN
GET /playlist.m3u?country=IN
GET /playlist.m3u?category=Kids&country=US
```

Channel responses contain a primary `stream_url` plus up to five `alternative_stream_urls` when matching channel identities were imported from multiple sources. `POST /channels/{channel_id}/playback-sources` is an on-demand health check; the dashboard starts a validated stream directly rather than blocking playback on that extra network request, then rechecks after local playback failures.

## Source configuration

`ChannelScraper.load_from_source_config()` accepts a JSON array shaped like `config/sources.example.json`. This is suitable for a scheduler or queue worker. For production, run periodic source imports in Celery/RQ/Arq workers rather than inside API request workers.

## Production notes

- Replace automatic `create_all` with versioned Alembic migrations after the schema stabilizes.
- Restrict `CORS_ORIGINS` instead of using `*` when deploying a known frontend.
- Protect `/import-m3u` with authentication and rate limits before exposing it publicly.
- Protect `/sources` and source-sync endpoints with authentication, authorization, audit logs, and encrypted secret storage.
- Review remote source URLs and private-network access to mitigate SSRF in internet-facing deployments.
- Put API instances behind a reverse proxy/load balancer and run multiple Uvicorn workers.

## Tests

```powershell
pytest -q
```

## Public catalog, synthetic benchmark, and player library

The local runner sets `SEED_PUBLIC_CATALOG=true` only when starting with no database, or when `-RefreshCatalog` is specified, and synchronizes `https://iptv-org.github.io/iptv/index.m3u`. Country metadata is read from playlist attributes or inferred from IPTV-org IDs; language metadata is matched against IPTV-org's separate language directory. A complete usable synchronization removes synthetic and legacy US-only rows; failed, empty, sharply truncated, or implausibly small refreshes preserve the existing active catalog. A validated snapshot must retain at least 5% of its parsed entries before stale records are reconciled.

Synthetic benchmark mode is opt-in through `SEED_DEMO_DATA=true` or `scripts/run_demo.ps1 -SyntheticCatalog`. It uses public, browser-CORS-enabled playback-test manifests referenced by the hls.js test catalog, Mux, Shaka Player, and JW Player test assets. Generated labels and artwork are capacity-test metadata, not commercial channel listings.

The IPTV-org repository is published under the Unlicense. Its maintainers state that no video files are stored there and provide a takedown process for disputed links. Review the upstream project and each stream operator's terms before production use.

The vendored hls.js license is stored at `app/static/vendor/hls.js.LICENSE`.