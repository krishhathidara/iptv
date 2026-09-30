"""Explicit local IMDb movie subset import for personal, non-commercial use only."""

import asyncio

from app.database import AsyncSessionLocal, engine, init_db
from app.services.imdb_movies import import_imdb_movies


async def main() -> None:
    try:
        await init_db()
        async with AsyncSessionLocal() as session:
            imported = await import_imdb_movies(session)
            print(f"IMDb movie rows indexed: {imported}", flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())