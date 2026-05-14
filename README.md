# Mimir

**Self-hostable AI memory server — episodic, semantic, procedural, and graph memory for any MCP-compatible AI agent.**

Mimir gives your AI assistant the kind of memory that persists across sessions, learns from what works, decays what is stale, and refuses to store what is adversarial. It connects to Cursor and other MCP clients in 60 seconds, with OAuth for browser-capable local setups and API-key Bearer auth for SSH, headless, and remote workflows. No cloud dependency, no data leaving your network.

Named for the Norse keeper of knowledge at the root of Yggdrasil.

---

## Why Mimir

Every LLM session starts blank. You re-explain your stack, your constraints, your preferences, the bug you fixed last week. Mimir ends that.

It is not a RAG system bolted onto a file system. It is a purpose-built memory server modeled on how biological memory actually works — three dissociable stores, offline consolidation, trust-weighted retrieval, adversarial quarantine, and a lifecycle that ages memories like a brain does, not a cache. Each design decision traces to peer-reviewed research in cognitive science, information retrieval, and AI safety.

---

## What it does

| Capability | What it means |
|-----------|--------------|
| **Three memory layers** | Episodic (what happened), semantic (what is true), procedural (how to do it) — classified automatically at write time |
| **Knowledge graph** | Entities and relationships extracted from memories; graph-aware retrieval for multi-hop reasoning |
| **Multi-source retrieval** | Six independent providers fused by adaptive weights — vector, keyword, identity, episodic-recent, procedural, high-trust |
| **Task-aware routing** | Query is auto-categorized (identity, troubleshooting, project-continuity, …) and provider weights adjusted accordingly |
| **Trust scoring** | Every memory carries a trust score, confidence, verification status, and source type — retrieval is trust-weighted |
| **Adversarial quarantine** | Seven pattern classes detected and quarantined before storage — prompt injection, credential exposure, approval spoofing, and more |
| **Memory lifecycle** | Four-stage state machine (active → aging → stale → archived) driven by recency, retrieval frequency, and trust — memories decay like Ebbinghaus, recover like spaced repetition |
| **Offline consolidation** | Nightly worker deduplicates, compresses episodic chains, extracts procedural lessons, and adjusts trust from retrieval feedback |
| **Reflection + contradiction detection** | Async worker detects contradictions between active memories and proposes resolutions |
| **Simulation + planning** | Multi-path outcome simulation with risk scoring, rollback estimation, and approval gating |
| **Skills system** | Reusable agent procedures that can be proposed, tested, approved, and promoted |
| **Approval workflow** | High-risk actions (risky simulations, irreversible procedures, auto-improvements) require human approval before execution |
| **OAuth 2.1 / PKCE** | Optional browser-based auth for normal local Cursor setups |
| **API-key Bearer auth** | First-class MCP auth for Cursor over SSH, headless clients, remote development, and RPi5 workflows |
| **Multi-user isolation** | Every memory, retrieval, and graph query is scoped by `user_id` across all layers and all workers |
| **React PWA** | Dashboard, memory browser, approval queue, simulation planner, telemetry — works offline |
| **REST + MCP + Python SDK** | Three integration surfaces: HTTP REST, MCP Streamable HTTP (Cursor), Python SDK |
| **SQLite or Postgres** | Single-file local dev or production Postgres with async connection pooling |

---

## Quick Start (Docker, 60 seconds)

```bash
git clone https://github.com/SketchOTP/mimir
cd mimir

export SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

MIMIR_SECRET_KEY=$SECRET \
MIMIR_PUBLIC_URL=http://127.0.0.1:8787 \
docker compose --profile local up -d

# Create your account — API key shown once, save it
docker compose exec api python -m mimir.auth.create_owner \
  --email you@example.com \
  --display-name "Your Name"
```

Add to Cursor — **Settings → MCP → Add Server**:

```json
{
  "mcpServers": {
    "mimir": {
      "url": "http://127.0.0.1:8787/mcp"
    }
  }
}
```

Cursor opens a browser, you enter your API key, you authorize. That is it — Mimir is now Cursor's persistent memory.

For SSH, remote, or headless Cursor setups, use Bearer API-key auth instead of OAuth:

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

See [docs/CURSOR_MCP_SETUP.md](docs/CURSOR_MCP_SETUP.md) for the local OAuth flow, API-key MCP setup, and remote-hosting notes.

---

