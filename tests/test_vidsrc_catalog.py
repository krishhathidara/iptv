import gzip

import httpx
import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import VidsrcTitle
from app.services.vidsrc_catalog import resolve_vidsrc_titles, sync_vidsrc_catalog
from app.services.vidsrc_titles import enrich_vidsrc_titles


@pytest.mark.asyncio
async def test_sync_all_advertised_pages_and_filter_titles(client):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/ids/movie_imdb.txt":
            return httpx.Response(200, text="tt1234567\ntt7654321\ntt9999999\ninvalid\ntt1234567\n")
        if request.url.path == "/ids/tv_imdb.txt":
            return httpx.Response(200, text="tt2345678\n")
        if request.url.path == "/movies/latest/page-1.json":
            return httpx.Response(200, json={"pages": 2, "result": [
                {"title": "A Film 2024", "imdb_id": "tt1234567", "embed_url": "https://untrusted.invalid/"},
                {"title": "Invalid ID", "imdb_id": "http://bad.example"},
            ]})
        if request.url.path == "/movies/latest/page-2.json":
            return httpx.Response(200, json={"pages": 2, "result": [
                {"title": "Another Film", "imdb_id": "tt7654321"},
                {"title": "A Film 2024", "imdb_id": "tt1234567"},
            ]})
        if request.url.path == "/tvshows/latest/page-1.json":
            return httpx.Response(200, json={"pages": 1, "result": [
                {"title": "A Show (TV Series)", "imdb_id": "tt2345678"},
            ]})
        if request.url.path == "/info/movie/tt9999999.json":
            return httpx.Response(200, json={"status_code": 200, "title": "Resolved Film"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        async with AsyncSessionLocal() as session:
            first = await sync_vidsrc_catalog(session, client=upstream)
            again = await sync_vidsrc_catalog(session, client=upstream)
            unnamed = (await client.get("/vod/vidsrc/search", params={"q": "tt9999999"})).json()
            rows = await resolve_vidsrc_titles(session, [unnamed["items"][0]["id"]], client=upstream)
    assert first == (4, 4, 0, 4)
    assert again == (4, 0, 4, 4)
    assert rows[0].title == "Resolved Film"
    assert len(paths) == 11 and "/movies/latest/page-2.json" in paths
    movie = (await client.get("/vod/vidsrc/search", params={"media_type": "movie", "q": "film", "page_size": 1})).json()
    assert movie["total"] == 3 and len(movie["items"]) == 1
    assert movie["items"][0]["embed_url"] == "https://vidsrc.sh/embed/movie/tt1234567"
    tv = (await client.get("/vod/vidsrc/search", params={"media_type": "tv"})).json()
    assert tv["total"] == 1 and tv["items"][0]["embed_url"] == "https://vidsrc.sh/embed/tv/tt2345678"
    assert (await client.get("/vod/vidsrc/search", params={"media_type": "invalid"})).status_code == 422
    assert (await client.get("/vod/search")).json()["total"] == 0


@pytest.mark.asyncio
async def test_sync_failure_preserves_previous_entries(client):
    def good(request):
        if request.url.path.startswith("/ids/"):
            return httpx.Response(200, text="tt1234567\n")
        media_type = "tv" if "tvshows" in request.url.path else "movie"
        return httpx.Response(200, json={"pages": 1, "result": [
            {"title": media_type, "imdb_id": "tt1234567"},
        ]})

    async with AsyncSessionLocal() as session:
        async with httpx.AsyncClient(transport=httpx.MockTransport(good)) as upstream:
            await sync_vidsrc_catalog(session, client=upstream)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as upstream:
            with pytest.raises(httpx.HTTPStatusError):
                await sync_vidsrc_catalog(session, client=upstream)
    assert (await client.get("/vod/vidsrc/search")).json()["total"] == 2


@pytest.mark.asyncio
async def test_rate_limited_recent_feed_still_imports_full_id_lists(client):
    def handler(request):
        if request.url.path == "/ids/movie_imdb.txt":
            return httpx.Response(200, text="tt1111111\ntt2222222\n")
        if request.url.path == "/ids/tv_imdb.txt":
            return httpx.Response(200, text="tt3333333\n")
        if request.url.path == "/movies/latest/page-1.json":
            return httpx.Response(200, json={"pages": 2, "result": [
                {"title": "Named Movie", "imdb_id": "tt1111111"}
            ]})
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        async with AsyncSessionLocal() as session:
            result = await sync_vidsrc_catalog(session, client=upstream)
    assert result == (3, 3, 0, 3)
    assert (await client.get("/vod/vidsrc/search", params={"q": "named"})).json()["total"] == 1
    assert (await client.get("/vod/vidsrc/search", params={"q": "tt2222222"})).json()["total"] == 1


@pytest.mark.asyncio
async def test_official_imdb_enrichment_makes_older_vidsrc_ids_searchable(client):
    async with AsyncSessionLocal() as session:
        session.add_all([
            VidsrcTitle(imdb_id="tt0848228", media_type="movie", title="Movie · tt0848228",
                        normalized_title="movie · tt0848228", embed_url="https://vidsrc.sh/embed/movie/tt0848228"),
            VidsrcTitle(imdb_id="tt4154796", media_type="movie", title="Already resolved",
                        normalized_title="already resolved", embed_url="https://vidsrc.sh/embed/movie/tt4154796"),
            VidsrcTitle(imdb_id="tt0848228", media_type="tv", title="Tv · tt0848228",
                        normalized_title="tv · tt0848228", embed_url="https://vidsrc.sh/embed/tv/tt0848228"),
        ])
        await session.commit()

    before = (await client.get("/vod/vidsrc/search", params={"q": "avengers"})).json()
    assert before["total"] == 0
    tsv = ("tconst\ttitleType\tprimaryTitle\toriginalTitle\n"
           "tt0848228\tmovie\tThe Avengers\tThe Avengers\n"
           "tt4154796\tmovie\tAvengers: Endgame\tAvengers: Endgame\n")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=gzip.compress(tsv.encode())))
    async with httpx.AsyncClient(transport=transport) as upstream:
        async with AsyncSessionLocal() as session:
            assert await enrich_vidsrc_titles(session, client=upstream) == 2
            assert await enrich_vidsrc_titles(session, client=upstream) == 0

    movies = (await client.get("/vod/vidsrc/search", params={"q": "avengers", "media_type": "movie"})).json()
    assert movies["total"] == 1
    assert movies["items"][0]["title"] == "The Avengers"
    assert movies["items"][0]["embed_url"] == "https://vidsrc.sh/embed/movie/tt0848228"
    by_id = (await client.get("/vod/vidsrc/search", params={"q": "tt0848228", "media_type": "movie"})).json()
    assert by_id["total"] == 1
    assert by_id["items"][0]["title"] == "The Avengers"
    tv = (await client.get("/vod/vidsrc/search", params={"q": "avengers", "media_type": "tv"})).json()
    assert tv["total"] == 1
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(select(VidsrcTitle).where(VidsrcTitle.imdb_id == "tt4154796"))
        assert existing.title == "Already resolved"


@pytest.mark.asyncio
async def test_imdb_enrichment_failure_keeps_original_titles(client):
    async with AsyncSessionLocal() as session:
        session.add(VidsrcTitle(imdb_id="tt0848228", media_type="movie", title="Movie · tt0848228",
                                normalized_title="movie · tt0848228", embed_url="https://vidsrc.sh/embed/movie/tt0848228"))
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=gzip.compress(b"invalid\tcolumns\n"))
    )) as upstream:
        async with AsyncSessionLocal() as session:
            with pytest.raises(ValueError, match="header"):
                await enrich_vidsrc_titles(session, client=upstream)
    result = (await client.get("/vod/vidsrc/search", params={"q": "tt0848228"})).json()
    assert result["total"] == 1
    assert result["items"][0]["title"] == "Movie · tt0848228"