"""Rebuild the FTS5 index with full user/project isolation metadata.

Usage:
    python -m storage.reindex_fts
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> int:
    from storage.database import get_session_factory, init_db
    from storage.fts import reindex_fts, reset_fts5_probe

    await init_db()
    reset_fts5_probe()  # force re-probe after migration

    factory = get_session_factory()
    async with factory() as session:
        count = await reindex_fts(session)

    if count >= 0:
        logger.info("FTS reindex complete: %d memories indexed", count)
        return 0
    else:
        logger.error("FTS reindex failed")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
