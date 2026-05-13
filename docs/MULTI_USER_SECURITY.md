# Mimir Multi-User Security

## Tenant Isolation

Every data layer enforces `user_id` scoping:

| Layer | Isolation method |
|-------|-----------------|
| Semantic memory | `WHERE user_id = :uid` |
| Episodic memory | `WHERE user_id = :uid` |
| FTS5 / keyword search | `WHERE (user_id = :uid OR user_id = '')` |
| Vector search | ChromaDB collection per user (via retrieval engine) |
| Graph nodes/edges | `WHERE user_id = :uid` on source node |
| Skills | `WHERE user_id = :uid` |
| Approvals | `WHERE user_id = :uid` |
| Reflections | `WHERE user_id = :uid` |
| Retrieval sessions | `WHERE user_id = :uid` |
| Simulation plans/runs | `WHERE user_id = :uid` |
| Telemetry | `WHERE user_id = :uid` |

Cross-user leakage is a **release gate hard-fail**:
- `cross_user_leakage_rate > 0` → blocks release
- `keyword_cross_user_leakage_rate > 0` → blocks release
- `fts_cross_user_leakage_rate > 0` → blocks release
- `vector_cross_user_leakage_rate > 0` → blocks release

## User Roles

| Role | Permissions |
|------|-------------|
| `owner` | Full access; first account created; cannot be deleted via API |
| `admin` | Manage users; access all owned data |
| `user` | Access own data only |

## Auth Mode Security Comparison

| Requirement | `dev` | `single_user` | `multi_user` |
|-------------|-------|---------------|--------------|
| API key required | No | Yes | Yes |
| OAuth available | Yes | Yes | Yes |
| local-dev-key accepted | Yes | Yes | **No** |
| Default secret_key rejected | No | No | **Yes** |
| Open registration default | Yes | **No** | **No** |
| HTTPS required | No | No | **Yes** (by config) |

## Token Security

- Access tokens: short-lived (default 1h), stored hashed
- Refresh tokens: rotated on use (single-use), stored hashed
- API keys: stored hashed (SHA-256), shown once at creation
- PKCE: S256 only (plain method rejected)
- Authorization codes: 5-minute expiry, single-use

## Production Hardening Checklist

- [ ] `MIMIR_AUTH_MODE=multi_user`
- [ ] `MIMIR_SECRET_KEY` set to a strong random value (not `change-me`)
- [ ] `MIMIR_PUBLIC_URL` set to the actual public URL
- [ ] `MIMIR_ALLOW_REGISTRATION=false` (default)
- [ ] No wildcard CORS origins
- [ ] HTTPS enabled for `MIMIR_PUBLIC_URL`
- [ ] `MIMIR_API_KEY` and `MIMIR_DEV_API_KEY` are NOT `local-dev-key`
- [ ] System mutation endpoints disabled (default): `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=false`

## Known Limitations

- OAuth authorize page requires API key entry (no password auth yet)
- No email verification in first-run setup
- No RBAC beyond owner/admin/user
- Audit trail available in logs but no dedicated audit API yet
