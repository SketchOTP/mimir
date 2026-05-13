# Security Guide

## Authentication Modes

### Development mode (`MIMIR_AUTH_MODE=dev` or `MIMIR_ENV=development`)

All requests are accepted and mapped to the synthetic `dev` user.  
**Never use in production.**

### Production mode (`MIMIR_AUTH_MODE=prod`)

Requests must include `X-API-Key: <key>` header.

- If the key matches `MIMIR_API_KEY`, it is accepted as the `admin` user (legacy).
- Otherwise the key is SHA-256 hashed and looked up in the `api_keys` table.
- API keys are stored as SHA-256 hashes — the plaintext is never logged or returned after creation.

---

## API Key Management

### Create a user API key

```bash
# Via API
curl -X POST http://localhost:8787/api/auth/keys \
  -H "X-API-Key: $MIMIR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-integration"}'
```

The response includes the plaintext key **once only** — store it immediately.

### Rotate API keys

```bash
# 1. Create a new key
curl -X POST http://localhost:8787/api/auth/keys \
  -H "X-API-Key: $MIMIR_API_KEY" \
  -d '{"name": "my-integration-v2"}'

# 2. Update your integrations to use the new key

# 3. Revoke the old key
curl -X DELETE http://localhost:8787/api/auth/keys/<key-id> \
  -H "X-API-Key: $MIMIR_API_KEY"
```

Also rotate `MIMIR_API_KEY` and `MIMIR_SECRET_KEY` in your environment and restart the service.

---

## Slack Integration

When `SLACK_BOT_TOKEN` is set, all incoming Slack interactive callbacks are verified using HMAC-SHA256 signature against `SLACK_SIGNING_SECRET`.

- Requests older than 5 minutes are rejected (replay protection).
- The signing secret must never be logged or exposed in error messages.

If `SLACK_BOT_TOKEN` is set without `SLACK_SIGNING_SECRET`, startup fails in production.

---

## PWA Push Notifications

VAPID keys authenticate push subscription requests.

- `VAPID_PRIVATE_KEY` must never be logged or exposed.
- Both keys must be set together — startup validates this.
- Generate a fresh key pair: `pywebpush --genkey`

---

## Secret Rules

| Secret | Rule |
|--------|------|
| `MIMIR_SECRET_KEY` | Minimum 32 chars random, stored only in `.env` or vault |
| `MIMIR_API_KEY` | Rotate at least annually; never commit to git |
| `SLACK_SIGNING_SECRET` | Never log, never expose in API responses |
| `VAPID_PRIVATE_KEY` | Never log, never expose in API responses |
| User API keys | Plaintext shown once at creation; hashed at rest |

---

## System Mutation Endpoints

`POST /api/system/consolidate`, `/api/system/reflect`, and `/api/system/lifecycle` trigger system-wide worker passes that affect all users' data.

These endpoints are **disabled by default** in all environments:

```
MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=false   # default
```

Enable for dev/test or scheduled operator use:

```
MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true
```

When disabled, these endpoints return HTTP 403. Auth is still required even when enabled.

---

## What Is Protected

- **Quarantine pipeline**: adversarial content (prompt injection, Tailscale manipulation, credential theft attempts, approval spoofing) is quarantined and never surfaces in retrieval.
- **Quarantine sticky state**: quarantined memories cannot be reactivated via content update (PATCH). Only explicit admin action can clear quarantine. (Fixed P18)
- **Cross-user isolation**: memories are scoped by `user_id` at all retrieval layers (vector, FTS5, SQL). Cross-user leakage causes the release gate to fail.
- **High-trust memory protection**: high-trust identity memories require explicit API call to supersede — they cannot be overwritten by low-trust content.
- **Simulation approval gating**: plans with risk ≥ 0.7 or high-impact keywords require explicit `approve_plan()` before execution.
- **System mutation gating**: consolidate/reflect/lifecycle endpoints disabled by default; require explicit opt-in.

---

## CORS

In production, set `MIMIR_CORS_ORIGINS` to your exact domain(s):

```
MIMIR_CORS_ORIGINS=["https://mimir.yourdomain.com"]
```

Wildcard `*` is rejected at startup in production mode.

---

## Audit Trail

All approval decisions write an `ApprovalAuditLog` row. Quarantine events are logged with reason and `poisoning_flags`. Trust changes write `LifecycleEvent` rows.

---

## Security Scan

Run `make security` to execute the full security scan:

```bash
make security
# Reports saved to reports/security/latest.json
```

Checks:
1. **Python dependency audit** (`pip-audit`) — runtime CVEs only; tool package vulns (pip itself) noted but not blocking
2. **npm audit** — high/critical vulns in web dependencies
3. **Tailscale forbidden commands** — static scan for executable `tailscale up/down/logout/set` in source
4. **Hardcoded credentials** — scan for API key patterns, tokens, secrets
5. **Insecure config defaults** — warns if `secret_key`/`api_key` still at dev defaults

Scan is non-blocking on WARN (dev defaults expected in dev). Fails on FAIL (forbidden commands found, credentials found).

---

## Release Gate Security Checks

The release gate hard-fails on:
- `cross_user_leakage_rate > 0`
- Any red-team adversarial pattern bypass
- `quarantine_exclusion_rate < 1.0`
- `fts_cross_user_leakage_rate > 0`
- `keyword_cross_user_leakage_rate > 0`

See also: `docs/ACCESS_CONTROL_MATRIX.md` for per-endpoint auth, ownership, and risk details.
