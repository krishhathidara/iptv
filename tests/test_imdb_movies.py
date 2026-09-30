import gzip

import httpx
import pytest

from app.database import AsyncSessionLocal
from app.services.imdb_movies import import_imdb_movies
from app.services.vidsrc_catalog import sync_vidsrc_catalog


@pytest.mark.asyncio
async def test_import_movie_subset_and_join_only_active_vidsrc_movie_ids(client):
    tsv = ("tconst\ttitleType\tprimaryTitle\toriginalTitle\tisAdult\tstartYear\n"
           "tt0848228\tmovie\tThe Avengers\tThe Avengers\t0\t2012\n"
           "tt4154796\tmovie\tAvengers: Endgame\tAvengers: Endgame\t0\t2019\n"
           "tt7777000\tmovie\tOffline Film\tOffline Film\t0\t\\N\n"
           "tt8888000\tmovie\tAdult Film\tAdult Film\t1\t2010\n"
           "tt6666000\tshort\tShort Film\tShort Film\t0\t2000\n"
           "tt9999000\tmovie\t\\N\t\\N\t0\t2001\n")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=gzip.compress(tsv.encode()))
    )) as imdb:
        async with AsyncSessionLocal() as session:
            assert await import_imdb_movies(session, client=imdb) == 3
            assert await import_imdb_movies(session, client=imdb) == 3

    def vidsrc(request):
        if request.url.path == "/ids/movie_imdb.txt":
            return httpx.Response(200, text="tt0848228\n")
        if request.url.path == "/ids/tv_imdb.txt":
            return httpx.Response(200, text="tt4154796\n")
        return httpx.Response(200, json={"pages": 1, "result": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(vidsrc)) as upstream:
        async with AsyncSessionLocal() as session:
            await sync_vidsrc_catalog(session, client=upstream)

    matched = (await client.get("/vod/imdb/search", params={"q": "avengers", "page_size": 1})).json()
    assert matched["total"] == 1
    assert matched["items"] == [{"imdb_id": "tt0848228", "title": "The Avengers", "release_year": 2012,
                                  "embed_url": "https://vidsrc.sh/embed/movie/tt0848228"}]
    assert (await client.get("/vod/imdb/search", params={"q": "tt0848228"})).json()["items"] == matched["items"]
    assert (await client.get("/vod/imdb/search", params={"q": "tt7777000"})).json()["total"] == 0
    all_movies = (await client.get("/vod/imdb/search", params={"vidsrc_only": "false", "page_size": 1})).json()
    assert all_movies["total"] == 3
    assert len(all_movies["items"]) == 1
    assert (await client.get("/vod/imdb/search", params={"vidsrc_only": "false", "q": "tt7777000"})).json()["items"][0]["embed_url"] is None
    assert (await client.get("/vod/imdb/search", params={"vidsrc_only": "false", "q": "tt4154796"})).json()["items"][0]["embed_url"] is None
    assert (await client.get("/vod/imdb/search", params={"vidsrc_only": "false", "q": "avengers"})).json()["total"] == 2
    assert (await client.get("/vod/imdb/search", params={"vidsrc_only": "false", "q": "short"})).json()["total"] == 0
    assert (await client.get("/vod/imdb/search", params={"page_size": 101})).status_code == 422

    async with AsyncSessionLocal() as session:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: (
            httpx.Response(200, text="tt7777000\n") if request.url.path == "/ids/movie_imdb.txt" else
            httpx.Response(200, text="tt4154796\n") if request.url.path == "/ids/tv_imdb.txt" else
            httpx.Response(200, json={"pages": 1, "result": []})
        ))) as upstream:
            await sync_vidsrc_catalog(session, client=upstream)
    assert (await client.get("/vod/imdb/search", params={"q": "avengers"})).json()["total"] == 0
    assert (await client.get("/vod/imdb/search", params={"q": "offline"})).json()["total"] == 1


@pytest.mark.asyncio
async def test_invalid_imdb_download_rolls_back_entire_import(client):
    tsv = ("tconst\ttitleType\tprimaryTitle\tstartYear\n"
           "tt0848228\tmovie\tThe Avengers\t2012\n")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=gzip.compress(tsv.encode()))
    )) as upstream:
        async with AsyncSessionLocal() as session:
            with pytest.raises(ValueError, match="header"):
                await import_imdb_movies(session, client=upstream)
    assert (await client.get("/vod/imdb/search", params={"vidsrc_only": "false"})).json()["total"] == 0