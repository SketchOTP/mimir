"""DB-backed distributed job locks for multi-worker safety.

Usage:
    async with acquire_lock(session, "consolidation", ttl=300) as locked:
        if not locked:
            return  # another worker holds it
        await do_work()

The lock row's primary key is job_name, so only one lock per job can exist.
A background heartbeat is updated every ttl/3 seconds while the context is held.
Stale locks (expired heartbeat) are automatically reclaimed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from datetime import datetime, UTC, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage.models import JobLock

logger = logging.getLogger(__name__)

_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _expires(ttl_seconds: int) -> datetime:
    return _now() + timedelta(seconds=ttl_seconds)


async def _purge_stale(session: AsyncSession) -> int:
    """Delete locks that have expired (heartbeat stopped, TTL lapsed)."""
    result = await session.execute(
        delete(JobLock).where(JobLock.expires_at < _now())
    )
    return result.rowcount or 0


async def try_acquire(
    session: AsyncSession,
    job_name: str,
    ttl: int = 300,
) -> bool:
    """Attempt to acquire the lock. Returns True if acquired, False if held."""
    # Purge expired locks first so we can claim them
    await _purge_stale(session)
    await session.flush()

    existing = await session.get(JobLock, job_name)
    if existing and existing.status == "locked":
        return False

    if existing:
        # Reclaim released row
        existing.locked_by = _WORKER_ID
        existing.locked_at = _now()
        existing.expires_at = _expires(ttl)
        existing.heartbeat_at = _now()
        existing.status = "locked"
    else:
        lock = JobLock(
            job_name=job_name,
            locked_by=_WORKER_ID,
            locked_at=_now(),
            expires_at=_expires(ttl),
            heartbeat_at=_now(),
            status="locked",
        )
        session.add(lock)

    try:
        await session.flush()
    except Exception:
        await session.rollback()
        return False

    return True


async def release(session: AsyncSession, job_name: str) -> None:
    """Release the lock held by this worker."""
    await session.execute(
        update(JobLock)
        .where(JobLock.job_name == job_name, JobLock.locked_by == _WORKER_ID)
        .values(status="released")
    )
    await session.flush()


async def heartbeat(session: AsyncSession, job_name: str, ttl: int = 300) -> None:
    """Extend the lock TTL — call periodically during long-running jobs."""
    await session.execute(
        update(JobLock)
        .where(JobLock.job_name == job_name, JobLock.locked_by == _WORKER_ID)
        .values(heartbeat_at=_now(), expires_at=_expires(ttl))
    )
    await session.flush()


@asynccontextmanager
async def acquire_lock(
    session: AsyncSession,
    job_name: str,
    ttl: int = 300,
    heartbeat_interval: int | None = None,
):
    """Async context manager that acquires a job lock and yields True/False.

    Usage:
        async with acquire_lock(session, "my_job") as locked:
            if not locked:
                return
            # do work...

    Heartbeat task runs in the background every ttl/3 seconds if a heartbeat
    interval is not specified.
    """
    acquired = await try_acquire(session, job_name, ttl=ttl)
    if not acquired:
        logger.info("Job '%s' locked by another worker — skipping", job_name)
        yield False
        return

    logger.info("Acquired job lock '%s' (worker=%s)", job_name, _WORKER_ID)
    interval = heartbeat_interval or max(10, ttl // 3)
    _stop = asyncio.Event()

    async def _heartbeat_loop():
        while not _stop.is_set():
            try:
                await asyncio.sleep(interval)
                if not _stop.is_set():
                    await heartbeat(session, job_name, ttl=ttl)
                    await session.commit()
            except Exception as exc:
                logger.debug("Heartbeat error for '%s': %s", job_name, exc)

    task = asyncio.create_task(_heartbeat_loop())
    try:
        yield True
    finally:
        _stop.set()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await release(session, job_name)
            await session.commit()
            logger.info("Released job lock '%s'", job_name)
        except Exception as exc:
            logger.warning("Failed to release lock '%s': %s", job_name, exc)


async def get_active_locks(session: AsyncSession) -> list[dict]:
    """Return all currently held (non-expired) lock rows."""
    result = await session.execute(
        select(JobLock)
        .where(JobLock.status == "locked", JobLock.expires_at > _now())
        .order_by(JobLock.locked_at)
    )
    return [
        {
            "job_name": r.job_name,
            "locked_by": r.locked_by,
            "locked_at": r.locked_at.isoformat() if r.locked_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "heartbeat_at": r.heartbeat_at.isoformat() if r.heartbeat_at else None,
        }
        for r in result.scalars()
    ]
