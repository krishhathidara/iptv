"""One-time VidSrc catalog import using the configured DATABASE_URL."""

import asyncio

from app.database import AsyncSessionLocal, engine, init_db
from app.services.vidsrc_catalog import sync_vidsrc_catalog


async def main() -> None:
    try:
        await init_db()
        async with AsyncSessionLocal() as session:
            parsed, inserted, updated, total = await sync_vidsrc_catalog(session)
            print(f"VidSrc indexed: {total} entries ({parsed} parsed, {inserted} new, {updated} existing)", flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())