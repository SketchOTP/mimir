"""Restore a Mimir backup archive.

Usage:
    python -m mimir.backup.restore /path/to/mimir_backup_20260101_120000.zip
    python -m mimir.backup.restore /path/to/backup.zip --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import zipfile
from pathlib import Path

from mimir.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def validate_restore(archive_path: Path) -> dict:
    """Validate a backup archive and return the manifest. Raises on failure."""
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    if not zipfile.is_zipfile(archive_path):
        raise ValueError(f"Not a valid zip archive: {archive_path}")

    with zipfile.ZipFile(archive_path, "r") as zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            raise ValueError("Archive is missing manifest.json — not a valid Mimir backup")

        manifest = json.loads(zf.read("manifest.json"))

    required_manifest_keys = ("version", "created_at", "migration_version")
    for key in required_manifest_keys:
        if key not in manifest:
            raise ValueError(f"Manifest missing required field: {key}")

    if "mimir.db" not in names:
        raise ValueError("Archive is missing mimir.db")

    logger.info(
        "Backup manifest: version=%s created_at=%s migration=%s files=%d",
        manifest.get("version"),
        manifest.get("created_at"),
        manifest.get("migration_version"),
        len(names),
    )
    return manifest


async def restore_backup(archive_path: Path, dry_run: bool = False) -> None:
    manifest = await validate_restore(archive_path)
    settings = get_settings()

    logger.info("Restoring from %s%s", archive_path, " [DRY RUN]" if dry_run else "")

    if dry_run:
        logger.info("Dry run — no files written")
        logger.info("Would restore DB to: %s", settings.data_dir / "mimir.db")
        logger.info("Would restore vectors to: %s", settings.vector_dir)
        return

    # Back up existing data before overwriting
    db_path = settings.data_dir / "mimir.db"
    if db_path.exists():
        backup_db = db_path.with_suffix(".db.pre_restore")
        shutil.copy2(db_path, backup_db)
        logger.info("  Saved existing DB to %s", backup_db)

    vec_backup = settings.vector_dir.with_name(settings.vector_dir.name + ".pre_restore")
    if settings.vector_dir.exists():
        if vec_backup.exists():
            shutil.rmtree(vec_backup)
        shutil.copytree(settings.vector_dir, vec_backup)
        logger.info("  Saved existing vectors to %s", vec_backup)

    with zipfile.ZipFile(archive_path, "r") as zf:
        # Restore DB
        zf.extract("mimir.db", settings.data_dir)
        logger.info("  Restored mimir.db → %s", db_path)

        # Restore vector files
        vec_prefix = settings.vector_dir.relative_to(settings.data_dir)
        vec_files = [n for n in zf.namelist() if n.startswith(str(vec_prefix) + "/")]
        if vec_files:
            for name in vec_files:
                zf.extract(name, settings.data_dir)
            logger.info("  Restored %d vector files", len(vec_files))
        else:
            logger.warning("  No vector files found in archive")

    logger.info("Restore complete. Migration version: %s", manifest.get("migration_version"))
    logger.info("Run 'make migrate' to apply any pending migrations if needed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a Mimir backup archive")
    parser.add_argument("archive", type=Path, help="Path to backup .zip file")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing files")
    args = parser.parse_args()

    try:
        asyncio.run(restore_backup(args.archive, dry_run=args.dry_run))
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Restore failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