## Quick Start (No Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# Set MIMIR_SECRET_KEY, MIMIR_AUTH_MODE=single_user, MIMIR_PUBLIC_URL

alembic upgrade head
python -m mimir.auth.create_owner --email you@example.com --display-name "You"
make dev    # API on :8787
make web    # React UI on :5173 (optional)
```

---

## MCP Tools

Cursor (and any MCP client) calls these tools directly:

| Tool | What it does |
|------|-------------|
| `memory.remember` | Store an event or fact; layer is auto-classified (episodic/semantic/procedural) |
| `memory.recall` | Retrieve relevant memories for a query — token-budgeted context string |
| `memory.search` | Semantic search across all layers with optional layer filter |
| `memory.record_outcome` | Record a task outcome; feeds the trust update and reflection pipeline |
| `skill.list` | List approved reusable procedures for the current project |
| `approval.request` | Gate a high-risk action behind human approval |
| `approval.status` | Check the approval queue |
| `reflection.log` | Log an observation or lesson for offline pattern analysis |
| `improvement.propose` | Propose a system-level behavior change (requires approval before activation) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Cursor / any MCP client                                    │
│  POST /mcp  (MCP Streamable HTTP, Bearer auth)              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI  (api/)                                            │
│  ├── OAuth 2.1 / PKCE server  (api/routes/oauth.py)        │
│  ├── MCP Streamable HTTP      (api/routes/mcp_http.py)     │
│  ├── REST API                 (api/routes/*)               │
│  └── Auth deps + multi-user isolation  (api/deps.py)       │
└──────┬───────────────────────────────────────┬─────────────┘
       │                                       │
       ▼                                       ▼
┌────────────────────────┐     ┌───────────────────────────────┐
│  Memory write path     │     │  Retrieval path               │
│  memory_extractor      │     │  retrieval_engine             │
│  ├── classify()        │     │  └── orchestrate()            │
│  │   (episodic /       │     │      ├── task_categorizer     │
│  │    semantic /       │     │      ├── adaptive_weights     │
│  │    procedural)      │     │      ├── 6 providers (async)  │
│  ├── trust_defaults()  │     │      │   ├── vector           │
│  ├── quarantine_       │     │      │   ├── keyword          │
│  │   detector (7       │     │      │   ├── identity         │
│  │   pattern classes)  │     │      │   ├── episodic_recent  │
│  └── store to layer    │     │      │   ├── procedural       │
│                        │     │      │   └── high_trust       │
│  memory/               │     │      └── confidence scorer    │
│  ├── episodic_store    │     │                               │
│  ├── semantic_store    │     │  context/                     │
│  └── procedural_store  │     │  └── context_builder          │
└────────┬───────────────┘     │      (token budgeter)         │
         │                     └───────────────┬───────────────┘
         │                                     │
         └──────────────────┬──────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  storage/  (SQLAlchemy async, SQLite or Postgres)           │
│  ├── Memory (all layers, lifecycle state, trust fields)     │
│  ├── EpisodicChain, MemoryLink, MemoryEvent                 │
│  ├── GraphNode, GraphEdge                                   │
│  ├── SimulationPlan, SimulationRun                          │
│  ├── Skill, SkillRun                                        │
│  ├── ImprovementProposal, ApprovalRequest                   │
│  ├── OAuthClient, OAuthToken (PKCE, hashed)                 │
│  └── vector_store (ChromaDB, project-namespaced)            │
└──────────────────────────┬──────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  worker/  (APScheduler background jobs)                     │
│  ├── consolidator   — nightly: dedup, chain compression,    │
│  │                    trust updates from retrieval feedback │
│  ├── reflector      — every 30 min: contradiction detect,   │
│  │                    pattern analysis, improvement proposals│
│  ├── lifecycle      — nightly: aging, stale → archived,     │
│  │                    verification decay, temporal supersession│
│  ├── procedural_    — promotes validated episodic patterns  │
│  │   promoter         to procedural memory                  │
│  └── feedback_      — infers implicit feedback from task    │
│      inference        outcomes to retrain retrieval weights │
└─────────────────────────────────────────────────────────────┘
```

---

## Research Foundations

Every layer of Mimir maps to established work in cognitive science, information retrieval, and AI safety. These are not marketing terms — they are the actual design rationale.

### Three dissociable memory stores

