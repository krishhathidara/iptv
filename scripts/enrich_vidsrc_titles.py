"""One-time personal/non-commercial VidSrc title enrichment via IMDb's public dataset."""

import asyncio

from app.database import AsyncSessionLocal, engine, init_db
from app.services.vidsrc_titles import enrich_vidsrc_titles


async def main() -> None:
    try:
        await init_db()
        async with AsyncSessionLocal() as session:
            count = await enrich_vidsrc_titles(session)
            print(f"VidSrc display names added: {count}", flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())