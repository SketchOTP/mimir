"""Load / soak test for Mimir.

Exercises concurrent memory writes, recall, worker consolidation, graph traversal,
and simulation runs across N simulated users and M sessions.

Usage:
    python -m evals.load_test --users 10 --sessions 50 --out reports/load/latest.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import traceback
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from storage.database import init_db, get_session_factory
import memory.semantic_store as _semantic_store
import memory.episodic_store as _episodic_store
from retrieval.retrieval_engine import search as retrieval_search
from context.context_builder import build as build_context
from worker.tasks import run_consolidation_pass

# ── Sample payloads ───────────────────────────────────────────────────────────

_QUERIES = [
    "What is the deployment process?",
    "How do I roll back a failed migration?",
    "What are the security policies for credential handling?",
    "How does the episodic memory layer work?",
    "What are the backup procedures?",
    "How do I rotate an API key?",
    "What happened during the last incident?",
    "How do I run the test suite?",
]

_CONTENT_TEMPLATES = [
    "User {uid} session {sid}: completed deployment step via automated pipeline.",
    "User {uid} recalled that rollback requires running alembic downgrade.",
    "User {uid} noted that credentials should never be stored in plaintext.",
    "User {uid} observed that episodic chains accumulate over multiple sessions.",
    "User {uid} ran backup at session {sid} — backup completed in 1.2s.",
    "User {uid} rotated API key at session {sid} using the auth endpoint.",
    "User {uid} session {sid}: incident resolved by reverting to previous build.",
    "User {uid}: pytest tests/ passes all {sid} new tests after this session.",
]

# ── Timing helpers ────────────────────────────────────────────────────────────

class _Timer:
    def __init__(self) -> None:
        self._samples: list[float] = []

    def record(self, elapsed_s: float) -> None:
        self._samples.append(elapsed_s * 1000)  # store ms

    @property
    def p50(self) -> float:
        return statistics.median(self._samples) if self._samples else 0.0

    @property
    def p95(self) -> float:
        if not self._samples:
            return 0.0
        s = sorted(self._samples)
        idx = int(len(s) * 0.95)
        return s[min(idx, len(s) - 1)]

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def errors(self) -> int:
        return self._error_count

    def __init__(self) -> None:
        self._samples: list[float] = []
        self._error_count: int = 0

    def record_error(self) -> None:
        self._error_count += 1


# ── Workload tasks ────────────────────────────────────────────────────────────

async def _write_memory(session: Any, user_id: str, session_id: str, index: int) -> None:
    content = _CONTENT_TEMPLATES[index % len(_CONTENT_TEMPLATES)].format(
        uid=user_id, sid=session_id
    )
    await _semantic_store.store(session, content=content, user_id=user_id, project="load_test")


async def _recall_memory(session: Any, user_id: str, index: int) -> None:
    query = _QUERIES[index % len(_QUERIES)]
    await retrieval_search(session, query=query, user_id=user_id, project="load_test")


async def _orchestrated_recall(session: Any, user_id: str, index: int) -> None:
    query = _QUERIES[index % len(_QUERIES)]
    await build_context(
        session,
        query=query,
        user_id=user_id,
        project="load_test",
        token_budget=1024,
    )


async def _run_user_session(
    user_index: int,
    sessions: int,
    timers: dict[str, _Timer],
    errors: list[str],
) -> None:
    user_id = f"load_user_{user_index}"
    factory = get_session_factory()

    for s in range(sessions):
        session_id = f"s{s}"

        # Write — fresh session per operation to avoid cascading failures
        t0 = time.perf_counter()
        try:
            async with factory() as session:
                await _write_memory(session, user_id, session_id, s)
            timers["write"].record(time.perf_counter() - t0)
        except Exception as exc:
            timers["write"].record_error()
            errors.append(f"write error user={user_id} session={s}: {exc}")

        # Raw recall
        t0 = time.perf_counter()
        try:
            async with factory() as session:
                await _recall_memory(session, user_id, s)
            timers["recall"].record(time.perf_counter() - t0)
        except Exception as exc:
            timers["recall"].record_error()
            errors.append(f"recall error user={user_id}: {exc}")

        # Orchestrated recall (every 5th session to avoid excess overhead)
        if s % 5 == 0:
            t0 = time.perf_counter()
            try:
                async with factory() as session:
                    await _orchestrated_recall(session, user_id, s)
                timers["orchestrated_recall"].record(time.perf_counter() - t0)
            except Exception as exc:
                timers["orchestrated_recall"].record_error()
                errors.append(f"orchestrated_recall error user={user_id}: {exc}")


# ── Main runner ───────────────────────────────────────────────────────────────

async def run_load_test(
    users: int = 10,
    sessions: int = 50,
    out_path: Path | None = None,
) -> dict:
    await init_db()

    timers: dict[str, _Timer] = {
        "write": _Timer(),
        "recall": _Timer(),
        "orchestrated_recall": _Timer(),
        "consolidation": _Timer(),
    }
    errors: list[str] = []

    print(f"[load_test] Starting: {users} users × {sessions} sessions")
    wall_start = time.perf_counter()

    # Run all users concurrently
    await asyncio.gather(
        *[_run_user_session(u, sessions, timers, errors) for u in range(users)],
        return_exceptions=False,
    )

    # Run a consolidation pass and time it
    t0 = time.perf_counter()
    try:
        await run_consolidation_pass(project="load_test")
        timers["consolidation"].record(time.perf_counter() - t0)
    except Exception as exc:
        timers["consolidation"].record_error()
        errors.append(f"consolidation error: {exc}")

    wall_elapsed = time.perf_counter() - wall_start

    # Measure DB and vector size
    from mimir.config import get_settings
    settings = get_settings()
    db_size_kb = (settings.data_dir / "mimir.db").stat().st_size // 1024 if (settings.data_dir / "mimir.db").exists() else 0
    vec_size_kb = sum(f.stat().st_size for f in settings.vector_dir.rglob("*") if f.is_file()) // 1024 if settings.vector_dir.exists() else 0

    report: dict = {
        "created_at": datetime.now(UTC).isoformat(),
        "config": {"users": users, "sessions": sessions},
        "wall_time_s": round(wall_elapsed, 2),
        "latency_ms": {
            name: {
                "p50": round(t.p50, 1),
                "p95": round(t.p95, 1),
                "count": t.count,
                "errors": t.errors,
            }
            for name, t in timers.items()
        },
        "error_rate": round(sum(t.errors for t in timers.values()) / max(1, sum(t.count + t.errors for t in timers.values())), 4),
        "errors": errors[:20],  # cap at 20 for readability
        "storage": {
            "db_size_kb": db_size_kb,
            "vector_size_kb": vec_size_kb,
        },
        "passed": len(errors) == 0,
    }

    # Print summary
    print(f"[load_test] Done in {wall_elapsed:.1f}s — error_rate={report['error_rate']:.1%}")
    for name, stats in report["latency_ms"].items():
        print(f"  {name:25s}  p50={stats['p50']:7.1f}ms  p95={stats['p95']:7.1f}ms  n={stats['count']}  err={stats['errors']}")
    print(f"  DB size: {db_size_kb} KB   Vector size: {vec_size_kb} KB")

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"[load_test] Report saved to {out_path}")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Mimir load / soak test")
    parser.add_argument("--users", type=int, default=10, help="Concurrent simulated users")
    parser.add_argument("--sessions", type=int, default=50, help="Sessions per user")
    parser.add_argument("--out", type=Path, default=Path("reports/load/latest.json"),
                        help="Output JSON report path")
    args = parser.parse_args()
    asyncio.run(run_load_test(users=args.users, sessions=args.sessions, out_path=args.out))


if __name__ == "__main__":
    main()
