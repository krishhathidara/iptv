import pytest
from html.parser import HTMLParser

from app.database import AsyncSessionLocal
from app.services.demo_data import DEMO_CHANNELS, DEMO_VODS, seed_demo_catalog


class WorkspaceParser(HTMLParser):
    void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
        self.ancestors = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ancestors[attributes["id"]] = tuple(self.stack)
        if tag not in self.void_tags:
            self.stack.append((tag, attributes.get("id")))

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
        else:
            self.errors.append((tag, self.getpos()))


@pytest.mark.asyncio
async def test_dashboard_and_static_assets_are_served(client):
    dashboard = await client.get("/")
    assert dashboard.status_code == 200
    assert "NexaStream" in dashboard.text
    assert "/static/vendor/hls.min.js" in dashboard.text
    assert 'id="liveCategoryFacets"' in dashboard.text
    assert 'id="playlistExportLink"' in dashboard.text
    assert 'id="fullscreenButton"' in dashboard.text
    assert 'data-view-target="india"' in dashboard.text
    assert 'id="indiaNotice"' in dashboard.text
    assert 'https://www.yupptv.com/livetv' in dashboard.text
    assert 'id="vodLanguageFacets"' in dashboard.text
    assert 'id="vodImportForm"' in dashboard.text
    assert 'id="vidsrcGrid"' in dashboard.text
    assert 'id="imdbGrid"' in dashboard.text
    assert 'id="imdbAvailabilityFilter"' in dashboard.text
    parser = WorkspaceParser()
    parser.feed(dashboard.text)
    assert not parser.errors and not parser.stack
    for element_id in ("refreshVodButton", "vodLanguageFacets", "vodImportForm", "vodGrid", "vidsrcGrid", "syncVidsrcButton", "imdbGrid", "imdbAvailabilityFilter"):
        assert ("section", "vodView") in parser.ancestors[element_id]
        assert ("section", "liveView") not in parser.ancestors[element_id]

    script = await client.get("/static/js/dashboard.js")
    assert script.status_code == 200
    assert "Hls.isSupported" in script.text
    assert "failOrTryNext" in script.text
    assert "No other matching live source is available" in script.text
    assert "/playback-sources" in script.text
    assert "attemptFailures" in script.text
    assert 'startPlayback(0);' in script.text
    assert 'data-facet-type="live-category"' in script.text
    assert 'ArrowRight' in script.text
    assert '"/catalog/facets?country=IN"' in script.text
    assert 'menuOpen ? elements.sidebar : document' in script.text
    assert 'document.body.classList.add("tv-remote")' in script.text
    assert 'view === "india" ? "IN"' in script.text
    assert "did not deliver playable video in time" in script.text
    assert '"vod-language"' in script.text
    assert 'apiFetch("/vod/import"' in script.text
    assert 'state.player.kind === "vod" && /\\.(mp4|webm)' in script.text
    assert 'data-play-vidsrc=' in script.text
    assert 'elements.embedPlayer.src = item.embed_url' in script.text
    assert 'elements.embedPlayer.removeAttribute("src")' in script.text
    assert 'data-play-imdb=' in script.text
    assert 'if (item?.embed_url) openVidsrcPlayer' in script.text
    assert '/vod/imdb/search?' in script.text

    stylesheet = await client.get("/static/css/dashboard.css")
    assert stylesheet.status_code == 200
    assert ".channel-grid" in stylesheet.text
    assert ".india-notice[hidden]" in stylesheet.text
    assert 'body.tv-remote .nav-item' in stylesheet.text


@pytest.mark.asyncio
async def test_demo_catalog_is_idempotent_and_browsable(client):
    async with AsyncSessionLocal() as session:
        first = await seed_demo_catalog(session)
        second = await seed_demo_catalog(session)

    assert first == (len(DEMO_CHANNELS), len(DEMO_VODS))
    assert second == (0, 0)

    facets = await client.get("/catalog/facets")
    assert facets.status_code == 200
    payload = facets.json()
    assert payload["live"]["total"] == len(DEMO_CHANNELS)
    assert payload["vod"]["total"] == len(DEMO_VODS)
    assert {item["value"] for item in payload["live"]["languages"]} == {"en", "fr"}
    assert {item["value"] for item in payload["vod"]["media_types"]} == {"movie", "tv"}

    all_vod = await client.get("/vod/search", params={"sort_by": "year", "sort_direction": "desc"})
    assert all_vod.status_code == 200
    assert all_vod.json()["total"] == len(DEMO_VODS)
    release_years = [item["release_year"] for item in all_vod.json()["items"]]
    assert release_years == sorted(release_years, reverse=True)

    french_tv = await client.get(
        "/vod/search",
        params={"media_type": "tv", "lang": "fr", "country": "FR"},
    )
    assert french_tv.status_code == 200
    assert french_tv.json()["total"] == 1
    assert french_tv.json()["items"][0]["title"] == "ARTE Streaming Test"