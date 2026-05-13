# Mimir — Installation

## Quick Start (any environment)

```bash
git clone <repo-url> mimir
cd mimir

make install-dev   # creates .venv and installs all dependencies
source .venv/bin/activate

make migrate       # run Alembic migrations (creates SQLite + ChromaDB dirs)
make dev           # API on :8787
```

## Managed-environment Linux (Debian/Ubuntu)

Ubuntu 23.04+ and Debian 12+ mark the system Python as externally managed.
Running bare `pip install` fails with:

```
error: externally-managed-environment
```

Use `make install-dev` which always creates a `.venv` first:

```bash
make install-dev
source .venv/bin/activate
```

If you prefer to manage the venv manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Dev vs Production dependencies

| Command | Installs | Use for |
|---------|----------|---------|
| `make install-dev` | `mimir[dev]` (includes pytest, ruff) | local development |
| `pip install -e .` | `mimir` only | Docker / production |

## Web UI

```bash
cd web
npm install
npm run build     # production build → web/dist/
npm run dev       # dev server on :5173
```

## Docker

```bash
docker compose up --build
```

The API runs on `:8787` and the web UI is served from `/`.

## Environment Variables

Copy `.env.example` to `.env` and edit as needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `MIMIR_DATA_DIR` | `./data` | SQLite database directory |
| `MIMIR_VECTOR_DIR` | `./data/vectors` | ChromaDB vector store |
| `MIMIR_API_KEY` | `local-dev-key` | API key for all endpoints |
| `MIMIR_PORT` | `8787` | HTTP port |
| `VAPID_PRIVATE_KEY` | _(empty)_ | PWA push notifications |
| `VAPID_PUBLIC_KEY` | _(empty)_ | PWA push notifications |
| `SLACK_BOT_TOKEN` | _(empty)_ | Slack approval notifications |

Leave `VAPID_*` and `SLACK_BOT_TOKEN` empty to disable those channels (notifications are recorded as `stubbed` instead).

## MCP Config

Add to your Claude / AI client config:

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
