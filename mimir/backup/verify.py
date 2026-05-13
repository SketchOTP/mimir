"""Verify a Mimir backup archive is well-formed and internally consistent.

Usage:
    python -m mimir.backup.verify /path/to/mimir_backup_20260101_120000.zip
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import zipfile
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REQUIRED_MANIFEST_KEYS = ("version", "created_at", "migration_version")
REQUIRED_TABLES = (
    "memories", "alembic_version",
)


def verify_backup(archive_path: Path) -> dict:
    """Return a verification report dict. Raises ValueError on hard failures."""
    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    if not zipfile.is_zipfile(archive_path):
        raise ValueError(f"Not a valid zip archive: {archive_path}")

    report: dict = {
        "path": str(archive_path),
        "size_kb": archive_path.stat().st_size // 1024,
        "ok": False,
        "checks": [],
    }

    def _check(name: str, passed: bool, detail: str = "") -> bool:
        report["checks"].append({"name": name, "passed": passed, "detail": detail})
        if passed:
            logger.info("  ✓ %s %s", name, f"— {detail}" if detail else "")
        else:
            logger.error("  ✗ %s %s", name, f"— {detail}" if detail else "")
        return passed

    with zipfile.ZipFile(archive_path, "r") as zf:
        names = set(zf.namelist())

        # 1. manifest present and valid
        if "manifest.json" not in names:
            _check("manifest_present", False, "manifest.json not found in archive")
            return report
        manifest = json.loads(zf.read("manifest.json"))
        missing_keys = [k for k in REQUIRED_MANIFEST_KEYS if k not in manifest]
        _check("manifest_valid", not missing_keys,
               f"missing keys: {missing_keys}" if missing_keys else
               f"version={manifest.get('version')} migration={manifest.get('migration_version')}")

        # 2. DB file present
        if not _check("db_present", "mimir.db" in names, "mimir.db not found in archive"):
            return report

        # 3. DB not empty
        db_bytes = zf.read("mimir.db")
        _check("db_non_empty", len(db_bytes) > 0, f"{len(db_bytes)} bytes")

        # 4. DB is a valid SQLite file (magic header)
        sqlite_magic = b"SQLite format 3\x00"
        _check("db_sqlite_magic", db_bytes[:16] == sqlite_magic,
               "header OK" if db_bytes[:16] == sqlite_magic else "invalid SQLite header")

        # 5. Required tables present (read directly from bytes via temp in-memory DB)
        try:
            import tempfile, os
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
                tf.write(db_bytes)
                tmp_path = tf.name
            conn = sqlite3.connect(tmp_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            db_tables = {row[0] for row in cur.fetchall()}

            # Check alembic version matches manifest
            migration_in_db: str | None = None
            if "alembic_version" in db_tables:
                cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
                row = cur.fetchone()
                migration_in_db = row[0] if row else None
            conn.close()
            os.unlink(tmp_path)

            for tbl in REQUIRED_TABLES:
                _check(f"table_{tbl}", tbl in db_tables, f"found={tbl in db_tables}")

            manifest_version = manifest.get("migration_version")
            version_match = migration_in_db == manifest_version
            _check("migration_version_matches",
                   version_match,
                   f"manifest={manifest_version} db={migration_in_db}")
        except Exception as exc:
            _check("db_readable", False, str(exc))

        # 6. Vector files present (optional — warn if absent)
        vec_files = [n for n in names if "vectors/" in n and not n.endswith("/")]
        _check("vector_files_present", len(vec_files) > 0,
               f"{len(vec_files)} vector files" if vec_files else "no vector files (reindex required after restore)")

    all_passed = all(c["passed"] for c in report["checks"])
    report["ok"] = all_passed
    report["manifest"] = manifest
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify a Mimir backup archive")
    parser.add_argument("archive", type=Path, help="Path to backup .zip file")
    args = parser.parse_args()

    logger.info("Verifying backup: %s", args.archive)
    try:
        result = verify_backup(args.archive)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Verify failed: %s", exc)
        sys.exit(1)

    passed = sum(1 for c in result["checks"] if c["passed"])
    total = len(result["checks"])
    logger.info("Result: %d/%d checks passed — %s", passed, total,
                "OK" if result["ok"] else "FAILED")

    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
