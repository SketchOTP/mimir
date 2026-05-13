# Mimir

**Self-hostable AI memory server for Cursor and any MCP-compatible AI agent.**

Mimir gives your AI assistant persistent, structured memory — episodic, semantic, procedural, and graph-linked — accessible via MCP, REST API, or Python SDK. Connect Cursor in 60 seconds with URL-only OAuth.

---

## Features

- **MCP over Streamable HTTP** — Cursor connects with a single URL, no local package install
- **OAuth 2.1 / PKCE** — browser-based auth flow, no manual API key copy-paste
- **Three auth modes** — `dev` (no auth), `single_user` (local), `multi_user` (teams)
- **Multi-layer memory** — semantic, episodic, procedural, graph nodes/edges
- **Per-user isolation** — every query scoped by `user_id` across all layers
- **Background learning** — consolidation, reflection, lifecycle aging, graph building
- **React PWA** — dashboard, memory browser, approvals, telemetry, simulation planner
- **SQLite or Postgres** — single-file local or production Postgres with connection pooling

---

## Quick Start (Docker, 60 seconds)

```bash
git clone https://github.com/SketchOTP/mimir
cd mimir

# Generate a secret key
export SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Start (SQLite + single_user auth)
MIMIR_SECRET_KEY=$SECRET \
MIMIR_PUBLIC_URL=http://127.0.0.1:8787 \
docker compose --profile local up -d

# Create your account (API key shown once — save it)
docker compose exec api python -m mimir.auth.create_owner \
  --email you@example.com \
  --display-name "Your Name"
```

Add to Cursor (**Settings → MCP → Add Server**):

```json
{
  "mcpServers": {
    "mimir": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

Cursor opens a browser → enter your API key → authorize. Done.

---

## Quick Start (No Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Edit .env: set MIMIR_SECRET_KEY, MIMIR_AUTH_MODE=single_user, MIMIR_PUBLIC_URL

alembic upgrade head
python -m mimir.auth.create_owner --email you@example.com --display-name "You"
make dev    # API on :8787
make web    # UI on :5173 (optional)
```

---

## Connecting to Cursor

See [docs/CURSOR_MCP_SETUP.md](docs/CURSOR_MCP_SETUP.md) for full setup, OAuth flow details, and manual Bearer fallback.

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `memory.remember` | Store an event or fact |
| `memory.recall` | Retrieve relevant memories |
| `memory.search` | Semantic search across all layers |
| `memory.record_outcome` | Record task outcome for learning |
| `skill.list` | List available skills |
| `approval.request` | Request approval for an action |
| `approval.status` | Check pending approvals |
| `reflection.log` | Log an observation or lesson |
| `improvement.propose` | Propose a system improvement |

---

## Self-Hosting

- **Local (SQLite):** [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md)
- **Production (Postgres):** [docs/SELF_HOSTING.md#production](docs/SELF_HOSTING.md)
- **Multi-user / Teams:** [docs/MULTI_USER_SECURITY.md](docs/MULTI_USER_SECURITY.md)
- **OAuth setup:** [docs/OAUTH_SETUP.md](docs/OAUTH_SETUP.md)

---

## Architecture

```
Cursor / AI Agent
       │ MCP Streamable HTTP (OAuth 2.1)
       ▼
  POST /mcp  ──►  FastAPI (api/)
                       │
         ┌─────────────┼──────────────┐
         ▼             ▼              ▼
   memory/        retrieval/      graph/
   (semantic,      (vector,       (nodes,
   episodic,       keyword,       edges,
   procedural)     identity,      traversal)
                   episodic,
                   graph)
         │             │
         └──────┬───────┘
                ▼
         storage/ (SQLite / Postgres via SQLAlchemy async)
                │
         worker/ (APScheduler: consolidation, reflection,
                  lifecycle aging, graph build, telemetry)
```

---

## Development

```bash
make dev      # API on :8787 (hot reload)
make web      # React UI on :5173
make worker   # APScheduler background worker
make test     # pytest tests/ -v  (647 tests)
make evals    # eval harness (8 suites)
make gate     # release gate (blocks on failures)
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MIMIR_AUTH_MODE` | `""` | `dev` / `single_user` / `multi_user` |
| `MIMIR_SECRET_KEY` | `change-me` | JWT/token signing secret |
| `MIMIR_PUBLIC_URL` | `""` | Public base URL (required for OAuth) |
| `MIMIR_DATABASE_URL` | `""` | Postgres URL (empty → SQLite) |
| `MIMIR_DATA_DIR` | `./data` | SQLite + backup path |
| `MIMIR_ALLOW_REGISTRATION` | `false` | Open user registration |

Full reference: [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md)

---

## Security

See [docs/SECURITY.md](docs/SECURITY.md) and [docs/MULTI_USER_SECURITY.md](docs/MULTI_USER_SECURITY.md).

To report a vulnerability: open a [GitHub Security Advisory](https://github.com/SketchOTP/mimir/security/advisories/new).

---

## License

Apache-2.0 — see [LICENSE](LICENSE).
