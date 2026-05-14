# Mimir Self-Hosting Guide

## Docker Compose — Local (SQLite)

```bash
# Clone and start
git clone https://github.com/youruser/mimir
cd mimir
cp .env.example .env   # or create .env manually (see below)
docker compose --profile local up
```

### Minimal .env for local single-user

```env
MIMIR_AUTH_MODE=single_user
MIMIR_SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(32))">
MIMIR_PUBLIC_URL=http://127.0.0.1:8787
```

### Create owner account

```bash
docker compose exec api python -m mimir.auth.create_owner \
  --email you@example.com \
  --display-name "Your Name"
```

Copy the printed API key (shown once).

---

## Docker Compose — Production (Postgres)

```bash
cp .env.example .env.prod
# Edit .env.prod (see below)
docker compose --profile prod-postgres --env-file .env.prod up
```

### .env.prod for multi-user Postgres

```env
MIMIR_AUTH_MODE=multi_user
MIMIR_ENV=production
MIMIR_SECRET_KEY=<strong-random-secret>
MIMIR_PUBLIC_URL=https://mimir.example.com
MIMIR_DATABASE_URL=postgresql://mimir:password@postgres:5432/mimir
MIMIR_ALLOW_REGISTRATION=false
MIMIR_DB_POOL_SIZE=20
```

---

## Without Docker

```bash
# Install
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env

# Migrate DB
source .venv/bin/activate && alembic upgrade head

# Create owner
python -m mimir.auth.create_owner --email admin@example.com --display-name Admin

# Run services
make dev      # API on :8787
make web      # UI on :5173 (optional)
make worker   # background worker (optional)
```

---

## Nginx / Reverse Proxy

```nginx
server {
    listen 443 ssl;
    server_name mimir.example.com;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # Required for SSE (MCP streaming)
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MIMIR_AUTH_MODE` | `""` | `dev` / `single_user` / `multi_user` |
| `MIMIR_SECRET_KEY` | `change-me` | JWT/token signing secret |
| `MIMIR_PUBLIC_URL` | `""` | Public base URL (required for OAuth) |
| `MIMIR_ENV` | `development` | `development` / `production` |
| `MIMIR_ALLOW_REGISTRATION` | `false` | Allow open user registration |
| `MIMIR_REQUIRE_HTTPS` | `true` | Enforce HTTPS in multi_user mode |
| `MIMIR_OAUTH_ENABLED` | `true` | Enable OAuth endpoints |
| `MIMIR_ACCESS_TOKEN_TTL_SECONDS` | `3600` | Access token lifetime |
| `MIMIR_REFRESH_TOKEN_TTL_SECONDS` | `2592000` | Refresh token lifetime (30d) |
| `MIMIR_DATABASE_URL` | `""` | Postgres URL (empty = SQLite) |
| `MIMIR_DATA_DIR` | `./data` | SQLite + backup storage path |
| `MIMIR_VECTOR_DIR` | `./data/vectors` | ChromaDB vector index path |

See [DEPLOYMENT.md](DEPLOYMENT.md) for the full variable reference.

---

## Cursor MCP Configuration

For normal local/browser-capable Cursor setups, add:

```json
{
  "mcpServers": {
    "mimir": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

Cursor will open a browser window for OAuth authentication on first use.

If you use OAuth, `MIMIR_PUBLIC_URL` must be reachable from the machine running Cursor, not just from the server itself.

For Cursor over SSH, headless clients, remote development, and RPi5 workflows, use API-key Bearer auth instead:

```json
{
  "mcpServers": {
    "mimir": {
      "url": "http://127.0.0.1:8787/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

For a server deployment:

```json
{
  "mcpServers": {
    "mimir": {
      "url": "https://mimir.example.com/mcp"
    }
  }
}
```

---

## Health Check

```bash
curl http://127.0.0.1:8787/health
# → {"status": "ok", "service": "mimir", "version": "0.1.0-rc1"}

curl http://127.0.0.1:8787/api/system/readiness
# → {"ready": true, "checks": {...}}
```