Mimir's episodic, semantic, and procedural stores reflect Endel Tulving's taxonomy of human long-term memory (1972, 1983). Episodic memory encodes *what happened and when* — timestamped, session-scoped, decaying. Semantic memory encodes *what is true* — facts, preferences, rules, identity. Procedural memory encodes *how to do things* — workflows, runbooks, learned patterns. The three stores are separated because retrieval from each is qualitatively different: episodic retrieval is recency-sensitive, semantic retrieval is associative, procedural retrieval is condition-triggered.

### Offline consolidation ("dreaming")

The nightly consolidation worker compresses episodic sequences into chains, extracts procedural lessons, deduplicates overlapping memories, and adjusts trust scores from retrieval feedback. This mirrors the Complementary Learning Systems (CLS) theory of memory consolidation (McClelland, McNaughton, O'Reilly, 1995) — offline replay integrates episodic traces into structured long-term knowledge without catastrophically overwriting existing semantic memory. The worker is conservative: it never silently deletes high-trust memories; it proposes merges and flags contradictions for human review.

### Trust-weighted retrieval

Every memory carries a `trust_score`, `confidence`, `verification_status` (`trusted_user_explicit`, `trusted_system_observed`, `inferred_low_confidence`, `external_unverified`, `conflicting`, `quarantined`), and `source_type`. Retrieval scoring combines semantic similarity with trust weight — a high-similarity but low-trust memory is ranked below a moderate-similarity high-trust one. This prevents learned misinformation from surfacing as confident context.

### Adaptive multi-provider retrieval

Six independent providers run concurrently — vector (ChromaDB semantic similarity), keyword (BM25-style), identity (high-weight personal/preference facts), episodic-recent (recency-boosted), procedural (condition-triggered), and high-trust (trust floor filter). Provider budgets are allocated by `adaptive_weights.py`, which combines static category boosts (identity queries → identity provider weight doubles) with historically learned per-provider effectiveness, updated from task outcome feedback. The design is inspired by mixture-of-experts routing and multi-armed bandit adaptation — no single retrieval strategy dominates; the system learns which combination works for each query type.

### Memory lifecycle and forgetting

Memories follow a four-state machine: `active → aging → stale → archived`, with hard deletion after a configurable retention window. Transition timing is modulated by recency, retrieval frequency, trust score, and verification age. Each retrieval extends a memory's effective life by a configurable number of days. Unverified high-trust memories lose confidence over time via verification decay. This implements a computational analog of Ebbinghaus's forgetting curve (1885) and spaced repetition — frequently recalled memories stay active; unused ones age out without manual curation.

### Adversarial quarantine

Seven pattern classes are detected before any memory is stored:

1. **Prompt injection** — attempts to override agent system instructions
2. **Security policy overwrite** — disabling safety, filtering, or governance rules
3. **Approval spoofing** — falsely claiming prior human authorization
4. **Infrastructure manipulation** — network or system configuration control instructions
5. **Dangerous procedure** — unapproved shell or command execution workflows
6. **Credential exposure** — secrets, API keys, or passwords embedded in memory content
7. **High-trust contradiction** — content that directly contradicts a high-confidence existing memory

Quarantined memories receive a trust score cap of 0.2, verification_status `quarantined`, and are permanently blocked from all retrieval and context paths. Quarantine state is sticky — it cannot be removed by a content update. This draws on adversarial robustness research for LLM systems (Riley et al., 2022; Perez & Ribeiro, 2022; Greshake et al., 2023) and applies it at the memory layer rather than the prompt layer.

### Simulation and outcome estimation

The simulation engine generates multi-path execution plans (branching depth ≤ 5, branches per step ≤ 3) with per-path risk scores, success probability estimates, token cost projections, and rollback risk assessments. Historical procedural memory provides a ground-truth calibration base for success probability. High-risk plans (`risk_score > 0.7`) or those containing irreversible operations require explicit approval before execution. This is a lightweight implementation of the cognitive planning loop described in Bratman's BDI (Belief-Desire-Intention) agent theory and Kahneman's System 2 deliberative reasoning.

### OAuth 2.1 / PKCE

Mimir ships a full RFC-compliant OAuth 2.1 authorization server (`api/routes/oauth.py`) — dynamic client registration (RFC 7591), PKCE S256 challenge (RFC 7636), authorization code grant, refresh token rotation, token revocation (RFC 7009), and protected resource metadata discovery (RFC 9728). This is intended for normal browser-capable local/client setups. Tokens are stored as SHA-256 hashes; revoked tokens are permanently blocked and cannot fall through to dev-mode auth paths.

### API-key Bearer auth

MCP setup does not require OAuth. `Authorization: Bearer <API_KEY>` remains a supported first-class path for Cursor over SSH, headless clients, remote development, and RPi5 workflows. If a client cannot complete a browser redirect flow, use an API key directly.

---

## Memory Layer Reference

### Episodic

What happened. Timestamped, session-scoped, retrieved by recency and semantic proximity.

- **Auto-classified** when content contains temporal language ("today", "this session", "currently")
- **Chains** — the consolidator compresses episodic sequences into named chains (e.g., "debugging session March 2026")
- **Lifecycle** — ages fastest; retrieval frequency slows decay
- **Use** — task logs, bug observations, session outcomes, "what did I do last Tuesday"

### Semantic

What is true. Associative retrieval, high persistence.

- **Auto-classified** when content contains fact signals ("always", "never", "prefer", "rule", "policy")
- **Identity memories** — personal facts, name, role, preferences — receive a 2× retrieval weight boost via the identity provider
- **Trust-weighted** — user-explicit facts (`trusted_user_explicit`, trust ≥ 0.90) override inferred facts in context ranking
- **Contradiction detection** — the reflector worker flags pairs of contradicting active semantic memories for review
- **Use** — user preferences, project constraints, tech stack facts, governance rules

### Procedural

How to do it. Condition-triggered retrieval, high importance weight.

- **Auto-classified** when content contains procedure signals ("step", "workflow", "how to", "runbook")
- **Promoted from episodic** — the `procedural_promoter` worker elevates validated episodic patterns to procedural status after ≥ 3 successful completions
- **Skills integration** — approved skills (agent procedures with test cases) are linked to procedural memories
- **Use** — build commands, deployment runbooks, debugging procedures, agent workflows

### Graph

Who relates to what. Entity and relationship extraction from existing memories.

- **Auto-built** — the `graph_builder` scans episodic chains, improvement proposals, simulation runs, and rollback events to extract nodes and edges
- **Graph-aware retrieval** — the retrieval orchestrator uses graph traversal to surface related memories not caught by vector similarity
- **Use** — "what other memories are related to this entity?", "how did this decision connect to this outcome?"

---

## Retrieval Pipeline

A query flows through this sequence:

```
query → task_categorizer        detect: identity / troubleshooting / project_continuity / …
      → adaptive_weights        compute per-provider token budgets
      → 6 providers (async)     each returns ProviderHit list with scores
      → merge + deduplicate     union hits, deduplicate by memory_id, keep best score
      → trust filter            remove quarantined / archived / deleted
      → confidence scorer       trust-weighted agreement score across providers
      → token budgeter          fill context window to budget, highest-confidence first
      → context_builder         return context string + memory_ids + debug trace
```

Every retrieval is auditable — the debug trace includes which providers contributed, which memories were excluded and why, provider agreement scores, and the final token cost.

---

## Background Workers

| Worker | Schedule | Responsibility |
|--------|----------|----------------|
| `consolidator` | Nightly | Dedup, episodic chain compression, trust score update from feedback |
| `reflector` | Every 30 min | Contradiction detection, pattern analysis, improvement proposals |
| `lifecycle` | Nightly + weekly | Memory aging, verification decay, temporal supersession, hard deletion |
| `procedural_promoter` | Nightly | Promote validated episodic patterns to procedural memory |
| `feedback_inference` | After each task outcome | Infer implicit provider feedback, update adaptive weights |
| `graph_builder` | Nightly | Extract entities and relationships from memory corpus |

Workers hold a distributed job lock (`job_lock.py`) to prevent concurrent double-runs across horizontally scaled instances.

---

## Safety

Mimir is built to be safe by default:

- **Quarantine is permanent** — content update cannot reactivate a quarantined memory
- **Mutation endpoints gated** — `POST /system/consolidate`, `POST /system/reflect`, and `POST /system/lifecycle` require `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true`; off by default in production
- **High-trust memories protected** — the consolidator never silently deletes memories with `trust_score ≥ 0.7`; it proposes a merge and logs a lifecycle event
- **Cross-user isolation** — OAuth token resolution happens before any dev-mode shortcut; every query is user-scoped at the DB layer
- **Revoked tokens blocked globally** — revoked OAuth tokens are permanently rejected even if the system is in dev auth mode
- **Seven quarantine classes** — adversarial content detection at write time, not query time

Full audit: [docs/SECURITY.md](docs/SECURITY.md) · [docs/MULTI_USER_SECURITY.md](docs/MULTI_USER_SECURITY.md) · [docs/ACCESS_CONTROL_MATRIX.md](docs/ACCESS_CONTROL_MATRIX.md)

---

## Project Bootstrap

For existing repos that are already far into development, Mimir ships a one-time bootstrap script that reads a curated set of governance/status/docs files and ingests a structured project capsule — without touching source code, secrets, or databases.

```bash
# Preview (no writes)
./scripts/bootstrap_mimir_project.sh --dry-run --repo /path/to/your/repo --project myproject

# Execute
./scripts/bootstrap_mimir_project.sh \
  --repo /path/to/your/repo \
  --project myproject \
  --url http://192.168.1.246:8787 \
  --key $MIMIR_API_KEY

# Force re-bootstrap after major project changes
./scripts/bootstrap_mimir_project.sh ... --force
```

What gets ingested (7 typed memories — no source files, no secrets):

| Memory type | Sources | Layer |
|------------|---------|-------|
| `project_profile` | README, project_goal, pyproject.toml | semantic |
| `architecture_summary` | repo_map, project_knowledge, docs/ | semantic |
| `active_status` | project_status (8 KB cap), history tail (100 lines) | episodic |
| `testing_protocol` | Test sections from AGENTS.md, CLAUDE.md | procedural |
| `safety_constraint` | Full AGENTS.md, CLAUDE.md, .cursor/rules/ | semantic |
| `governance_rules` | Priority order, Mimir usage rules | semantic |
| `procedural_lesson` | project_knowledge, history tail | procedural |

Idempotent — aborts if bootstrap memories exist; use `--force` to overwrite.

---

## Self-Hosting

| Topic | Doc |
|-------|-----|
| Local (SQLite, single user) | [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md) |
| Production (Postgres, teams) | [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md) |
| Multi-user security model | [docs/MULTI_USER_SECURITY.md](docs/MULTI_USER_SECURITY.md) |
| OAuth flow and endpoints | [docs/OAUTH_SETUP.md](docs/OAUTH_SETUP.md) |
| Cursor MCP setup | [docs/CURSOR_MCP_SETUP.md](docs/CURSOR_MCP_SETUP.md) |
| Backup and restore | [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) |
| Operations and monitoring | [docs/OPERATIONS.md](docs/OPERATIONS.md) |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MIMIR_AUTH_MODE` | `""` | `dev` · `single_user` · `multi_user` |
| `MIMIR_SECRET_KEY` | `change-me` | JWT / token signing secret (required in production) |
| `MIMIR_PUBLIC_URL` | `""` | Public base URL — required for OAuth redirect |
| `MIMIR_DATABASE_URL` | `""` | Postgres DSN — empty uses SQLite |
| `MIMIR_DATA_DIR` | `./data` | SQLite file + backup storage path |
| `MIMIR_ALLOW_REGISTRATION` | `false` | Open user self-registration |
| `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS` | `false` | Unlock consolidate/reflect/lifecycle POST endpoints |

Full reference: [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md)

---

## Development

```bash
make dev      # FastAPI hot-reload on :8787
make web      # Vite dev server on :5173
make worker   # APScheduler background worker
make test     # pytest tests/ -v  (647 tests, 3 skipped)
make evals    # 8-suite eval harness
make gate     # release gate — exits 1 on any critical failure
make security # security scan (pip-audit, npm audit, credential pattern scan)
```

Test isolation: all tests share a session-scoped SQLite DB at `/tmp/mimir_test/mimir.db`, cleared at import. Tests that write semantic memories pass `project="unique_test_name"` to avoid cross-test pollution.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API | Python 3.12, FastAPI, Uvicorn |
| ORM | SQLAlchemy 2 async |
| Database | SQLite (local) / Postgres (production) |
| Vector store | ChromaDB (project-namespaced collections) |
| Background jobs | APScheduler |
| Migrations | Alembic |
| Frontend | React 18, TypeScript, Vite, PWA |
| Auth | OAuth 2.1 / PKCE (implemented from scratch, no third-party OAuth library) |
| MCP transport | Streamable HTTP (2025-03-26 spec), JSON-RPC 2.0 |
| Container | Docker + Docker Compose |

---

## License

Apache-2.0 — see [LICENSE](LICENSE).

---

## Security

To report a vulnerability: [GitHub Security Advisory](https://github.com/SketchOTP/mimir/security/advisories/new)

Full security model: [docs/SECURITY.md](docs/SECURITY.md)
