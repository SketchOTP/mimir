"""Create a Mimir backup archive.

Usage:
    python -m mimir.backup.create
    python -m mimir.backup.create --out /path/to/backups/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import zipfile
from datetime import datetime, UTC
from pathlib import Path

from mimir.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _get_alembic_version(db_path: Path) -> str | None:
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


async def create_backup(out_dir: Path | None = None) -> Path:
    settings = get_settings()
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = out_dir or settings.data_dir / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / f"mimir_backup_{ts}.zip"

    db_path = settings.data_dir / "mimir.db"
    migration_version = _get_alembic_version(db_path)

    manifest = {
        "version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "migration_version": migration_version,
        "data_dir": str(settings.data_dir),
        "vector_dir": str(settings.vector_dir),
    }

    logger.info("Creating backup → %s", archive_path)

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        # SQLite DB
        if db_path.exists():
            zf.write(db_path, "mimir.db")
            logger.info("  Added mimir.db (%d KB)", db_path.stat().st_size // 1024)
        else:
            logger.warning("  DB file not found: %s", db_path)

        # Vector store (ChromaDB persistent directory)
        if settings.vector_dir.exists():
            for vec_file in settings.vector_dir.rglob("*"):
                if vec_file.is_file():
                    rel = vec_file.relative_to(settings.data_dir)
                    zf.write(vec_file, str(rel))
            logger.info("  Added vector store from %s", settings.vector_dir)
        else:
            logger.warning("  Vector dir not found: %s", settings.vector_dir)

    size_kb = archive_path.stat().st_size // 1024
    logger.info("Backup complete: %s (%d KB)", archive_path, size_kb)
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Mimir backup archive")
    parser.add_argument("--out", type=Path, help="Output directory (default: data/backups/)")
    args = parser.parse_args()
    path = asyncio.run(create_backup(out_dir=args.out))
    print(f"Backup saved to: {path}")


if __name__ == "__main__":
    main()
