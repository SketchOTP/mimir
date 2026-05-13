# Mimir — Access Control Matrix

**Version:** 0.1.0-rc1 | **Updated:** 2026-05-13

Auth modes:
- **dev** — `MIMIR_AUTH_MODE=dev` or `MIMIR_ENV=development`: all requests accepted, mapped to synthetic `dev` user (no DB lookup). Suitable for local development only.
- **prod** — `MIMIR_AUTH_MODE=prod`: `X-API-Key` header required; key must match a row in `api_keys` table (SHA-256 hash comparison). Legacy single-key mode also supported via `MIMIR_API_KEY`.

Ownership enforcement: non-dev users can only read/write resources where `user_id == current_user.id`. Dev user bypasses ownership checks.

Risk levels: **LOW** (read-only, scoped), **MEDIUM** (write, scoped), **HIGH** (write, cross-user risk if ownership not enforced), **CRITICAL** (system-wide mutation, no ownership scope).

---

## Public Endpoints (No Auth)

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/health` | GET | No | N/A | No | LOW | `test_p16_production` |
| `/api/auth/register` | POST | No | N/A | No — open registration | MEDIUM | `test_auth` |

**Note:** `/api/auth/register` creates a new user + returns a raw API key (shown once). In production, consider restricting registration via network policy or an `MIMIR_ALLOW_REGISTRATION` flag if multi-tenancy is not desired.

---

## Auth Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/auth/keys` | POST | Yes | Self (creates for current user) | No | MEDIUM | `test_auth` |
| `/api/auth/keys` | GET | Yes | Self (own keys only) | No | LOW | `test_auth` |
| `/api/auth/keys/{key_id}` | DELETE | Yes | Self (own keys only) | No | MEDIUM | `test_auth` |
| `/api/auth/me` | GET | Yes | Self | No | LOW | `test_auth` |

---

## Memory Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/memory` | POST | Yes | Writes as current user | No | MEDIUM | `test_memory`, `test_quarantine` |
| `/api/memory` | GET | Yes | Non-dev filtered to own user_id | No | LOW | `test_memory` |
| `/api/memory/{memory_id}` | GET | Yes | Own user_id or user_id=null | No | LOW | `test_memory` |
| `/api/memory/{memory_id}` | PATCH | Yes | Own user_id or user_id=null | No | MEDIUM | `test_memory` |
| `/api/memory/{memory_id}` | DELETE | Yes | Own user_id or user_id=null | No | MEDIUM | `test_memory` |

**Security note:** Quarantined memories (`memory_state=quarantined`) remain quarantined even after content update — they cannot be silently reactivated via PATCH (fixed P18).

---

## Events / Recall Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/events` | POST | Yes | Writes as current user | No | MEDIUM | `test_events` |
| `/api/events` | GET | Yes | Self-scoped | No | LOW | `test_events` |
| `/api/recall` | POST | Yes | FTS + vector search scoped to current user | No | MEDIUM | `test_p15_fts_isolation`, `test_recall` |
| `/api/recall/feedback` | POST | Yes | Own session only | No | MEDIUM | `test_procedural_learning` |
| `/api/recall/session/{id}/outcome` | POST | Yes | Own session only | No | MEDIUM | `test_p9_telemetry` |

---

## Skills Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/skills` | POST | Yes | Writes as current user | No | MEDIUM | `test_skills` |
| `/api/skills` | GET | Yes | Own user_id | No | LOW | `test_skills` |
| `/api/skills/{skill_id}` | GET | Yes | Own user_id | No | LOW | `test_skills` |
| `/api/skills/{skill_id}/run` | POST | Yes | Own skill | No | MEDIUM | `test_skills` |
| `/api/skills/{skill_id}/test` | POST | Yes | Own skill | No | MEDIUM | `test_skills` |
| `/api/skills/{skill_id}/result` | POST | Yes | Own skill | No | MEDIUM | `test_skills` |
| `/api/skills/propose` | POST | Yes | Own user | No | MEDIUM | `test_skills` |

---

