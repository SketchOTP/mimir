# Mimir — Public GitHub / Self-Hosting Setup

This guide is for users who clone Mimir from GitHub and want to run it as a remote MCP server for Cursor.

## 1-Minute Setup (local, single user)

```bash
git clone https://github.com/youruser/mimir
cd mimir

# Generate a secret key
export SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Start with Docker Compose
MIMIR_AUTH_MODE=single_user \
MIMIR_SECRET_KEY=$SECRET \
MIMIR_PUBLIC_URL=http://127.0.0.1:8787 \
docker compose --profile local up -d

# Create your account
docker compose exec api python -m mimir.auth.create_owner \
  --email you@example.com \
  --display-name "Your Name"
```

Add to Cursor:
```json
{
  "mcpServers": {
    "mimir": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

Open Cursor → authorize in browser → done.

---

## Prerequisites

- Docker + Docker Compose (for Docker-based setup)
- OR: Python 3.12+, pip

## No Docker (manual)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
source .venv/bin/activate

# Config
cat > .env <<EOF
MIMIR_AUTH_MODE=single_user
MIMIR_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
MIMIR_PUBLIC_URL=http://127.0.0.1:8787
EOF

# Migrate DB
alembic upgrade head

# Create owner
python -m mimir.auth.create_owner --email you@example.com --display-name "You"

# Start
make dev     # API on :8787
```

---

## What Happens on First Connection

1. Cursor detects Mimir's MCP URL
2. Mimir returns 401 with OAuth discovery metadata
3. Cursor fetches `/.well-known/oauth-authorization-server`
4. Cursor registers as a client and opens `/oauth/authorize` in your browser
5. You enter your API key → Cursor gets an OAuth token
6. Cursor stores the token — future connections are automatic

---

## Server Deployment (team use)

See [SELF_HOSTING.md](SELF_HOSTING.md) for full server deployment with Postgres, HTTPS, and multi-user setup.

For team deployments:
- Set `MIMIR_AUTH_MODE=multi_user`
- Use a real domain with HTTPS
- Create an owner account, then add team members via `/api/auth/register`

---

## Security Notes

- Secrets are never stored in code or configuration files
- API keys are hashed (SHA-256) before storage, shown only once
- OAuth tokens expire (default: 1h access, 30d refresh)
- Refresh tokens are rotated on use
- Registration is disabled by default in multi_user mode
- The default `local-dev-key` is rejected in production auth modes

See [SECURITY.md](SECURITY.md) and [MULTI_USER_SECURITY.md](MULTI_USER_SECURITY.md) for full details.
