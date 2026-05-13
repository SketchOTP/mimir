# Mimir OAuth 2.1 / PKCE Setup

Mimir supports OAuth 2.1 with PKCE (RFC 7636) so Cursor and other MCP clients can authenticate without manually copying API keys.

## Auth Modes

| Mode | `MIMIR_AUTH_MODE` | Use case |
|------|-------------------|----------|
| `dev` | `dev` | Local development — no auth required |
| `single_user` | `single_user` | Personal self-hosted instance |
| `multi_user` | `multi_user` | Team / public deployment |

Legacy: `auth_mode=prod` maps to `multi_user` for backward compatibility.

---

## Quick Start: single_user (Cursor on localhost)

### 1. Set up environment

```bash
# .env
MIMIR_AUTH_MODE=single_user
MIMIR_SECRET_KEY=your-random-secret-here
MIMIR_PUBLIC_URL=http://127.0.0.1:8787
```

Generate a secret: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

### 2. Start Mimir

```bash
docker compose --profile local up
# or: make dev
```

### 3. Create your owner account

```bash
python -m mimir.auth.create_owner \
  --email you@example.com \
  --display-name "Your Name"
```

Copy the API key printed — it's shown only once.

### 4. Add Mimir to Cursor

```json
{
  "mcpServers": {
    "mimir": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

When Cursor connects, it will open your browser to the OAuth authorize page. Enter your API key to grant access. Cursor stores the token — you won't need to enter it again.

### 5. Verify

In Cursor, type a prompt that uses memory tools. Mimir's tools should appear in the tool list.

---

## Quick Start: multi_user (team/server)

### 1. Configure

```bash
# .env
MIMIR_AUTH_MODE=multi_user
MIMIR_SECRET_KEY=<strong-random-secret>
MIMIR_PUBLIC_URL=https://mimir.example.com
MIMIR_ALLOW_REGISTRATION=false
```

### 2. Create owner

```bash
docker compose exec api python -m mimir.auth.create_owner \
  --email admin@example.com \
  --display-name "Admin"
```

### 3. Invite users

Users register via the API (if `MIMIR_ALLOW_REGISTRATION=true`) or are created by the admin:

```bash
curl -X POST https://mimir.example.com/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "user@example.com", "display_name": "User", "key_name": "default"}'
```

The raw API key is returned once. Users can use it in the OAuth flow or directly.

---

## OAuth Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /.well-known/oauth-protected-resource` | RFC 9728 resource metadata |
| `GET /.well-known/oauth-authorization-server` | RFC 8414 server metadata |
| `POST /oauth/register` | Dynamic client registration (RFC 7591) |
| `GET /oauth/authorize` | Authorization form (browser) |
| `POST /oauth/authorize` | Process authorization |
| `POST /oauth/token` | Token exchange (authorization_code, refresh_token) |
| `POST /oauth/revoke` | Token revocation (RFC 7009) |
| `GET /setup` | First-run setup page |

---

## PKCE Flow (what Cursor does automatically)

```
Cursor → POST /mcp (no token)
Mimir  ← 401  WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource"
Cursor → GET /.well-known/oauth-protected-resource
Mimir  ← {authorization_servers: ["http://host:8787"]}
Cursor → GET /.well-known/oauth-authorization-server
Mimir  ← {authorization_endpoint, token_endpoint, registration_endpoint, ...}
Cursor → POST /oauth/register {redirect_uris: [...]}
Mimir  ← {client_id: "mimir-xxx"}
Cursor → opens browser: /oauth/authorize?client_id=...&code_challenge=...&state=...
User   → enters API key, clicks "Authorize"
Mimir  ← 302 redirect to redirect_uri?code=AUTH_CODE&state=STATE
Cursor → POST /oauth/token {grant_type=authorization_code, code=..., code_verifier=...}
Mimir  ← {access_token, token_type=Bearer, expires_in, refresh_token}
Cursor → POST /mcp  Authorization: Bearer ACCESS_TOKEN
Mimir  ← tools/list, tools/call, etc.
```

---

## Token Lifetimes

| Token | Default TTL | Config var |
|-------|-------------|-----------|
| Access token | 1 hour | `MIMIR_ACCESS_TOKEN_TTL_SECONDS` |
| Refresh token | 30 days | `MIMIR_REFRESH_TOKEN_TTL_SECONDS` |

Refresh tokens are rotated on use (single-use).

---

## Manual Bearer fallback

If OAuth is not needed, use a static API key directly:

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

---

## Troubleshooting

**Cursor shows "Authorization Required" repeatedly**  
→ Check that `MIMIR_PUBLIC_URL` matches the URL Cursor is connecting to.

**Browser shows "Setup Required"**  
→ Run `python -m mimir.auth.create_owner` first.

**`INVALID: MIMIR_AUTH_MODE` error on startup**  
→ Set `MIMIR_AUTH_MODE` to `dev`, `single_user`, or `multi_user`.

**401 in multi_user mode with local-dev-key**  
→ Expected — the default dev key is rejected in multi_user mode for security.