## Reflections / Improvements Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/reflections` | POST | Yes | Writes as current user | No | MEDIUM | `test_reflection_restraint` |
| `/api/reflections` | GET | Yes | Own user_id | No | LOW | `test_reflection_restraint` |
| `/api/reflections/generate` | POST | Yes | Own user | No | MEDIUM | `test_reflection_restraint` |
| `/api/improvements` | GET | Yes | Own user_id | No | LOW | `test_reflection_restraint` |
| `/api/improvements/{improvement_id}` | GET | Yes | Own user_id | No | LOW | `test_reflection_restraint` |
| `/api/improvements/propose` | POST | Yes | Own user | No | MEDIUM | `test_reflection_restraint` |

---

## Approvals Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/approvals` | GET | Yes | Own user_id | No | LOW | `test_approvals`, `test_audit_trail` |
| `/api/approvals/{approval_id}` | GET | Yes | Own user_id | No | LOW | `test_approvals` |
| `/api/approvals/{approval_id}/approve` | POST | Yes | **Own user_id enforced** | No | HIGH | `test_approvals`, `test_audit_trail` |
| `/api/approvals/{approval_id}/reject` | POST | Yes | **Own user_id enforced** | No | HIGH | `test_approvals`, `test_audit_trail` |

**Critical:** Approval decisions enforce that `approval.user_id == current_user.id`. An attacker who can forge their user ID or obtain another user's API key cannot approve other users' improvement proposals.

---

## Graph Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/graph/nodes/{entity_id}` | GET | Yes | None (graph is shared) | No | LOW | `test_p11_graph` |
| `/api/graph/traverse/{entity_id}` | GET | Yes | None | No | LOW | `test_p11_graph` |
| `/api/graph/causal/{entity_id}` | GET | Yes | None | No | LOW | `test_p11_graph` |
| `/api/graph/contradictions/{entity_id}` | GET | Yes | None | No | LOW | `test_p11_graph` |
| `/api/graph/centrality` | GET | Yes | None | No | LOW | `test_p11_graph` |
| `/api/graph/telemetry` | GET | Yes | None | No | LOW | `test_p11_graph` |
| `/api/graph/build` | POST | Yes | None | No | MEDIUM | `test_p11_graph` |

**Note:** Graph is a shared layer — graph nodes and edges are not per-user. This is by design; graph nodes reference memory entity IDs, and the underlying memories are user-scoped.

---

## Simulation Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/simulation/plans` | POST | Yes | None (plans are project-scoped) | No | MEDIUM | `test_p12_simulation` |
| `/api/simulation/plans` | GET | Yes | None | No | LOW | `test_p12_simulation` |
| `/api/simulation/plans/{plan_id}` | GET | Yes | None | No | LOW | `test_p12_simulation` |
| `/api/simulation/plans/{plan_id}/approve` | POST | Yes | None | No | HIGH | `test_p12_simulation` |
| `/api/simulation/plans/{plan_id}/reject` | POST | Yes | None | No | MEDIUM | `test_p12_simulation` |
| `/api/simulation/plans/{plan_id}/simulate` | POST | Yes | None | No | MEDIUM | `test_p12_simulation` |
| `/api/simulation/plans/{plan_id}/simulations` | GET | Yes | None | No | LOW | `test_p12_simulation` |
| `/api/simulation/plans/{plan_id}/counterfactual` | POST | Yes | None | No | MEDIUM | `test_p12_simulation` |
| `/api/simulation/plans/{plan_id}/counterfactuals` | GET | Yes | None | No | LOW | `test_p12_simulation` |
| `/api/simulation/plans/{plan_id}/risk` | POST | Yes | None | No | LOW | `test_p12_simulation` |
| `/api/simulation/runs/{run_id}/outcome` | POST | Yes | None | No | MEDIUM | `test_p12_simulation` |
| `/api/simulation/calibration/compute` | POST | Yes | None | No | MEDIUM | `test_p12_simulation` |
| `/api/simulation/calibration/history` | GET | Yes | None | No | LOW | `test_p12_simulation` |

**Note:** High-risk simulation plans (risk ≥ 0.7 or dangerous keywords) are auto-gated for approval before any downstream action is taken. Simulation runs are advisory only — they do not auto-promote procedures.

---

