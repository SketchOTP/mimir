# Mimir Architecture

## Overview

```
Host AI App  →  MCP Server / REST API / SDK  →  Mimir Service
                                                     ↓
                                           Memory + Reflection + Skill Engine
                                                     ↓
                                           SQLite + ChromaDB (Vector)
                                                     ↓
                                           Web UI / PWA / Notifications
```

## Memory Layers

| Layer | ID Prefix | Purpose |
|-------|-----------|---------|
| Episodic | `ep_` | Events, conversations, task outcomes |
| Semantic | `sm_` | Durable facts, preferences, rules |
| Procedural | `pr_` | Workflows, skills, behavior rules |
| Working | `wk_` | Current session context only |

## Key Design Decisions

- **No full-history prompting**: context is built via retrieval, not replaying all events
- **Token budget enforced**: `context_builder.py` + `token_budgeter.py` trim to fit
- **Importance-weighted retrieval**: recency + importance + semantic score combined
- **Approval before promotion**: no silent behavioral changes
- **Automatic rollback**: if success rate degrades >15% after promotion

## Storage

- **SQLite** (async via aiosqlite): all structured data
- **ChromaDB** (local persistence): vector embeddings for semantic search
- **Embeddings**: `all-MiniLM-L6-v2` via sentence-transformers (local, no API key needed)

## Services

| Service | Command | Port |
|---------|---------|------|
| API | `make api` | 8787 |
| Web UI | `make web` | 5173 |
| MCP Server | `make mcp` | stdio |
| Worker | `make worker` | — |

## MCP Config

```json
{
  "mcpServers": {
    "mimir": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "env": {
        "MIMIR_URL": "http://127.0.0.1:8787",
        "MIMIR_API_KEY": "local-dev-key"
      }
    }
  }
}
```

## Self-Improvement Loop

```
observe → reflect → propose → test → score → notify → approve → promote → monitor → rollback if worse
```

Managed by:
- `reflections/reflection_engine.py` — generates reflections
- `reflections/improvement_planner.py` — converts to proposals
- `approvals/approval_queue.py` — human gate
- `approvals/promotion_worker.py` — applies approved changes
- `approvals/rollback_watcher.py` — reverts degraded changes
- `worker/scheduler.py` — runs all of the above on schedule