## Telemetry Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/telemetry/snapshot` | GET | Yes | None (system-wide) | No | LOW | `test_p9_telemetry` |
| `/api/telemetry/snapshot/compute` | POST | Yes | None | No | MEDIUM | `test_p9_telemetry` |
| `/api/telemetry/metrics/{name}/history` | GET | Yes | None | No | LOW | `test_p9_telemetry` |
| `/api/telemetry/retrieval/stats` | GET | Yes | None | No | LOW | `test_p9_telemetry` |
| `/api/telemetry/retrieval/heatmap` | GET | Yes | None | No | LOW | `test_p9_telemetry` |
| `/api/telemetry/procedural/effectiveness` | GET | Yes | None | No | LOW | `test_p9_telemetry` |
| `/api/telemetry/drift/detect` | GET | Yes | None | No | LOW | `test_p9_telemetry` |
| `/api/telemetry/drift/apply-decay` | POST | Yes | None | No | HIGH | `test_p9_telemetry` |
| `/api/providers/stats` | GET | Yes | None | No | LOW | `test_p10_adaptive` |
| `/api/providers/aggregate` | POST | Yes | None | No | MEDIUM | `test_p10_adaptive` |
| `/api/providers/drift` | GET | Yes | None | No | LOW | `test_p10_adaptive` |

---

## System Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/system/status` | GET | Yes | None (system-wide) | No | LOW | `test_p16_production` |
| `/api/system/readiness` | GET | Yes | None | No | LOW | `test_p16_production` |
| `/api/system/jobs` | GET | Yes | None | No | LOW | `test_p16_production` |
| `/api/system/metrics` | GET | Yes | None | No | LOW | `test_p16_production` |
| `/api/metrics` | GET | Yes | None | No | LOW | `test_p16_production` |
| `/api/system/consolidate` | POST | Yes | None | **Requires `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true`** | CRITICAL | `test_p18_security` |
| `/api/system/reflect` | POST | Yes | None | **Requires `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true`** | CRITICAL | `test_p18_security` |
| `/api/system/lifecycle` | POST | Yes | None | **Requires `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true`** | CRITICAL | `test_p18_security` |

**Critical:** System mutation endpoints are **disabled by default** in production. They trigger system-wide worker passes that affect all users' data. Enable only in dev/test or for manual operator intervention via `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true`.

---

## Dashboard / Notifications

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/dashboard` | GET | Yes | Own user_id | No | LOW | `test_ui` |
| `/api/notifications` | GET | Yes | Own user_id | No | LOW | `test_ui` |
| `/api/notifications/{notification_id}` | GET | Yes | Own user_id | No | LOW | `test_ui` |
| `/api/push/vapid-key` | GET | Yes | None | No | LOW | `test_notifications` |
| `/api/push/subscribe` | POST | Yes | Own user | No | LOW | `test_notifications` |
| `/api/push/interactions` | POST | Yes | Own user | No | LOW | `test_notifications` |

---

## Slack Endpoints

| Route | Method | Auth Required | Ownership | Admin/Dev Only | Risk | Test Coverage |
|-------|--------|--------------|-----------|----------------|------|---------------|
| `/api/slack/interactions` | POST | HMAC-SHA256 signature (Slack signing secret) | N/A | No | MEDIUM | `test_slack_security` |

**Note:** Slack endpoint uses Slack's own signing secret verification (HMAC-SHA256 + 5-minute replay window) rather than API key auth. The signing secret must be set via `SLACK_SIGNING_SECRET`.

---

## Security Properties Summary

| Property | Status |
|----------|--------|
| All non-health endpoints require auth in prod | ✅ |
| API keys stored as SHA-256 hashes only | ✅ |
| Raw API key returned only at creation time, never again | ✅ |
| Approval decisions require ownership | ✅ |
| Quarantined memories excluded from recall/context/simulation | ✅ |
| Quarantined memories cannot be silently reactivated via update | ✅ (fixed P18) |
| Cross-user memory recall blocked (FTS + vector + SQL) | ✅ |
| System mutation endpoints gated by config flag | ✅ (added P18) |
| Tailscale command patterns detected and quarantined | ✅ |
| Slack endpoint HMAC signature verified with replay protection | ✅ |
| Production CORS wildcard rejected | ✅ |
| Insecure secret_key/api_key defaults rejected in prod | ✅ |
| No raw secrets returned after creation | ✅ |
