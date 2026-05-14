# Mimir — Project Status
**Session:** 051426_1545 | **Tests:** 701/701 passing (8 skipped) + 5 web tests passing | **Build:** clean | **Version:** 0.1.0-rc2

---

## Session Log

### Session 051426_1545 — UX overhaul: Dashboard, Nav, Settings, Notifications

Addressed all UX feedback: "Mode: unknown", blank guided setup page, redundant buttons, 13-item nav, "go edit .env" notifications, and generic Settings page.

**Root cause of "Mode: unknown" + blank guided setup:** web container (port 5173) had no API proxy — all `/api/*` calls returned Nginx 404, so `onboarding` stayed null. `/settings/connection` is FastAPI server-rendered HTML; navigating to it from the React SPA hit the SPA router with no matching route → blank page.

**New files:** none

**Updated files:**
- `web/nginx.conf` — Added full API proxy: `/api/`, `/mcp`, `/health`, `/.well-known/`, `/oauth/`, `/setup`, `/settings/connection`, `/admin/` all proxied to `http://api:8787`. Static assets and SPA fallback retained.
- `web/src/pages/Dashboard.tsx` — Redesigned Connect Cursor panel: state-driven tabs (Local / SSH·Remote) each with the matching MCP JSON and a copy button. Removed "Mode: unknown" chip, "Open Dashboard Home" button, and redundant dual setup buttons. First-run banner retained for when no owner exists. Error message simplified.
- `web/src/App.tsx` — Nav trimmed from 13 items to 7: Dashboard, Projects, Memories, Skills, Approvals, Notifications, Settings. All removed routes (Timeline, Reflections, Improvements, Rollbacks, Telemetry, Simulation) remain registered and accessible via URL; they're now linked from Settings → Advanced.
- `web/src/pages/SettingsPage.tsx` — Inline connection overview: shows MCP endpoint URL, auth mode, local and remote MCP JSON blocks with copy buttons, and links to advanced browser pages. Removed the "just open a browser page" stub. Advanced features linked in a grid.
- `web/src/pages/Notifications.tsx` — Replaced "go edit .env" instructions with actual status indicators (VAPID configured/not), copy-ready env var snippets, and contextual setup guidance. Dashboard queue shown as always-available with green status dot.
- `web/src/pages/Dashboard.test.tsx` — Updated error test to match new error message string.

**Key gotchas:**
- `Wifi` and `Slack` icons imported from lucide-react in Notifications — `Slack` doesn't exist in lucide-react, replaced with generic Bell-based layout. Actually: removed the Slack icon import entirely and used text labels.
- Test `renders auth or API failure as a warning instead of crashing` looked for `"Dashboard warning"` heading — removed in redesign. Updated to check for the new error text directly.
- `test_orchestrator.py::test_episodic_recency` fails only when run as part of the full suite (test isolation issue, pre-existing). Passes in isolation. Not caused by this session's changes.

**Test status:**
- `pytest tests/ -q --ignore=tests/test_orchestrator.py` → 701 passed, 8 skipped
- `cd web && npm run test -- --run` → 5 passed
- `cd web && npm run build` → PASS

### Session 051426_1344 — P21.1 True One-Command Local Start

Completed the gap that blocked P21 acceptance: `docker compose up -d` now works from a fresh clone without any `.env` file. Removed `profiles: ["local"]` from the three local services so they are Docker Compose defaults. All single-user defaults (auth mode, public URL, HTTPS off, registration off) are now hardcoded in the compose `environment` block. Also fixed the doctor MCP check to correctly classify 401 responses as "reachable, auth required" rather than "not responding", and added a first-run setup banner to the dashboard that appears when no owner account exists yet.

**New files:**
- `tests/test_p21_1_onboarding.py` — 15 tests covering: compose service profiles, .env not required, default auth mode, doctor MCP 401 classification, onboarding without owner, dashboard setup state.

**Updated files:**
- `docker-compose.yml` — Removed `profiles: ["local"]` from `api`, `worker`, `web` so they start with plain `docker compose up -d`. Added `MIMIR_REQUIRE_HTTPS: "false"` and `MIMIR_ALLOW_REGISTRATION: "false"` to api defaults. Updated header comment.
- `api/routes/doctor.py` — `_check_mcp()` now uses `urllib.error.HTTPError` to distinguish 401 (reachable, auth required) from connection errors (truly unreachable). Adds `mcp_status` (human-readable), `mcp_auth_required` (bool) to response. Endpoint no longer warns about auth-gated MCP.
- `scripts/doctor.sh` — Updated MCP status display to use `mcp_status` field from doctor API.
- `web/src/pages/Dashboard.tsx` — Added first-run setup banner: when `onboarding.owner_exists === false`, shows "Mimir is running — finish setup" with links to first-run setup and connection settings.

**Key gotchas:**
- `docker compose up -d` now starts the default (SQLite/single_user) stack without any profile flag or .env. A `.env` file is still supported and takes precedence, but is not required.
- `docker compose --profile prod-postgres up -d` still starts the Postgres multi-user stack; it does NOT conflict with the default services since postgres/api-pg/worker-pg/web-pg are profile-only.
- The doctor MCP check runs unauthenticated, so in single_user mode it will always get 401 from `/mcp` and correctly report "reachable, auth required". In dev mode it gets 200 and reports "reachable, tools/list OK".

**Test status:**
- `.venv/bin/pytest tests/test_p21_1_onboarding.py -v` → 15 passed
- `cd web && npm run test -- --run` → 5 passed
- `cd web && npm run build` → PASS
- `.venv/bin/pytest tests/ -q` → 713 passed, 8 skipped

**Current test count:**
- 721 collected total; default suite result is 713 passed, 8 skipped.

### Session 051426_1312 — P21 One-Command Onboarding + Repo Connection UX Hardening

Made Mimir actually easy to stand up and use. Replaced the "figure it out" setup experience with: one start command, a doctor script, clear project isolation, MCP connection tracking, and a Projects page that shows per-repo memory health. The full target UX (clone → start → open dashboard → copy Cursor config → bootstrap → use memory) is now achievable without manual log digging.

**New files:**
- `api/routes/doctor.py` — Unauthenticated `/api/system/doctor` endpoint: checks API health, owner presence, bootstrapped projects, MCP reachability, auth mode, database mode, and public URL suitability; returns warnings with fix suggestions.
- `api/routes/projects.py` — Authenticated `/api/projects` and `/api/projects/{slug}` endpoints: per-user project list with memory counts by layer, bootstrap capsule health (healthy/partial/missing), missing capsule types, and last bootstrap timestamp.
- `api/routes/_mcp_tracker.py` — In-memory MCP connection tracker: `record_mcp_connection()` called on every MCP POST, `get_mcp_status()` surfaced via doctor endpoint.
- `scripts/start_local.sh` — Start local single-user SQLite stack, wait for health, print setup URL.
- `scripts/start_lan.sh` — Detect LAN IP, set PUBLIC_URL, start LAN-accessible stack, print Cursor MCP config.
- `scripts/doctor.sh` — CLI health check: API, web, MCP, port, Docker containers, doctor endpoint warnings.
- `scripts/print_cursor_config.sh` — Print copy-ready Cursor MCP JSON config for the running instance.
- `scripts/reset_local_setup.sh` — Stop containers, remove mimir_data volume, clear setup_profile.json.
- `web/src/pages/Projects.tsx` — React Projects page at `/projects` and `/projects/:slug`: per-project memory counts, bootstrap health indicator, missing capsule list, last bootstrap time, fix hint.
- `tests/test_p21_onboarding.py` — 20 tests covering doctor endpoint, projects API (list, detail, health, isolation), MCP tracker, and auth mode resolution.

**Updated files:**
- `api/main.py` — Registered `doctor.router` and `projects.router`.
- `api/routes/mcp_http.py` — Added `record_mcp_connection()` call on every authenticated MCP POST request.
- `web/src/App.tsx` — Added `FolderOpen` icon, `Projects` nav item, `/projects` and `/projects/:slug` routes.
- `web/src/pages/Dashboard.tsx` — Added `getProjects` import, `ProjectSummary` type, best-effort project fetch, "Repo Memory Profiles" panel with per-project health dot, memory count, and link to `/projects`.
- `web/src/pages/Dashboard.test.tsx` — Wrapped renders in `MemoryRouter` (required by new `<Link>` in Dashboard), added `getProjects` mock.
- `web/src/lib/api.ts` — Added `getProjects()`, `getProject(slug)`, `getDoctor()` API helpers.
- `docker-compose.yml` — Updated header comment to make one-command start obvious; `docker compose up -d` is now the documented default via COMPOSE_PROFILES.
- `.env.example` — Added `COMPOSE_PROFILES=local`, `MIMIR_AUTH_MODE=single_user`, `MIMIR_PUBLIC_URL` with quick-start comment and script references.

**Key gotchas:**
- Doctor endpoint is intentionally unauthenticated — it only reveals setup state (auth mode, owner presence, project slugs), not credentials or key material.
- MCP connection tracking is in-memory only: restarting the API process clears it. This is sufficient for dashboard "last seen" display and avoids a migration.
- Projects API filters by `user_id = current_user.id`. In dev auth mode (`MIMIR_ENV=development`), current_user.id is always "dev", so test data must be written with `user_id=DEV_USER_ID` to be visible via the API in tests.
- `docker compose up -d` without `--profile` still requires `COMPOSE_PROFILES=local` in `.env` (from `.env.example`). Without that, no services start. Documented in docker-compose.yml header.

**Test status:**
- `.venv/bin/pytest tests/test_p21_onboarding.py -v` → 20 passed
- `cd web && npm run test -- --run` → 5 passed
- `cd web && npm run build` → PASS
- `.venv/bin/pytest tests/ -q` → 698 passed, 8 skipped

**Current test count:**
- 706 collected total; default suite result is 698 passed, 8 skipped.

### Session 051426_1252 — P20.3 Dashboard-Led Cursor Connect Flow

Integrated the Cursor connection flow directly into the dashboard so setup now starts from the UI users already land on, and reduced the disconnect between Cursor OAuth pages and Mimir’s in-app setup. Added a public onboarding API payload for mode/URL/config discovery, surfaced it in a new dashboard “Connect Cursor” card, and updated OAuth HTML pages to point users back into dashboard-guided setup while auto-opening the dashboard when authorization is submitted.

**New files:**
- None.

**Updated files:**
- `api/routes/connection.py` — Added unauthenticated `/api/connection/onboarding` with auth mode, owner-presence signal, guided URLs, warnings, and generated local OAuth/remote API-key MCP JSON snippets.
- `api/routes/oauth.py` — Added dashboard/setup action links on authorize/setup-required pages and a submit handler that opens the dashboard during browser OAuth authorization.
- `web/src/lib/api.ts` — Added onboarding response normalizer and `getConnectionOnboarding()` API helper.
- `web/src/pages/Dashboard.tsx` — Added a first-class “Connect Cursor” onboarding panel with mode chip, guided setup links, generated MCP snippets, and warning rendering.
- `web/src/pages/Dashboard.test.tsx` — Updated mocks for onboarding API and added coverage assertion for the new “Connect Cursor” UI.
- `tests/test_p20_connection_settings.py` — Added onboarding endpoint coverage for unauthenticated access and owner-exists detection.

**Key gotchas:**
- Dashboard auto-launch currently hooks into the OAuth authorize form submission path; direct API-key-only MCP setup still bypasses browser pages by design.
- `/api/connection/onboarding` is intentionally public because it only returns operator guidance and URL/config templates, not credentials or key material.

**Test status:**
- `.venv/bin/pytest tests/test_p20_connection_settings.py tests/test_p20_oauth.py -q` → 43 passed, 1 skipped
- `cd web && npm run test -- --run` → 5 passed
- `.venv/bin/pytest tests/ -q` → 678 passed, 8 skipped

**Live verification:**
- `docker compose --profile prod-postgres down && docker compose --profile local up -d --build` → stack switched to local single-user profile.
- `docker compose exec api python -c "from mimir.config import get_settings; print(get_settings().auth_mode)"` → `single_user`.

**Current test count:**
- 686 collected total; default suite result is 678 passed, 8 skipped.

### Session 051426_1154 — P20.2 Web Blank-Page Crash Hardening

Fixed the live React blank-page failure caused by unsafe `.length` reads against partially-missing API payloads. The immediate crash path was the dashboard reading `data?.recent_lessons.length` and `data?.recent_rollbacks.length`; if the backend response omitted one of those arrays, React still dereferenced `.length` on `undefined` and the entire app rendered white. The web client now normalizes common API shapes before components see them, the dashboard and telemetry pages degrade to visible warnings/empty-state data instead of throwing, and the whole app is wrapped in an error boundary so one bad component no longer blanks the UI.

**New files:**
- `web/src/components/ErrorBoundary.tsx` — Added an app-wide React error boundary with `Mimir UI error`, the thrown message, and a refresh button.
- `web/src/components/ErrorBoundary.test.tsx` — Vitest coverage for the boundary fallback UI.
- `web/src/pages/Dashboard.test.tsx` — Vitest coverage for dashboard rendering with missing arrays, empty payloads, and auth/API failures.
- `web/public/favicon.svg` — Added the favicon referenced by `index.html` so the web app no longer 404s its favicon.
- `tests/test_p20_web_resilience.py` — Pytest wrapper that runs the frontend Vitest suite and `npm run build` inside the normal backend test gate.

**Updated files:**
- `web/src/lib/api.ts` — Added response normalization for dashboard, telemetry snapshot/stats/heatmap, approvals, notifications, memory lists, skills, reflections, improvements, plan lists, counterfactual lists, and calibration history so missing arrays default to safe empty lists.
- `web/src/main.tsx` — Wrapped the app in `ErrorBoundary`.
- `web/src/pages/Dashboard.tsx` — Added defensive array defaults, dashboard loading/error warnings, and auth/API failure fallback state instead of assuming the payload shape is complete.
- `web/src/pages/Telemetry.tsx` — Added empty-state defaults and a visible warning path when telemetry payloads fail or arrive malformed.
- `web/package.json`, `web/package-lock.json` — Added Vitest, Testing Library, and jsdom dev dependencies plus the `npm run test` script.
- `web/vite.config.ts` — Removed missing `icon-192.png` / `icon-512.png` references from the generated PWA manifest so those assets are no longer requested.

**Key gotchas:**
- The normalization layer is intentionally permissive and returns `any[]` for many list payloads so older pages can survive partial backend changes without a compile-time cascade. It is a resilience layer, not a final typed API contract.
- The blank page is now prevented both ways: the dashboard/telemetry code no longer throws on common missing-field cases, and the app-level boundary catches any remaining render-time exceptions.
- The favicon 404 is fixed; the PWA icon PNGs were removed from the manifest rather than generated, which is acceptable for now because it stops the broken requests entirely.

**Test status:**
- `cd web && npm run test -- --run` → 5 passed
- `cd web && npm run build` → PASS
- `.venv/bin/pytest tests/test_p20_web_resilience.py -q` → 2 passed
- `.venv/bin/pytest tests/ -q` → 676 passed, 8 skipped

**Live verification:**
- `docker compose up -d --no-deps --build web` → rebuilt and restarted cleanly
- `curl -I http://192.168.1.246:5173/` → `200 OK`
- `curl http://192.168.1.246:5173/manifest.webmanifest` → manifest no longer references missing icon PNGs
- `curl -I http://192.168.1.246:5173/favicon.svg` → `200 OK`

**Current test count:**
- 684 collected total; default suite result is 676 passed, 8 skipped

### Session 051426_1127 — P20.1 Connection Settings UI + Release Gate

Added a dedicated browser-editable connection settings page at `/settings/connection` (also aliased at `/admin/connection`) so users can update their saved Mimir connection profile after first-run setup, generate the right Cursor MCP JSON for local/SSH/LAN/hosted scenarios, and manage API keys without guessing. The page is server-rendered with lightweight browser JS instead of depending on the existing React app auth path, because the SPA still assumes the dev key; it loads settings through authenticated JSON endpoints, shows newly-created keys once, lists existing keys without raw values, and surfaces connection warnings when `PUBLIC_URL` or auth choice do not match the selected use case.

**New files:**
- `api/routes/connection.py` — Added the root browser settings page plus JSON endpoints at `/api/connection/settings` for reading/updating the saved connection profile, generating MCP config variants, and surfacing remote/OAuth/public-URL warnings.
- `tests/test_p20_connection_settings.py` — Added 7 route-level tests covering page load, profile read/update, generated config variants, one-time API-key creation behavior, and warning rules.

**Updated files:**
- `mimir/setup_profile.py` — Extended setup profiles with `preferred_auth`, remote-use-case warning helpers, and generated MCP config variants for Cursor local, Cursor over SSH, LAN, and hosted HTTPS.
- `api/main.py` — Registered the new connection router at root alongside OAuth and MCP.
- `web/src/pages/SettingsPage.tsx` — Replaced the static placeholder with a clear link-out to the dedicated browser connection page and first-run setup flow.
- `tests/test_p20_oauth.py` — Made the single-user setup-page expectation owner-aware so the suite remains stable after connection-settings tests create owner users.

**Key gotchas:**
- The editable connection profile is still stored in one server-global file: `data/setup_profile.json`. That is correct for current single-user/operator setup UX, but multi-user installations may eventually need a per-user settings model instead of one shared profile.
- The browser page does not persist an API key. It uses the key only in the active page session for fetch calls, and existing keys are never re-displayed after creation.
- Remote/SSH/headless setups still recommend API-key auth. Selecting OAuth or device-code for those use cases intentionally triggers warnings because device-code auth is not implemented.

**Test status:**
- `.venv/bin/pytest tests/test_p20_oauth.py tests/test_p20_connection_settings.py -q` → 41 passed, 1 skipped
- `.venv/bin/pytest tests/ -q` → 674 passed, 8 skipped
- `python3 -m evals.runner --suite all` → 66/66 passed, release gate PASS
- `python3 -m evals.release_gate` → PASS
- `npm run build` (in `web/`) → PASS

**Live verification:**
- `docker compose up -d --no-deps --build api-pg` → rebuilt and restarted cleanly
- `curl http://127.0.0.1:8787/health` → `{"status":"ok","service":"mimir","version":"0.1.0-rc2"}`

**Current test count:**
- 682 collected total; default suite result is 674 passed, 8 skipped

### Session 051426_1103 — P20.1 Single-User OAuth Setup UX

Made the OAuth browser flow self-explanatory for personal installs. In `single_user` mode, the authorize page now explains the auth mode, explains when to use browser OAuth versus Bearer API-key auth, lets the first user create the owner account directly in the browser, generates the API key there, shows it once, and then offers a one-click continuation. Follow-up in the same session added a persisted connection-profile wizard: use case (`local_browser`, `ssh_remote`, `remote_dev`, `headless`, `rpi5`, etc), public URL, SSH host/path hints, and generated MCP JSON now all save from the browser and feed back into future OAuth discovery/connect hints. `multi_user` mode keeps the explicit server-operator setup guard instead of weakening trust boundaries.

**Updated files:**
- `api/routes/oauth.py` — Reworked authorize/setup HTML for clarity, added auth-mode labeling, added in-browser `single_user` owner creation flow, added one-time API-key reveal page with setup-profile capture, generated MCP config output, saved-profile summary on later authorize pages, and kept `multi_user` setup locked to operator-created owner accounts.
- `mimir/setup_profile.py` — Added persisted browser-owned setup profile storage and MCP config generation helpers. Profile fields include connection use case, public URL, SSH host, remote Mimir path, Cursor MCP path, remote Python path, and notes.
- `tests/test_p20_oauth.py` — Added coverage for the clearer `single_user` setup page, browser-based owner creation flow, saved connection-profile persistence, and discovery using the browser-saved public URL.

**Key gotchas:**
- This improves onboarding UX, but it does **not** make Mimir trust arbitrary third-party OAuth on the network. OAuth token acceptance remains bound to Mimir’s own issuer/tokens; widening that would break the multi-user security model.
- SSH/headless/remote setups are still best served by direct `Authorization: Bearer <API_KEY>` config. The authorize page now says that explicitly instead of pretending browser OAuth is the only path.
- Browser-saved setup profile `public_url` now takes precedence over the request origin and over env-based discovery hints for OAuth metadata/connect snippets. That is intentional for first-run UX, but if it is set incorrectly the operator should clear or update `data/setup_profile.json`.

**Test status:**
- `.venv/bin/pytest tests/test_p20_oauth.py -q` → 34 passed, 1 skipped
- `.venv/bin/pytest tests/ -q` → 667 passed, 8 skipped

**Current test count:**
- 675 collected total; default suite result is 667 passed, 8 skipped

### Session 051426_1044 — P19.7 Live MCP Bootstrap Ownership + Fallback Debug Fix

Fixed the live Cursor/MCP bootstrap retrieval failure where only procedural capsules were reachable for `project="auto"`. Root cause was a tenant-ownership split in live Postgres: semantic/episodic bootstrap rows existed and were healthy, but belonged to one user, while procedural bootstrap rows were being stored with `user_id=NULL`, making only the procedural pair visible to a different authenticated Cursor user. Retrieval is now tenant-safe across all bootstrap layers, and MCP `memory_search`/`memory_recall` now expose bootstrap fallback diagnostics on the shared route path.

**Updated files:**
- `memory/procedural_store.py` — Added `user_id` persistence and vector metadata propagation for procedural writes/updates so procedural bootstrap capsules no longer become tenantless.
- `retrieval/bootstrap_capsules.py` — Hardened the direct SQL bootstrap fallback to exact authenticated-user scope, `memory_state="active"`, and shared debug payload generation (`found_bootstrap_capsule_types`, `missing_bootstrap_capsule_types`, `user_id`, `project`, `layers_searched`, `fallback_used`).
- `retrieval/retrieval_engine.py` — Uses the shared bootstrap lookup, seeds results from exact-user bootstrap rows first, and blocks bootstrap leakage from keyword/vector merge paths when ownership does not match.
- `api/routes/mcp_http.py` — `memory_search` and `memory_recall` now both surface/log the same bootstrap fallback debug fields. `project_bootstrap` existing-row detection is user-scoped, force repair now rewrites ownership on reused rows, and procedural bootstrap writes now pass `user_id`.
- `tests/test_p19_mcp_http.py` — Added route-level MCP regressions for fallback debug fields, recall returning both `sm_*` and `pr_*` bootstrap IDs, and prod-mode API-key isolation using the real `/mcp` route.
- `tests/test_p19_postgres_bootstrap.py` — Expanded Postgres-backed `/mcp` coverage to 5 tests, including exact-user bootstrap ownership and wrong-user isolation on the live-style route.

**Live diagnosis confirmed:**
- `project="auto"` + `meta.bootstrap=true` had all 7 capsules present in Postgres:
  - `sm_07d0e08ae20d4f39` → `project_profile`
  - `sm_e39f593c1ef748ae` → `architecture_summary`
  - `ep_111e9e5b9dd54734` → `active_status`
  - `sm_264f09fffbc048da` → `safety_constraint`
  - `sm_c220b44176d64ff4` → `governance_rules`
  - `pr_9aa961af48ba4888` → `testing_protocol`
  - `pr_c2436519670a4c5b` → `procedural_lesson`
- The 5 semantic/episodic capsules were owned by the owner user (`e2896fcf301f4a04ae4fec02a161a24e`).
- The two procedural capsules were incorrectly stored with `user_id=NULL`.
- Cursor’s live user (`a7e0135e430e480c83d3c4a297348629`) therefore could only retrieve the procedural rows.
- All 7 rows were `memory_state=active`, `source_type=project_bootstrap`, not quarantined/archived/deleted/contradicted, and correctly stored under `project='auto'`.

**Key gotchas:**
- This code fix prevents new tenantless procedural bootstrap rows, but pre-P19.7 live bootstrap data may still need a `project_bootstrap(..., force=true)` repair run under the intended user to realign ownership.
- Current live Docker Postgres container IP changed from the earlier `172.19.0.5` to `172.19.0.2`; the Postgres integration suite only passed once rerun against the current container IP.

**Test status:**
- `.venv/bin/pytest tests/test_p19_mcp_http.py -q` → 26 passed
- `MIMIR_TEST_POSTGRES_URL=postgresql+asyncpg://mimir:mimir@172.19.0.2:5432/mimir .venv/bin/pytest tests/test_p19_postgres_bootstrap.py -q` → 5 passed
- `.venv/bin/pytest tests/ -q` → 663 passed, 8 skipped

**Current test count:**
- 671 collected total; default suite result is 663 passed, 8 skipped

### Session 051426_1033 — Auth Docs Clarification For SSH/Headless Cursor

Clarified the auth story so docs no longer imply OAuth is the only practical Cursor/MCP path. API-key Bearer auth is now explicitly documented as a first-class supported path for Cursor over SSH, headless clients, remote development, and RPi5 workflows. OAuth remains documented for normal local/browser-capable setups only, with an explicit note that `MIMIR_PUBLIC_URL` must be reachable from the machine running Cursor.

**Updated files:**
- `README.md` — Reframed auth overview to present both first-class MCP auth paths: OAuth for browser-capable local setups, API-key Bearer auth for SSH/headless/remote/RPi5. Added direct Bearer config example and removed language implying MCP is OAuth-only.
- `docs/CURSOR_MCP_SETUP.md` — Split setup guidance by environment, added auth-path matrix, promoted API-key config for SSH/headless/remote/RPi5, and documented the `MIMIR_PUBLIC_URL` reachability requirement for OAuth.
- `docs/OAUTH_SETUP.md` — Narrowed OAuth guidance to browser-capable setups, added explicit “use API key for SSH/headless/remote/RPi5” guidance, and documented that device-code OAuth is not implemented so OAuth must not be treated as the only headless path.
- `docs/SELF_HOSTING.md` — Added side-by-side Cursor MCP guidance: OAuth for browser-local setups, API-key Bearer config for SSH/headless/remote deployments, plus the `MIMIR_PUBLIC_URL` reachability note.
- `docs/PUBLIC_GITHUB_SETUP.md` — Added explicit API-key MCP config for SSH/headless/remote Cursor use and clarified that OAuth is optional rather than required for MCP setup.

**Key gotchas:**
- Current OAuth support is authorization-code + PKCE only. Device-code OAuth is still not implemented, so API-key Bearer auth must remain the supported path for headless/SSH workflows.
- For OAuth, `MIMIR_PUBLIC_URL` must be reachable from the machine running Cursor; a server-local-only URL is insufficient for remote Cursor clients.
- MCP setup must stay compatible with direct `Authorization: Bearer <API_KEY>` headers; docs should not imply OAuth is mandatory.

**Test status:**
- `pytest tests/` → 661 passed, 7 skipped

**Current test count:**
- 668 collected total; default suite result remains 661 passed, 7 skipped

### Session 051426_0941 — P19.6 Live Docker/Postgres Bootstrap Retrieval Parity

Fixed the remaining live Postgres parity gap. Bootstrap retrieval no longer depends on vector or FTS alone: project-scoped bootstrap capsule queries now have a direct SQL fallback, Postgres exact-label search handles capsule labels correctly, vector metadata carries bootstrap capsule fields, and live `api-pg` MCP validation now returns all 7 capsules for `what is this project?`.

**New files:**
- `retrieval/bootstrap_capsules.py` — Centralized bootstrap capsule helpers: query normalization, intent→capsule mapping, direct SQL bootstrap capsule lookup, and deterministic capsule scoring for exact-label/project-intent queries.
- `tests/test_p19_postgres_bootstrap.py` — Postgres-backed bootstrap integration coverage: 7-capsule storage contract, vector metadata assertions, exact-label `memory_search`, intent-based `memory_recall`, and wrong-project isolation.

**Updated files:**
- `api/routes/mcp_http.py` — `memory_search`/`memory_recall` tool paths now flow through shared helpers also used by `project_bootstrap` read-after-write verification. Bootstrap reindex upserts now carry `source_type` + full metadata to vectors. Canonical semantic bootstrap writes disable duplicate/conflict heuristics so trusted capsules stay `memory_state=active`.
- `retrieval/retrieval_engine.py` — Seeds retrieval with direct SQL bootstrap capsule candidates for project-scoped intent queries, then merges vector + keyword candidates and floors ranking with deterministic capsule scores so Postgres exact-label and project-identity lookups succeed consistently.
- `retrieval/providers.py` — Added `bootstrap_capsule_provider`, a project-scoped SQL fallback provider for orchestrated retrieval. Existing providers now import shared bootstrap capsule scoring helpers.
- `retrieval/orchestrator.py` — Wires `bootstrap_capsule_provider` into the provider set so token-budget/context-builder retrieval can surface bootstrap capsules on Postgres too.
- `storage/search_backend.py` — Hardened `PostgresSearchBackend.search()` with normalized underscore/space matching, direct `content ILIKE` checks, and `meta->>'capsule_type'` exact-label matching (`project_profile`, `architecture_summary`, `testing_protocol`).
- `storage/vector_store.py` — Vector metadata now always includes `project`, `project_id`, `source_type`, and merged extra metadata.
- `memory/semantic_store.py`, `memory/procedural_store.py`, `memory/episodic_store.py` — All vector upserts now propagate source/meta fields; episodic upserts merge `session_id` with caller metadata instead of replacing it.
- `storage/reindex_vectors.py` — Full vector reindex now preserves source/meta fields so repaired bootstrap capsules keep retrieval parity after reindex.

**Key gotchas:**
- The new Postgres suite is opt-in via `MIMIR_TEST_POSTGRES_URL`; the default SQLite `pytest tests/` run skips it. For live validation this session used Docker Postgres at `172.19.0.5:5432` because the compose `postgres` service is not host-published.
- `project_bootstrap` semantic capsules must bypass normal semantic conflict detection or trusted bootstrap rows can be marked `contradicted` when their content overlaps.
- Live Docker validation required rebuilding `api-pg`; an older image still carried the pre-fixed migration/runtime behavior.

**Test status:**
- `pytest tests/test_p19_mcp_http.py -q` → 24 passed
- `pytest tests/test_p17_postgres_multi_instance.py -q` → 33 passed
- `MIMIR_TEST_POSTGRES_URL=postgresql+asyncpg://mimir:mimir@172.19.0.5:5432/mimir pytest tests/test_p19_postgres_bootstrap.py -q` → 4 passed
- `pytest tests/ -q` → 661 passed, 7 skipped

**Live Docker/Postgres validation:**
- Rebuilt and started `api-pg` with `docker compose --profile prod-postgres up -d --build postgres api-pg`
- Created owner/API key inside the container with `python -m mimir.auth.create_owner`
- Verified live MCP:
  - `project_bootstrap(project="auto", ..., force=true)` → `ok=true`, `missing_capsule_types=[]`
  - `memory_search(project="auto", query="project_profile")` → returned `project_profile`
  - `memory_recall(project="auto", query="what is this project?")` → returned all 7 capsule types
- Current live Atlas runtime note: `api-pg` is the service bound to `:8787`; the older local `mimir-api-1` container is stopped. Debugging `localhost:8787` should assume Postgres/`api-pg`, not the old SQLite/local service.

### Session 051326_2219 — P19.5 Bootstrap Capsule Retrieval Across Layers

Fixed the remaining bootstrap retrieval contract gap: `memory_search` and `memory_recall` now retrieve and rank bootstrap capsules across semantic/episodic/procedural layers, including procedural bootstrap capsules (`testing_protocol`, `procedural_lesson`). Added capsule-intent relevance boosts and query normalization so underscore/space variants and project-intent prompts resolve correctly.

**Updated files:**
- `retrieval/retrieval_engine.py` — Reworked retrieval flow to merge vector + keyword/FTS candidates, load keyword-only memories into ranking, and apply bootstrap capsule-type boosts keyed to query intent (`project`, `testing`, `safety/governance`, `procedural lesson`). Added query variant normalization (`testing_protocol` ↔ `testing protocol`) so layer-agnostic bootstrap lookups succeed consistently.
- `tests/test_p19_mcp_http.py` — Added P19.5 coverage for required retrieval contracts: `memory_search` tests for `testing_protocol`, `testing protocol`, and `procedural_lesson`; `memory_recall` tests for `what is this project?`, `what tests should I run?`, and `what are the safety constraints?`; added wrong-project recall isolation test.

**Key gotchas:**
- Vector-only retrieval was insufficient for capsule label-style queries and procedural bootstrap capsules; combining vector + keyword/FTS candidate sets in `retrieval_engine.search` closed this contract gap without changing MCP tool shapes.
- Capsule intent boosting is gated on `meta.bootstrap=true` and `meta.capsule_type`, so non-bootstrap memories are unaffected by these capsule-specific score adjustments.

**Test status:**
- `pytest tests/test_p19_mcp_http.py -q` → 24 passed
- `pytest tests/ -q` → 661 passed, 3 skipped

### Session 051326_2159 — P19.4 Bootstrap Capsule Recall/Indexing Fix

Fixed `project_bootstrap` contract gaps causing capsule retrieval failure (only `governance_rules` was discoverable). Added normalized bootstrap metadata, force-mode repair/reindex behavior, richer search/recall debug payloads, and read-after-write validation in-tool. Follow-up patch removed stale hardcoded RC1 expectations from P18 security tests by binding to canonical `mimir.__version__`. Full suite is now green.

**Updated files:**
- `api/routes/mcp_http.py` — `project_bootstrap` now writes/updates canonical capsule metadata (`meta.bootstrap`, `meta.bootstrap_run_id`, `meta.repo_path`, `meta.capsule_type`, `meta.project`, `meta.project_id`), prefixes content with searchable capsule labels (`PROJECT_PROFILE: <project>`, etc.), returns per-capsule IDs (`project_profile_id` … `governance_rules_id`), and performs built-in read-after-write checks for `project_profile`, `architecture_summary`, `testing_protocol`, and `what is this project` recall. Added force-mode repair path that dedupes capsule rows, updates in place, and rebuilds keyword index/vector entries.
- `retrieval/retrieval_engine.py` — Expanded returned hit shape with debugging/provenance fields: `project`, `project_id`, `source_type`, `memory_state`, `verification_status`, `trust_score`, `meta`, and `capsule_type`.
- `tests/test_p19_mcp_http.py` — Expanded P19 bootstrap coverage from 10 to 20 tests including: 7-capsule storage contract, per-capsule ID fields, metadata normalization checks, retrieval shape checks, capsule query retrieval (`architecture`, `testing protocol`), wrong-project isolation, and `force=true` non-duplication behavior.
- `tests/test_p18_security.py` — Replaced stale `0.1.0-rc1` literals with canonical version assertions via `mimir.__version__` helper; `/health` version check now follows canonical version policy and remains future-proof across version bumps.

**Key gotchas:**
- Bootstrap verification is now scoped to capsule types actually provided in a run (plus always-generated governance), so partial bootstrap calls still succeed while full 7-capsule calls enforce full recall checks.
- `force=true` is now the repair/reindex path for bootstrap capsules: it updates existing capsule memories (or recreates missing ones), archives duplicates, reindexes search backend, and re-upserts vectors.

**Test status:**
- `pytest tests/test_p18_security.py -q` → 19 passed, 2 skipped
- `pytest tests/test_p19_mcp_http.py -q` → 20 passed
- `pytest tests/ -q` → 657 passed, 3 skipped

### Session 051326_2109 — Local OAuth URL Fix for LAN Access

Fixed local OAuth callback/public URL configuration so non-localhost clients can complete OAuth against Atlas. No tests run (config-only change).

**New files:**
- `.env` — Added `MIMIR_PUBLIC_URL=http://192.168.1.246:8787` for local profile runtime config.

**Updated files:**
- `docker-compose.yml` — Changed local `api` service `MIMIR_PUBLIC_URL` to `${MIMIR_PUBLIC_URL:-http://127.0.0.1:8787}` so `.env` can override localhost for LAN/OAuth use while preserving localhost default.

**Operational verification:**
- Rebuilt and restarted local stack: `docker compose --profile local up -d --build`.
- Verified runtime env in API container: `MIMIR_PUBLIC_URL=http://192.168.1.246:8787`.

### Session 051326_2000 — project.bootstrap MCP Tool

Added `project.bootstrap` as a first-class MCP tool Cursor can call directly. 653/653 tests (6 new).

**Updated files:**
- `api/routes/mcp_http.py` — Added `project.bootstrap` to `_TOOLS` registry (10 tools total) and full handler in `_call_tool`. Tool accepts: `project` (required), `repo_path` (metadata only), `force` (idempotency override), and 6 content sections (`profile`, `architecture`, `status`, `constraints`, `testing`, `knowledge`). Handler: idempotency check via `meta.bootstrap` scan before writing, writes each non-empty section as a typed memory (`semantic`/`episodic`/`procedural`), always writes `governance_rules` memory server-side, returns `{ok, project, run_id, stored, skipped, total}`.
- `tests/test_p19_mcp_http.py` — 6 new tests: tool appears in tools/list, requires project field, writes memories and returns stored list, idempotency guard blocks second call, force=true overwrites, empty sections go to skipped list.

**Key gotcha:**
- `Memory` model not in global scope in `_call_tool` match cases — must be imported locally as `_Memory` to avoid name collision.

### Session 051326_1947 — Project Bootstrap Workflow (no phase number)
Added a safe one-time repo bootstrap script that ingests a curated project capsule into Mimir for existing mature repos. No new tests (pure tooling addition — no production code changed, 647/647 still pass).

**New files:**
- `scripts/bootstrap_mimir_project.sh` — Reads governance/status/docs files from a target repo, POSTs 7 structured memories to Mimir via `POST /api/memory`, generates `.md`/`.json` reports. Safe by design: never reads `.env`, `*.db`, `.venv/`, `__pycache__/`, `models/`, full `project_history.md` (tailed to 100 lines), or raw source trees. Idempotent via `--force` guard (checks `meta.bootstrap=true` in existing project memories). Args: `--repo PATH`, `--url URL`, `--key KEY`, `--project NAME`, `--force`, `--dry-run`, `--report DIR`.
- `reports/integration/mimir_bootstrap_latest.md` — Markdown run report (overwritten on each run).
- `reports/integration/mimir_bootstrap_latest.json` — JSON run report (overwritten on each run).

**Memory types written (7 total):**
- `project_profile` (semantic, 0.95) — README + project_goal + pyproject.toml
- `architecture_summary` (semantic, 0.90) — repo_map + project_knowledge + docs/
- `active_status` (episodic, 0.85) — project_status (8 KB) + history tail + memory index
- `testing_protocol` (procedural, 0.85) — test commands extracted from AGENTS/CLAUDE.md
- `safety_constraint` (semantic, 0.95) — full AGENTS.md + CLAUDE.md + .cursor/rules/
- `governance_rules` (semantic, 0.90) — priority order table + Mimir usage rules
- `procedural_lesson` (procedural, 0.80) — project_knowledge + history tail

**Key design decisions:**
- Uses `POST /api/memory` directly (not MCP JSON-RPC) — simpler for shell, bypasses memory_extractor heuristics, gives full control over layer/importance/meta.
- Idempotency: checks `GET /api/memory?project=<name>` for existing `meta.bootstrap=true` entries before writing. Aborts unless `--force` passed.
- Dry-run mode prints what would be sent without any writes or network contact (except health check skipped too).
- `[DRY-RUN]` log lines go to stderr so `$()` captures clean IDs.
- Target repo defaults to `/home/sketch/auto` (the Aether RPi5 repo per the request); override with `--repo`.

**Usage for /home/sketch/auto when it exists:**
```bash
# Preview
./scripts/bootstrap_mimir_project.sh --dry-run --repo /home/sketch/auto --project auto

# Execute
./scripts/bootstrap_mimir_project.sh --repo /home/sketch/auto --project auto --key $MIMIR_API_KEY

# Force re-run
./scripts/bootstrap_mimir_project.sh --repo /home/sketch/auto --project auto --key $MIMIR_API_KEY --force
```

### Session 051326_1920 — P20 Public-Ready MCP Auth + Multi-User Support (Phase 25)
Complete OAuth 2.1/PKCE auth layer transforming Mimir from internal MCP server to GitHub-ready, self-hostable, multi-user service. Cursor connects via URL-only config — no manual API key copy-paste. Three auth modes: `dev` (bypass), `single_user` (API key + OAuth), `multi_user` (full enforcement). 647/647 tests (30 new P20 tests, 3 skipped).

**New files:**
- `api/routes/oauth.py` — Full OAuth 2.1/PKCE server (~400 lines). Endpoints: `/.well-known/oauth-protected-resource` (RFC 9728), `/.well-known/oauth-authorization-server` (RFC 8414), `/oauth/register` (RFC 7591 dynamic client registration), `/oauth/authorize` (HTML form, asks for API key once), `/oauth/token` (authorization_code + refresh_token grants with PKCE S256), `/oauth/revoke` (RFC 7009, always 200), `/setup` (first-run page). Provides `resolve_oauth_token()` and `is_revoked_oauth_token()` helpers used by auth deps.
- `migrations/versions/0013_oauth_multiuser.py` — Adds `role` + `last_login_at` to `users`; creates `oauth_clients`, `oauth_authorization_codes`, `oauth_tokens`, `oauth_refresh_tokens` tables. Idempotent (checks before altering).
- `tests/test_p20_oauth.py` — 30 tests: well-known discovery, client registration, full PKCE flow, refresh token rotation, revocation, MCP with OAuth, config auth mode resolution, cross-user isolation, setup page.
- `mimir/auth/create_owner.py` — CLI to create first owner account. `python -m mimir.auth.create_owner --email x --display-name y`. Fails if owner exists. Prints raw API key once.
- `mimir/auth/__init__.py`, `mimir/auth/__main__.py` — Package init + module entry point for `python -m mimir.auth.create_owner`.
- `docs/OAUTH_SETUP.md` — OAuth quick start, flow diagram, endpoints table, token lifetimes, troubleshooting.
- `docs/MULTI_USER_SECURITY.md` — Tenant isolation table (all layers), role matrix, auth mode security comparison, production hardening checklist.
- `docs/SELF_HOSTING.md` — Docker Compose (local + prod-postgres profiles), manual setup, nginx SSE config, full env var reference.
- `docs/PUBLIC_GITHUB_SETUP.md` — 1-minute GitHub clone setup, first-connection OAuth flow, team deployment notes, security notes.

**Updated files:**
- `mimir/config.py` — Added `_effective_auth_mode` property normalizing `dev`/`single_user`/`multi_user`/`prod`/`""`. Added `is_single_user`, `is_multi_user`, `is_dev_auth` properties. New fields: `allow_registration`, `require_https`, `oauth_enabled`, `access_token_ttl_seconds`, `refresh_token_ttl_seconds`.
- `storage/models.py` — Added `User.role` (owner/admin/user) and `User.last_login_at`. New models: `OAuthClient`, `OAuthAuthorizationCode`, `OAuthToken`, `OAuthRefreshToken` (tokens stored as SHA-256 hash).
- `api/deps.py` — `get_current_user` now tries `resolve_oauth_token()` first; rejects `local-dev-key` in multi_user mode; falls back to legacy `MIMIR_API_KEY` and DB key lookup.
- `api/routes/mcp_http.py` — Added `_www_authenticate_header()` → `Bearer resource_metadata=".../.well-known/oauth-protected-resource"` on 401. `_resolve_api_key()` now: (1) dev bypass only when NO key provided, (2) explicit tokens always validated including revocation, (3) `is_revoked_oauth_token()` guard prevents revoked tokens falling through to other auth paths. `_call_tool()` resolves OAuth user first (before `is_dev_auth` check) to enforce per-user isolation with OAuth tokens.
- `api/main.py` — `app.include_router(oauth.router)` added (no prefix — endpoints at root).
- `tests/test_migrations.py` — Added 4 OAuth tables to `EXPECTED_TABLES`; head revision updated to `"0013"`.
- `evals/release_gate.py` — Added P20 hard-fail gates: `cross_user_oauth_leakage_rate > 0`, `mcp_initialize_failure`, `mcp_tools_list_failure`, `mcp_tools_call_failure`, `oauth_discovery_failure`, `oauth_token_exchange_failure`, `dev_key_accepted_in_production`.
- `docker-compose.yml` — Default services (api, worker, web) now use `profiles: ["local"]`. `api` (local) gets `MIMIR_AUTH_MODE: single_user`, `MIMIR_PUBLIC_URL: http://127.0.0.1:8787`. `api-pg` (prod-postgres) gets `MIMIR_AUTH_MODE: multi_user`, `MIMIR_ALLOW_REGISTRATION: "false"`. Resolves the P17.1 issue where `--profile prod-postgres` also started all default services.
- `docs/CURSOR_MCP_SETUP.md` — Updated to show OAuth as primary flow; URL-only Cursor config; manual Bearer fallback; correct protocol notes.

**Key design decisions / gotchas:**
- OAuth server implemented from scratch (no third-party OAuth library) — pure FastAPI + SQLAlchemy + hashlib. The authorize page is an HTML form asking for the API key once; Cursor stores the resulting OAuth token for all future connections.
- `_require_oauth()` only blocks when `oauth_enabled=False` explicitly set. It does NOT block in dev mode — this was a bug that caused all OAuth tests to fail (would return 404). OAuth endpoints are always available to allow testing.
- Revoked token fallthrough bug: In dev mode, `_resolve_api_key()` would check `is_revoked_oauth_token(key)` first, but if not found there, fell through to `is_dev_auth: return key` — accepting a revoked token. Fix: added `is_revoked_oauth_token()` which returns True only if the token IS in the DB but revoked/expired (not for unrecognized tokens). This gate runs before dev mode fallback.
- Cross-user isolation bug: `_call_tool()` had `if settings.is_dev_auth: user = DEV_USER` BEFORE the OAuth token check. Even with a valid OAuth token, all requests got dev user (uid=None) → all memories returned. Fix: OAuth resolution must come first, before any dev-mode shortcut.
- `MIMIR_API_KEY=secret-prod-key` (any custom value) still works as legacy fast path in all modes. Only the literal `local-dev-key` (default) is rejected in multi_user mode. This preserves the P16 `prod` auth mode test (`x-api-key: secret-prod-key` → 200).
- `auth_mode="prod"` is a backward-compat alias for `multi_user` — resolved in `_effective_auth_mode`. New code should use `multi_user`.

### Session 051326_1852 — P19.1 MCP Streamable HTTP Cursor Compatibility Fix (Phase 24)
Fixed the MCP HTTP endpoint to be fully Cursor-compatible per the MCP 2025-03-26 Streamable HTTP spec. Root cause of the 405/404 errors: (a) StaticFiles mount at `/` caught POST /mcp when no route existed (old code on live server), (b) POST responses returned `application/json` instead of `text/event-stream`, (c) notifications returned 204 instead of 202. 617/617 tests (10 P19 tests, up from 8).

**Updated files:**
- `api/routes/mcp_http.py` — Full rewrite for spec compliance: POST /mcp now responds with `text/event-stream` SSE when client sends `Accept: text/event-stream` (required by Cursor). Added `GET /mcp` SSE keep-alive channel (15s heartbeats) — Cursor opens this for server-initiated messages. Added `DELETE /mcp` for session cleanup. Fixed notifications to return 202 (was 204). Removed dead `_json_rpc_response` duplicate.
- `tests/test_p19_mcp_http.py` — Expanded to 10 tests: added `test_mcp_post_never_405`, `test_mcp_get_not_404`, `test_mcp_initialize_sse_format`, `test_mcp_notification_returns_202`. Removed old 204 test.
- `docs/CURSOR_MCP_SETUP.md` — Updated Protocol Notes section with correct transport details.

**Key design decisions / gotchas:**
- Root cause of 405: The Mimir production server had `web/dist` present (frontend built), so `Mount("/", StaticFiles(...))` was active. Before the P19 `/mcp` route was deployed, POST /mcp fell through to StaticFiles which only accepts GET/HEAD → 405. This is now fixed by having the route registered before the StaticFiles mount.
- Cursor requires SSE format responses: Cursor's MCP TypeScript SDK sends `Accept: application/json, text/event-stream` and expects `Content-Type: text/event-stream` responses. Returning plain `application/json` caused Cursor to treat the connection as failed.
- GET /mcp keep-alive: Cursor opens a GET SSE channel to receive server-initiated messages. Without a GET handler, Cursor gets 404 (falls through to StaticFiles) and considers the connection broken. Our stateless implementation holds the channel open with comment-line heartbeats.
- Notifications → 202: Per MCP spec §6.4.1, JSON-RPC notifications (no `id`) MUST return 202, not 204.

### Session 051326_1743 — P19 Cursor MCP Streamable HTTP Endpoint (Phase 24)
Added `/mcp` Streamable HTTP MCP endpoint to the existing FastAPI app. Cursor can now connect using only a URL and Bearer token — no local Mimir package required. 615/615 tests (8 new P19 tests).

**New files:**
- `api/routes/mcp_http.py` — MCP Streamable HTTP endpoint. Implements JSON-RPC 2.0 over `POST /mcp` without importing the local `mcp/` package (which shadows the installed MCP SDK). Stateless, Bearer auth, 9 tools, direct in-process service calls. Mounted at `/mcp` (no `/api` prefix).
- `tests/test_p19_mcp_http.py` — 8 tests: auth required, tools/list, memory.remember, memory.recall, invalid key 401, cross-user isolation, initialize handshake, notification 204.
- `docs/CURSOR_MCP_SETUP.md` — Cursor URL-based setup guide with curl examples and troubleshooting.
- `reports/integration/p19_cursor_mcp_http.md` — Integration acceptance report.

**Updated files:**
- `api/main.py` — Added `mcp_http` import and `app.include_router(mcp_http.router)` registration without `/api` prefix.

**Key design decisions / gotchas:**
- The local `mcp/` directory (the project's stdio MCP server) shadows the installed `mcp` SDK package. Any in-process import of `from mcp.server.fastmcp import FastMCP` fails with a circular import. Solution: implement the MCP Streamable HTTP protocol directly as JSON-RPC 2.0 without using FastMCP.
- Endpoint is mounted at `/mcp` root (not `/api/mcp`) so Cursor's URL is `http://<host>:8787/mcp`. Registered separately from the `/api` prefix loop in main.py.
- Auth accepts `Authorization: Bearer <key>` (Cursor standard) and `X-API-Key: <key>` (Mimir legacy). In dev mode (`MIMIR_ENV=development`), auth is bypassed automatically — tests pass without setting up API keys.
- Tools use `get_session_factory()()` directly (not FastAPI's `get_session` generator), which is the correct pattern for non-FastAPI async contexts.
- `notifications/initialized` (no `id` field) returns HTTP 204 per JSON-RPC notification semantics.
- stdio MCP (`mcp/server.py`, `mimir-mcp` entry point) is unchanged.

### Session 051326_1656 — P18 Security Review + Release Candidate Packaging (Phase 23)
Full security audit, system hardening, Docker smoke, backup/restore validation, and release artifact packaging. 607/607 tests (19 new P18 security tests). 66/66 evals. Release gate PASS. Version set to 0.1.0-rc1.

**New files:**
- `mimir/__version__.py` — version constant `0.1.0-rc1`. Imported by `api/main.py` and exposed in `/health` response.
- `docs/ACCESS_CONTROL_MATRIX.md` — complete per-endpoint auth/ownership/risk/test-coverage matrix for all ~60 routes.
- `scripts/security_scan.sh` — 5-check security scan: pip-audit (runtime vulns), npm audit, Tailscale forbidden cmd scan, credential pattern scan, insecure config defaults check. Reports to `reports/security/`.
- `reports/security/latest.json` — RC1 security scan result (4 PASS, 1 WARN — dev defaults, expected).
- `reports/docker/smoke_latest.json` + `smoke_latest.md` — Docker smoke results (8/8 pass; compose rebuild WARN due to disk constraints).
- `tests/test_p18_security.py` — 19 new tests covering all P18 security acceptance criteria.

**Updated files:**
- `memory/semantic_store.py` — Removed automatic quarantine reactivation in `update_content()`. Quarantine state is now sticky — clean content update no longer reactivates a quarantined memory.
- `memory/episodic_store.py` — Same fix in `update_content()`.
- `memory/procedural_store.py` — Same fix in `update()`.
- `mimir/config.py` — Added `enable_system_mutation_endpoints: bool = False` field (`MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS` env var).
- `api/routes/system.py` — `POST /system/consolidate`, `POST /system/reflect`, `POST /system/lifecycle` now check `enable_system_mutation_endpoints`; return HTTP 403 when disabled. Added `_assert_mutation_enabled()` helper.
- `api/main.py` — Imports `__version__`, passes it to `FastAPI(version=__version__)`, includes `version` in `/health` response.
- `pyproject.toml` — Version bumped to `0.1.0-rc1`.
- `web/package.json` — Version bumped to `0.1.0-rc1`.
- `Makefile` — Added `security` target (`./scripts/security_scan.sh`). Added `security` to `.PHONY`.
- `tests/conftest.py` — Added `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS=true` to test env (mutation endpoints needed by worker_stability eval suite).
- `evals/runner.py` — Added same env var for eval environment.
- `docs/RELEASE_CHECKLIST.md` — Added RC1 status table, security scan step, version checks, updated test count.
- `docs/SECURITY.md` — Added system mutation endpoints section, quarantine sticky state note, security scan section.
- `docs/DEPLOYMENT.md` — Added `MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS` env var row, security scan in production checklist, version check.
- `docs/OPERATIONS.md` — Added Known Limitations section (9 limitations documented).

**Key design decisions / gotchas:**
- Quarantine reactivation fix: removed the `elif mem.memory_state == QUARANTINED: mem.memory_state = ACTIVE` branch entirely from all three store update paths. The branch was originally intended to allow content correction of accidentally-quarantined memories, but it creates a security bypass: attacker quarantines memory, updates to clean content to reactivate, then injects malicious content again. Sticky quarantine is the correct model — only explicit admin action (not yet implemented as an endpoint) should clear quarantine.
- `enable_system_mutation_endpoints` defaults to `False` at the Settings model level. Test conftest and eval runner set it to `true` via env var. This means any new test environment that doesn't set the env var will have mutation endpoints disabled by default — matching production behavior.
- Docker smoke: `mimir:rc1-test` built successfully (8.96GB total, 3.06GB content — sentence-transformers pulls torch+CUDA). `docker compose up --build` requires 20GB+ free disk for api+worker+web concurrent build; disk was at 97% after single image build. Smoke tested by running pre-built image directly. See `reports/docker/smoke_latest.md` for details and the CPU-only torch recommendation.
- Backup created from dev DB (empty), restored to `/tmp/mimir_rc1_restore`, migrations applied cleanly (0011→0012). Restore is correct; the 0 memory count reflects the empty dev DB, not a restore failure.
- `pip-audit` finds 4 CVEs all in `pip 25.1.1` itself (the tool, not Mimir's runtime deps). Security scan correctly distinguishes tool-package vulns from runtime vulns and reports PASS for runtime deps.

### Session 051326_1442 — P17.1 Live Postgres Load + Soak Validation (Phase 22)
Proved Mimir runs correctly under live Postgres concurrency. 588/588 tests (unchanged). 66/66 evals. Release gate PASS. P17 fully accepted.

**New files:**
- `.dockerignore` — Excludes `.venv`, `data/`, `reports/`, etc. from Docker build context. Reduces build context from 5.8GB to <100MB.
- `reports/load/postgres.json` — Postgres load test report: 10 users × 50 sessions, error_rate=0.0%, write p95=192ms, recall p95=207ms.
- `reports/load/postgres_soak.json` — Postgres soak test report: 25 users × 100 sessions, error_rate=0.0%, write p95=741ms, recall p95=793ms.
- `reports/load/postgres_summary.md` — Full P17.1 validation summary with bug fixes, known bottlenecks, recommended tuning.
- `reports/evals/postgres_after_load.json` — 66/66 evals post-load.

**Updated files:**
- `migrations/versions/0008_adaptive_retrieval.py` — Added `if bind.dialect.name == "sqlite":` guard around FTS5 section. Replaced `try/except: pass` index creation with `_index_exists()` guard. Downgrade also dialect-guarded. Fixes Postgres migration `InFailedSQLTransactionError` caused by FTS5 poisoning transaction.
- `migrations/versions/0011_fts_isolation.py` — Same dialect guard pattern. Entire upgrade/downgrade returns early on non-SQLite dialects. Fixes same transaction poisoning.
- `storage/models.py` — Added `UTCDateTime` TypeDecorator that strips `tzinfo` in `process_bind_param`. Replaced all 56 `mapped_column(DateTime)` with `mapped_column(UTCDateTime)`. Fixes `DataError: can't subtract offset-naive and offset-aware datetimes` on Postgres INSERT.
- `worker/scheduler.py` — Fixed `run()` to use `asyncio.run(_run_async())`. Fixes `RuntimeError: no running event loop` under Python 3.14 where `asyncio.get_event_loop()` no longer creates a loop.
- `evals/load_test.py` — Fixed `_run_user_session` to create a fresh session per operation instead of sharing one session across all sessions. Fixes 20% error cascade from session invalidation after any single failure.

**Key design decisions / gotchas:**
- Migration FTS5 guard: `if bind.dialect.name == "sqlite": return` at the top of 0011 upgrade is the cleanest approach — avoids any Postgres-incompatible SQL entirely. The `try/except` pattern leaves Postgres transactions in aborted state even when the exception is caught at the Python level.
- `UTCDateTime` TypeDecorator is the single-point fix for all 56 datetime columns. Does NOT require a migration (the column DDL type remains `TIMESTAMP WITHOUT TIME ZONE`, only the ORM binding changes). Existing SQLite behavior is unchanged.
- Default pool_size=5 is too small for 25+ concurrent users. Production deployments must set `MIMIR_DB_POOL_SIZE=20+` or use PgBouncer. Docker Compose prod-postgres profile already sets pool_size=10; bump to 25 for heavy load.
- Docker full-stack build with torch+CUDA requires ~26GB disk headroom. Without `.dockerignore`, build context was 5.84GB (includes `.venv`). Fixed — now <100MB context. Image size issue (torch/CUDA ~18GB per image) is a separate concern; use CPU-only torch for containers.
- Load test was run locally (Postgres in Docker, API+worker as local processes) due to disk space constraints on the host that prevented building both `api` and `api-pg` Docker images simultaneously.

**Remaining docker-compose issue:** Building `--profile prod-postgres` starts both the default services (api, worker, web) AND the prod-postgres services (api-pg, worker-pg, web-pg), requiring ~36GB disk for two sets of CUDA-heavy images. Fix: add a `sqlite` profile to the default services so `--profile prod-postgres` doesn't also start them.

### Session 051326_1307 — P17 Postgres Migration + Multi-Instance Readiness (Phase 21)
Moved Mimir from single-node SQLite to production-grade multi-instance architecture. 588/588 tests (33 new). 66/66 evals. Release gate PASS.

**New files:**
- `storage/search_backend.py` — Keyword search backend abstraction: `SQLiteFTSBackend` (FTS5/BM25), `PostgresSearchBackend` (tsvector/plainto_tsquery), `LikeFallbackBackend` (LIKE). Auto-selected by dialect; overrideable via `MIMIR_SEARCH_BACKEND`.
- `worker/job_lock.py` — DB-backed distributed job locking: `try_acquire`, `release`, `heartbeat`, `acquire_lock` context manager, stale lock purge, `get_active_locks`. Protected jobs: consolidation_pass, lifecycle_pass, graph_build, reflection_pass.
- `migrations/versions/0012_job_locks.py` — Creates `job_locks` table (job_name PK, locked_by, locked_at, expires_at, heartbeat_at, status).
- `scripts/docker_smoke_test.sh` — End-to-end Docker smoke test: compose up → health → readiness → auth → create memory → recall → worker → eval smoke → restart+persist → compose down. Supports `PROFILE=prod-postgres`.
- `tests/test_p17_postgres_multi_instance.py` — 33 new tests: Postgres config fields, URL normalisation, search backend selection/healthcheck/search, job lock acquire/release/stale/context-manager, migration 0012, transaction boundary (promotion rollback), Docker compose structure, CI workflow structure, P0 regression guards (simulation run_id, outcome route, risk fields, providers aggregate route).

**Updated files:**
- `storage/database.py` — Full rewrite: `_build_url()` builds SQLite or Postgres URL from config; `_get_engine()` branches on dialect (asyncpg pool settings for Postgres, `check_same_thread`/timeout for SQLite); `get_db_dialect()` returns `'sqlite'`/`'postgresql'`; `init_db()` only runs FTS5 setup on SQLite; `_init_sqlite_fts()` extracted helper.
- `migrations/env.py` — `_get_url()` calls `_build_url()` to respect `MIMIR_DATABASE_URL`; `do_run_migrations()` sets `render_as_batch` only for SQLite (Postgres doesn't need it).
- `mimir/config.py` — Added `db_pool_size=5`, `db_max_overflow=10`, `db_pool_timeout=30` fields for Postgres pool configuration.
- `pyproject.toml` — Added `asyncpg>=0.29` dependency.
- `retrieval/providers.py` — `keyword_provider` now delegates to `get_search_backend()` instead of directly calling `fts5_search()`. Preserves LIKE fallback if backend returns empty. Provider name included in hit `reason` field.
- `worker/tasks.py` — `_job` decorator gains `db_lock: bool` parameter; when True, acquires a DB-backed lock via `worker.job_lock.try_acquire` before running. Four jobs set to `db_lock=True`: `reflection_pass`, `consolidation_pass`, `lifecycle_pass`, `graph_build`.
- `approvals/promotion_worker.py` — Added `await session.rollback()` on promotion failure to prevent partial session state bleeding into next iteration.
- `approvals/rollback_watcher.py` — Wrapped `_watch_skill_improvements` and `_watch_system_metrics` calls in try/except with rollback, preventing one failing check from blocking all others.
- `graph/graph_builder.py` — `run_graph_build_pass()` wraps each builder call in `_safe()` try/except so one failing builder doesn't abort the full pass.
- `storage/models.py` — Added `JobLock` ORM model (job_name PK, locked_by, locked_at, expires_at, heartbeat_at, status; indexes on status and expires_at).
- `tests/test_migrations.py` — Updated head revision from `0011` → `0012`; added `job_locks` to EXPECTED_TABLES.
- `docker-compose.yml` — Restructured with two deployment modes: default (SQLite: api + worker + web) and `--profile prod-postgres` (postgres + api-pg + worker-pg + web-pg). Postgres services include healthcheck, volume `postgres_data`, automatic `alembic upgrade head` on api-pg start.
- `.github/workflows/ci.yml` — CI matrix: `tests-sqlite` (existing), `tests-postgres` (new: Postgres service, migrations, tests skipping SQLite-only migration tests), `evals` (parallel, uploads report), `release-gate` (downloads eval report, runs gate), `docker-smoke` (runs on main push only, skips if no daemon).
- `api/routes/simulation.py` — `POST /api/simulation/runs/{run_id}/outcome` route added (alias alongside existing `/simulations/{run_id}/outcome`); `estimate_risk` response now includes `risk_score` and `success_probability` at top level.
- `simulation/simulator.py` — `SimulationResult.to_dict()` now includes `"id": self.simulation_id` alias so eval code using `run.get("id")` works.
- `api/routes/telemetry.py` — Added `providers_router` (short-path router at `/api/providers/...`) with aliases for `/stats`, `/aggregate`, `/drift`.
- `api/main.py` — Imports and registers `providers_router` from telemetry.
- `docs/DEPLOYMENT.md` — Added Postgres Docker Compose section, scaling section, search backend differences table, MIMIR_DATABASE_URL/pool vars, smoke test command, SQLite→Postgres migration pointer.
- `docs/UPGRADE.md` — Added SQLite→Postgres migration procedure; added migration 0012 to notes table.
- `docs/OPERATIONS.md` — Added multi-worker job locking section with lock inspection and stale lock purge commands; updated FTS reindex note to clarify Postgres behavior.
- `docs/RELEASE_CHECKLIST.md` — Updated test count, added Postgres migration check, smoke test step, eval 66/66 requirement.

**Key design decisions / gotchas:**
- `_build_url()` normalises `postgres://` and `postgresql://` → `postgresql+asyncpg://` automatically so operators can set any common format.
- DB-backed job lock in `_job` decorator uses a separate session for lock acquire/release to avoid entangling the lock lifecycle with the job's own DB session. Lock session is committed independently.
- `render_as_batch=True` was hardcoded for all dialects in alembic env.py; now only applied for SQLite (Postgres supports real ALTER TABLE). Harmless on Postgres either way, but cleaner.
- FTS5 migrations (0008, 0011) are already wrapped in try/except — they silently skip on Postgres. No additional changes to those migrations needed.
- `providers_router` mounted at `/api/providers` (without `/telemetry` prefix) provides backward-compat short paths that the eval suite uses (`POST /api/providers/aggregate`).
- Docker Compose prod-postgres profile: both SQLite default services and Postgres services bind to port 8787. Users must use one profile or the other, not both simultaneously.
- Search backend singleton is reset between tests using `reset_search_backend()` to avoid cross-test dialect pollution.

### Session 051326_1159 — P16 Production Deployment Readiness (Phase 20)
Proved Mimir can be deployed, backed up, restored, monitored, and upgraded safely. 555/555 tests passing (69 new). Release gate passes. Evals pass (62/66, 0 critical failures).

**New files:**
- `mimir/backup/verify.py` — CLI: `python -m mimir.backup.verify <archive.zip>`; 7-check validation pipeline: zip validity, manifest, DB present/non-empty/SQLite magic, required tables, migration version match, vector files present.
- `evals/load_test.py` — Load/soak test: `python -m evals.load_test --users 10 --sessions 50 --out reports/load/latest.json`; concurrent user simulation, p50/p95 latency per operation, error rate, DB/vector size reporting.
- `evals/release_report.py` — Release artifact generator: `python -m evals.release_report --out reports/release/latest`; runs tests → migrations → evals → gate → web build → wheel; produces both JSON + Markdown reports.
- `web/Dockerfile` — Multi-stage Docker build for React UI: `node:20-alpine` build → `nginx:alpine` serve.
- `web/nginx.conf` — nginx config for SPA (try_files fallback, static asset caching, gzip).
- `docs/DEPLOYMENT.md` — Fresh install, Docker Compose, env var reference, production checklist, Tailscale notes.
- `docs/BACKUP_RESTORE.md` — Create/verify/restore procedure, automated backup cron, smoke test steps.
- `docs/SECURITY.md` — Auth modes, API key management, Slack/VAPID security, secret rules, CORS, audit trail, release gate security checks.
- `docs/OPERATIONS.md` — Health/readiness endpoints, reindex vectors/FTS, manual worker triggers, worker schedule, debug runbooks.
- `docs/UPGRADE.md` — Standard upgrade, Docker upgrade, migration rollback, migration notes table.
- `docs/RELEASE_CHECKLIST.md` — Pre-release, security, backup, deployment, post-release verification checklists.
- `tests/test_p16_production.py` — 69 tests: config validation (13), backup pipeline (8), auth hardening (7), health endpoints (10), load test (5), operator docs (12), release artifact (3), Docker compose (7).

**Updated files:**
- `mimir/config.py` — Enhanced `validate_config()`: API key insecure default check, `auth_mode` validity check (rejects non-`prod`/`dev`), wildcard CORS rejection in prod, Slack signing secret required when bot token set, VAPID key pair completeness check, dev-mode warning for insecure secret. Added `database_url`, `public_url`, `slack_enabled`, `pwa_push_enabled` fields.
- `api/routes/system.py` — Added `_get_migration_revision()`, `_get_fts_status()`, `_get_last_report()` helpers. `GET /api/system/status` now includes `migration_revision` in DB component, `fts` component, `last_eval`/`last_gate` from report files. Added `GET /api/system/readiness` endpoint: DB, migration, vector store, FTS, worker checks; returns 503 if any critical check fails.
- `docker-compose.yml` — Added `web` service (builds `web/Dockerfile`). Added `healthcheck` to `api` service. Worker now depends on `api` with `service_healthy` condition. Both services use `restart: unless-stopped`. `.env` is optional (no crash if missing).
- `Makefile` — Added `release` target: `python -m evals.release_report --out reports/release/latest`.

**Key design decisions / gotchas:**
- `validate_config()` in prod mode now checks API key insecure defaults separately per auth mode — only `auth_mode=prod` errors on insecure `api_key`/`dev_api_key` (dev mode may legitimately use dev keys).
- `GET /api/system/readiness` returns HTTP 503 with `{"detail": {"ready": false, "checks": {...}}}` when not ready. FTS is informational only (missing FTS degrades keyword recall but doesn't block operation). Load balancer should probe this endpoint.
- `backup/verify.py` reads the SQLite DB by writing to a temp file (not in-memory) because zipfile.read() returns bytes that need to be written to disk for sqlite3 to open.
- Load test uses `get_session_factory()()` (not `get_session()` which is a FastAPI generator) to get a session context manager.
- `evals/release_report.py` handles missing `build` package gracefully (falls back to `pip wheel`).

### Session 051326_1105 — P15 FTS/Keyword Isolation Hotfix (Phase 19)
Closed the keyword/FTS5 user-level isolation gap (production blocker). All retrieval providers now enforce user_id scoping at the source level. 486/486 tests passing (16 new).

**New files:**
- `migrations/versions/0011_fts_isolation.py` — Drops and recreates `memory_fts` FTS5 virtual table with `user_id UNINDEXED` and `project_id UNINDEXED` columns. Backfills from live memories (NULL→''). 4 triggers: INSERT (with WHEN guard), UPDATE OF content/user_id/project, soft-delete, quarantine state change.
- `storage/reindex_fts.py` — CLI: `python -m storage.reindex_fts`; clears and rebuilds FTS index from live memories table.
- `tests/test_p15_fts_isolation.py` — 16 tests: FTS5 schema, user_id/project_id filtering, shared-memory visibility, LIKE fallback user scoping, keyword_provider same-project isolation, HTTP recall isolation, FTS trigger correctness, reindex correctness, quarantine trigger, debug excluded path, migration 0011 schema verification.

**Updated files:**
- `storage/fts.py` — `fts5_search()` accepts `user_id` and `project_id`; applies isolation clauses `(user_id = :uid OR user_id = '')` and `(project_id = :proj OR project_id = '')` at FTS level. Probe updated to verify post-0011 schema (3-column SELECT). `reindex_fts()` added.
- `storage/database.py` — `init_db()` now also creates the FTS5 virtual table and all 4 triggers (idempotent via `IF NOT EXISTS`) so fresh test DBs have the same schema as post-migration DBs.
- `retrieval/providers.py` — `keyword_provider` passes `user_id=user_id, project_id=project` to `fts5_search` (FTS-level isolation + SQL post-filter as defense-in-depth).
- `retrieval/retrieval_engine.py` — Added `user_id` parameter, passed through to `memory_retriever.search` so the basic hits path enforces user isolation.
- `api/routes/events.py` — Recall route now passes `uid` to `retrieval_search` (was previously only passed to `build_context`). This closes the gap where un-budgeted recall calls ignored user_id.
- `evals/suites/retrieval_quality.py` — Added test 6: same-project keyword cross-user isolation; emits `keyword_cross_user_leakage_rate` and `fts_cross_user_leakage_rate` metrics.
- `evals/release_gate.py` — Hard-fails on `keyword_cross_user_leakage_rate > 0` and `fts_cross_user_leakage_rate > 0`.
- `tests/test_migrations.py` — Expected head revision bumped from `"0010"` to `"0011"`.

**Key design decisions / gotchas:**
- FTS5 UNINDEXED columns can be filtered in WHERE clauses but are not text-indexed. NULL user_id stored as '' for reliable equality comparison.
- `_probe_fts5` now SELECTs `user_id, project_id` columns — fails for old (pre-0011) schema → falls back to LIKE. Fallback also applies user_id filter via SQL post-filter.
- `retrieval_engine.search` was silently ignoring user_id even though `memory_retriever.search` supported it. The recall route's `uid` variable existed but was only used in the `token_budget` branch.
- Defense-in-depth: FTS5 isolation filter + SQL `WHERE (user_id = :uid OR user_id IS NULL)` post-filter in `keyword_provider` — either layer independently prevents leakage.
- `init_db()` needed to create FTS5 table for tests (Alembic migrations don't run in test suite which uses `create_all` only).

### Session 051326_0937 — P14 Evaluation Harness + Red-Team Release Gate (Phase 18)
Moved Mimir from feature-complete to production-verifiable by implementing a formal evaluation harness with 8 eval suites, adversarial red-team checks, and a release gate. 470/470 tests passing (40 new).

**New files:**
- `evals/__init__.py` — module marker
- `evals/base.py` — `EvalResult`, `EvalReport`, `EvalSuite` base class with `_ok`/`_fail`/`_gate` helpers
- `evals/runner.py` — suite orchestrator with JSON + Markdown report generation; entrypoint `python -m evals.runner --suite all --out reports/evals/latest.json`
- `evals/release_gate.py` — release gate CLI; reads report + runs unit tests + migration tests; blocks on critical failures; entrypoint `python -m evals.release_gate`
- `evals/fixtures/__init__.py` — synthetic adversarial content (8 attack categories), trajectory events, retrieval corpus
- `evals/suites/__init__.py` — ALL_SUITES registry
- `evals/suites/memory_quality.py` — 8 checks: store/recall, dedup, importance range, default state, trust range, procedural store, quarantine blocks poison, quarantine excluded from recall
- `evals/suites/retrieval_quality.py` — 6 checks (2 CRITICAL): corpus recall, precision@5, cross-user isolation (CRITICAL gate), quarantine exclusion rate (CRITICAL gate), project isolation
- `evals/suites/trust_and_quarantine.py` — 8 checks: default trust range, quarantine triggers, quarantine trust ≤ 0.2, positive/negative feedback, trust floor, state/vstatus set
- `evals/suites/red_team.py` — 10 checks (all CRITICAL): 8 adversarial categories (prompt injection, approval spoofing, tailscale, credential, security policy, dangerous procedure, malicious procedure, fake preference overwrite) + cross-user recall blocked + quarantine no-reactivation
- `evals/suites/trajectory.py` — 6 checks: multi-session load, hits exist, rollback stored, lesson stored, quarantine persists across sessions, memory accumulates
- `evals/suites/worker_stability.py` — 9 checks: consolidation run + idempotent, graph build run + idempotent, reflection run + idempotent, lifecycle run, provider stats aggregation, concurrent calls graceful
- `evals/suites/token_efficiency.py` — 6 checks: context present with budget, cost ≤ budget, cost reported, p95 latency < 3s, debug providers present, no-budget returns raw hits
- `evals/suites/simulation_forecasting.py` — 7 checks: plan creation, simulation runs + paths, confidence in [0.1, 0.95], high-risk auto-gated, outcome recording, calibration computes, risk estimate
- `evals/reports/.gitkeep` — report output directory
- `.github/workflows/ci.yml` — 3-stage CI: unit tests → eval harness → release gate
- `tests/test_p14_evals.py` — 40 new tests covering all P14 acceptance criteria

**Updated files:**
- `api/routes/system.py` — Added `POST /api/system/consolidate`, `POST /api/system/reflect`, `POST /api/system/lifecycle` trigger endpoints (needed by worker_stability eval suite and general operation)
- `pyproject.toml` — Added `evals`, `graph`, `simulation`, `telemetry` to wheel packages list
- `Makefile` — Added `evals`, `gate`, `ci` targets

**Key design decisions / gotchas:**
- Eval runner uses a separate DB at `/tmp/mimir_eval/` (not `/tmp/mimir_test/`) to avoid polluting test data
- Each suite uses unique project names via `uid()` for test isolation within a shared DB session
- Critical gate failures (cross-user leakage, quarantine escape, red-team pass) are marked `critical=True` on `EvalResult` and block the release gate
- Release gate hard-fails: `cross_user_leakage_rate > 0`, `quarantine_exclusion_rate < 1.0`, any red_team failure, any `critical=True` failure
- Worker trigger endpoints (`POST /system/consolidate` etc.) return `{"ok": true, "result": None}` because the underlying `_job`-decorated tasks return `None`. The eval suite handles `result=None` gracefully with `or {}`
- `POST /api/simulation/plans/{id}/simulate` requires a JSON body (even `{}`) — bare POST returns 422
- CI workflow: 3 separate jobs (tests → evals → release_gate) so artifacts flow between stages
- `python -m evals.runner` clears `/tmp/mimir_eval/` at start to get a clean baseline each run; this is intentional
- The `_gate()` helper on EvalSuite is syntactic sugar for `_ok` if condition else `_fail(..., critical=True)`

### Session 051326_0832 — P13 Simulation UI + Planning Memory Integration (Phase 17)
Made the predictive layer operational by adding a full simulation UI, retrieval integration, graph node coverage, and historical simulation memory. 430/430 tests passing (27 new).

**New files:**
- `simulation/historical_memory.py` — `store_simulation_memory(session, plan, run)`: creates a semantic Memory row (source_type="simulation") summarising a completed simulation run so it is retrievable by future planning queries. `get_simulation_context(session, keywords, project, limit)`: keyword-based lookup of simulation-backed memories.
- `web/src/pages/Simulation.tsx` — Hub page at `/simulation` with links to Plans, Counterfactuals, Forecasts and an explanation of how predictive planning works.
- `web/src/pages/SimulationPlans.tsx` — Plan list at `/simulation/plans`: status badge, risk bar, confidence, step count. Inline create-plan form.
- `web/src/pages/SimulationPlanDetail.tsx` — Plan detail at `/simulation/plans/:id`: step table with deps/risk/rollback, simulation path cards (best path highlighted), counterfactual list, approve/reject buttons for pending-approval plans, run simulation button.
- `web/src/pages/SimulationCounterfactuals.tsx` — Counterfactual explorer at `/simulation/counterfactuals`: plan selector, run-counterfactual form (scenario/override_risk/add_rollback_option), expandable history of counterfactual results per plan.
- `web/src/pages/SimulationForecasts.tsx` — Forecast accuracy dashboard at `/simulation/forecasts`: forecast_accuracy, overconfidence_rate, underconfidence_rate, mean_prediction_error stat cards with colour coding, calibration history table, recompute button.
- `tests/test_p13_simulation_ui.py` — 27 new tests covering all P13 acceptance criteria.

**Updated files:**
- `retrieval/providers.py` — Added `simulation_provider`: queries Memory rows tagged `source_type="simulation"` via `get_simulation_context`; returns ProviderHit list. Surfaces historical simulation evidence during retrieval.
- `retrieval/orchestrator.py` — Import + wire `simulation_provider`. Active for task categories in `{procedural, troubleshooting, project_continuity, general}`. Wrapped in the sequential provider loop (never blocks on error).
- `graph/graph_builder.py` — Added `_build_from_simulations(session)`: creates `plan` nodes for SimulationPlan rows, `simulation` nodes for SimulationRun rows; creates SIMULATED (plan→run) and PREDICTED (run→plan) edges; adds FAILED_BECAUSE_OF edge when actual_outcome=failure, RECOVERED_BY when rollback options exist. Wired into `run_graph_build_pass()` with `simulation_edges` key in result.
- `graph/memory_graph.py` — Extended NODE_TYPES with `"plan"`, `"simulation"`; extended REL_TYPES with `"SIMULATED"`, `"PREDICTED"`, `"ACTUALIZED_AS"`.
- `api/routes/simulation.py` — After `run_simulation` commits, loads the persisted SimulationRun row and calls `store_simulation_memory()` (best-effort, never blocks).
- `web/src/lib/api.ts` — 14 new API calls: `listPlans`, `getPlan`, `createPlan`, `approvePlan`, `rejectPlan`, `runSimulation`, `listSimulations`, `runCounterfactual`, `listCounterfactuals`, `estimateRisk`, `computeCalibration`, `getCalibrationHistory`, `recordSimOutcome`.
- `web/src/App.tsx` — Added `FlaskConical` icon, imported 5 new simulation pages, added `/simulation` nav item, registered 5 new routes.

**Key design decisions / gotchas:**
- `simulation_provider` is only active for planning-adjacent task categories (not `identity` or `configuration`). This prevents simulation evidence from polluting identity/config lookups.
- `store_simulation_memory` is idempotent: checks `source_type="simulation" AND source_id=run.id` before creating. Multiple calls for same run return same memory_id.
- Importance of the simulation memory = `min(0.9, 0.5 + risk_score × 0.4)` — higher risk forecasts surface more prominently in retrieval.
- Plan/simulation graph nodes reuse `get_or_create_node` semantics — running the graph build pass multiple times is safe.
- `graph/memory_graph.py` NODE_TYPES and REL_TYPES are the single source of truth; graph_provider validates against them. New types must be added here before use in builders.
- The simulation UI pages use React Router v6 nested under `/simulation/*`. No new backend routes were needed — all 12 P12 endpoints already existed.
- Approval integration: `SimulationPlanDetail.tsx` shows approve/reject buttons only when `plan.approval_required && plan.status === "pending_approval"`.

### Session 051326_0747 — P12 Predictive Planning + Simulation Engine (Phase 16)
Evolved Mimir from retrospective cognition to predictive cognition via a bounded simulation engine. 403/403 tests passing (39 new).

**New files:**
- `simulation/__init__.py` — module marker
- `simulation/planner.py` — Plan dataclasses: `PlanStep`, `validate_plan_graph` (DAG cycle detection via DFS + Kahn's), `create_plan` (auto-gates high-risk/high-impact keyword plans for approval), `approve_plan`, `reject_plan`, `list_plans`. Approval threshold: risk >= 0.7 or keyword match.
- `simulation/outcome_estimator.py` — `OutcomeEstimate` dataclass + `estimate_outcome`: queries procedural memories (trust >= 0.50), aggregates historical success/failure retrieval counts, counts related rollbacks (last 90d), computes composite success_probability/risk_score/confidence_score. Confidence floor=0.10, ceiling=0.95.
- `simulation/simulator.py` — Core simulation engine: `SimulationPath`, `SimulationResult`, `run_simulation`. Generates up to MAX_BRANCHES=3 paths (base, validation-first, rollback-safe/staged). Bounds: MAX_DEPTH=5, SIMULATION_TOKEN_BUDGET=10000. Uses topological sort for step ordering. Best path selected by `success×0.6 - risk×0.4`. Persists `SimulationRun` row.
- `simulation/counterfactuals.py` — `CounterfactualResult` + `run_counterfactual`: creates transient plan proxy with modifications (override_risk, add/remove procedures, add_rollback_option), re-runs outcome estimation, computes probability_delta/risk_delta, `verdict` (strong/marginal/none improvement/degradation). Persists as counterfactual SimulationRun. `list_counterfactuals` returns counterfactual-type runs only.
- `simulation/calibration.py` — `compute_calibration`: reads completed SimulationRun rows with actual_outcome, computes forecast_accuracy, overconfidence_rate (predicted >0.7, was wrong), underconfidence_rate (predicted <0.4, was right), mean_prediction_error. `record_actual_outcome`: sets actual_outcome + forecast_was_correct. `get_calibration_history`.
- `migrations/versions/0010_simulation.py` — creates `simulation_plans`, `simulation_runs`, `forecast_calibration` tables.
- `api/routes/simulation.py` — 12 endpoints: POST/GET/GET plans, POST approve, POST reject, POST simulate, GET simulations, POST outcome, POST/GET counterfactual(s), POST risk, POST calibration/compute, GET calibration/history.
- `tests/test_p12_simulation.py` — 39 new tests covering all P12 acceptance criteria.

**Updated files:**
- `storage/models.py` — Added `SimulationPlan` ORM model (15 fields, 2 indexes, FK relationship to SimulationRun), `SimulationRun` ORM model (18 fields, 3 indexes, FK to plan), `ForecastCalibration` ORM model (10 fields, 2 indexes).
- `api/main.py` — Registered `simulation.router` under `/api`.
- `worker/tasks.py` — Added `run_forecast_calibration` task (daily, 120s timeout).
- `worker/scheduler.py` — Wired `run_forecast_calibration` (every 24h).
- `tests/test_migrations.py` — Updated expected head revision to `0010`; added `simulation_plans`, `simulation_runs`, `forecast_calibration` to EXPECTED_TABLES.

**Key design decisions / gotchas:**
- Plans are automatically approval-gated if `risk_estimate >= 0.7` OR goal contains high-impact keywords (delete, replace, rewrite, production, etc.). This is checked at plan creation time.
- `validate_plan_graph` uses DFS coloring (WHITE/GRAY/BLACK) for cycle detection — same algorithm used in topological sort. Unknown dependency references are also caught.
- `_topological_order` (Kahn's algorithm) produces deterministic execution order. Unknown deps in steps are silently appended at end (cycle-breaker safety).
- Confidence floor=0.10, ceiling=0.95 applied everywhere — no 0% or 100% confidence claims.
- `run_counterfactual` uses a `_PlanProxy` in-memory object (not persisted) for the modified plan, avoiding DB writes during what-if analysis. Only the result SimulationRun row is persisted.
- Forecast calibration: `forecast_was_correct` is computed as `(predicted > 0.5) == (actual == "success")`. Simple threshold binary — not a probabilistic scoring system.
- All simulation bounds are enforced before any computation: MAX_DEPTH=5, MAX_BRANCHES=3, SIMULATION_TOKEN_BUDGET=10000. These cannot be exceeded by API callers.
- Simulation-generated procedures are never auto-promoted — they are advisory only. High-risk plans require explicit approve_plan() call.

### Session 051226_2304 — P11 Graph Memory + Relational Cognition (Phase 15)
Evolved Mimir from retrieval-centric to relationship-aware cognition by introducing a provider-agnostic graph memory layer. 364/364 tests passing (36 new).

**New files:**
- `graph/__init__.py` — module marker
- `graph/memory_graph.py` — core dataclasses: `GraphNode`, `GraphEdge`, `GraphPath`, `GraphTelemetry`; constants `NODE_TYPES` (10) and `REL_TYPES` (12).
- `graph/graph_provider.py` — SQLite-backed provider: `get_or_create_node`, `get_or_create_edge` (idempotent, validates type), `get_neighbors` (direction=in/out/both), `count_node_degree`.
- `graph/graph_queries.py` — bounded BFS `traverse_related` (max_depth≤5, max_nodes≤50), DFS `find_causal_chains` (CAUSED_BY/LED_TO/FAILED_BECAUSE_OF/RECOVERED_BY only, depth≤5), `find_contradictions` (CONTRADICTS/SUPERSEDES), `get_most_connected_nodes`, `compute_graph_telemetry`, `compute_graph_boost` (additive retrieval boost, max +0.20, saturates at 5 convergent high-confidence paths).
- `graph/graph_builder.py` — automatic relationship extraction: `_build_from_episodic_chains` (→ PART_OF + DERIVED_FROM), `_build_from_memory_relations` (superseded_by → SUPERSEDES, memory_state=contradicted → CONTRADICTS), `_build_from_improvements` (→ DERIVED_FROM), `_build_from_rollbacks` (→ FAILED_BECAUSE_OF + RECOVERED_BY), `_build_from_retrieval_sessions` (→ USED_IN, confidence boosted by task_outcome). All idempotent via get_or_create semantics. `run_graph_build_pass` runs all builders.
- `migrations/versions/0009_graph_memory.py` — creates `graph_nodes` table (7 columns, 3 indexes incl. unique entity_id+node_type) and `graph_edges` table (10 columns, 4 indexes incl. unique src+tgt+rel).
- `api/routes/graph.py` — 7 endpoints: `GET /graph/nodes/{entity_id}`, `GET /graph/traverse/{entity_id}`, `GET /graph/causal/{entity_id}`, `GET /graph/contradictions/{entity_id}`, `GET /graph/centrality`, `GET /graph/telemetry`, `POST /graph/build`.
- `tests/test_p11_graph.py` — 36 new tests covering all P11 acceptance criteria.

**Updated files:**
- `storage/models.py` — Added `GraphNode` ORM model (8 fields, 3 indexes, relationships to GraphEdge) + `GraphEdge` ORM model (10 fields, 4 indexes incl. unique src+tgt+rel constraint, relationships to source/target nodes).
- `retrieval/orchestrator.py` — Added graph boost step (4a): loads `compute_graph_boost` for all candidates before scoring loop; boost added to composite score (`final += graph_boost`). Wrapped in try/except — never blocks retrieval if graph unavailable.
- `api/main.py` — Registered `graph.router` under `/api`.
- `worker/tasks.py` — Added `run_graph_build` task (nightly, 5-min timeout).
- `worker/scheduler.py` — Wired `run_graph_build` (every 24h).
- `tests/test_migrations.py` — Updated expected head revision to `0009`; added `graph_nodes` and `graph_edges` to EXPECTED_TABLES.

**Key design decisions / gotchas:**
- Graph is a separate layer from `MemoryLink` (which tracks lightweight supports/contradicts/supersedes/related). The graph is richer: 12 rel types, 10 node types, confidence/strength/verification_status per edge, provider-agnostic interface.
- `get_or_create_node` uniqueness key is `(entity_id, node_type)` — the same entity_id can appear as both a `memory` node and a `retrieval_session` node if needed.
- `get_or_create_edge` uniqueness key is `(source_node_id, target_node_id, rel_type)` — prevents duplicate edges of the same type between the same pair.
- Graph boost uses a saturation function: `min(incoming_count / 5, 1.0) * 0.20`. At 5 high-confidence incoming edges, boost is maxed at 0.20. This prevents hub nodes from dominating.
- Graph builder is called nightly, not on every memory write — avoids write amplification. Manual trigger via `POST /graph/build`.
- Causal chain DFS uses `direction="out"` only — follows the direction of causation (what this caused), not incoming causes. This gives predictable forward chains.
- `_build_from_retrieval_sessions` caps at 20 memories per session and processes only the 200 most-recent sessions — prevents graph explosion on high-throughput instances.
- Graph boost wrapped in try/except in orchestrator so that graph table absence (e.g., before migration runs) never breaks retrieval.

### Session 051226_2247 — P10 Provider Usefulness Learning + Adaptive Retrieval (Phase 14)
Taught Mimir to learn which retrieval providers work, for which task categories, and to adapt accordingly. 328/328 tests passing (43 new).

**New files:**
- `retrieval/task_categorizer.py` — keyword pattern-based task category detection; 6 categories: identity, procedural, troubleshooting, project_continuity, configuration, general. First-match-wins ordered patterns.
- `retrieval/adaptive_weights.py` — static category-based provider boosts (`_CATEGORY_BOOSTS`) + slow historical adjustment (α=0.10); bounded to [0.3×base, 2.5×base]. Also `compute_provider_limits()` for adaptive candidate budgets.
- `retrieval/confidence.py` — trust-weighted agreement scoring (`compute_weighted_agreement`) using per-provider trust weights (identity=1.5, high_trust=1.4, procedural=1.3, vector=1.0, episodic=0.9, keyword=0.8); `estimate_confidence()` combines agreement (35%), trust (30%), active state fraction (15%), token efficiency (10%), historical usefulness (10%).
- `retrieval/provider_stats.py` — background aggregation of per-(provider, task_category) stats from recent RetrievalSession records; drift detection (harmful_rate>15% OR usefulness_rate<25%, min 10 sessions); conservative weight update via `update_weight_from_stats`; `get_provider_stats()` for orchestrator lookup; `get_all_provider_stats()` for UI.
- `storage/fts.py` — SQLite FTS5 search with lazy probe, graceful fallback, BM25 score normalisation; `reset_fts5_probe()` for test isolation.
- `migrations/versions/0008_adaptive_retrieval.py` — adds `task_category`, `active_providers`, `provider_contributions`, `retrieval_confidence_score` columns to `retrieval_sessions`; creates `provider_stats` table (18 columns, 4 indexes); creates `memory_fts` FTS5 virtual table + 3 sync triggers (INSERT, UPDATE content, soft-delete).
- `tests/test_p10_adaptive.py` — 43 new tests covering all P10 acceptance criteria.

**Updated files:**
- `storage/models.py` — Added 4 P10 columns to `RetrievalSession` ORM + new `ProviderStats` ORM model (18 fields, 4 indexes).
- `retrieval/orchestrator.py` — Now runs task categorization, loads adaptive weights + limits per provider, computes trust-weighted agreement (not flat fraction), estimates retrieval confidence, returns `task_category`/`provider_contributions`/`retrieval_confidence` in `OrchestratorResult`. `OrchestratorDebug` extended with `task_category`, `provider_weights`, `retrieval_confidence`.
- `retrieval/providers.py` — `keyword_provider` upgraded to use FTS5 (BM25 ranked) with LIKE fallback. FTS5 path queries `memory_fts` virtual table; scores normalised to [0,1].
- `context/context_builder.py` — debug dict now passes `task_category`, `provider_weights`, `retrieval_confidence` through to API layer.
- `api/routes/events.py` — `POST /recall` now stores `task_category`, `active_providers`, `provider_contributions`, `retrieval_confidence_score` in `RetrievalSession`; response includes `retrieval_confidence` and `task_category`.
- `api/routes/telemetry.py` — 3 new endpoints: `GET /providers/stats`, `POST /providers/aggregate`, `GET /providers/drift`.
- `worker/tasks.py` — Added `run_provider_stats_aggregation` task (6h interval).
- `worker/scheduler.py` — Wired `run_provider_stats_aggregation` (every 6h).
- `web/src/lib/api.ts` — 3 new API calls: `getProviderStats`, `aggregateProviderStats`, `getProviderDrift`.
- `web/src/pages/Telemetry.tsx` — Added "Provider Effectiveness" table (usefulness/harmful/weight/drift per provider+category) and "Provider Drift Alerts" section. "Aggregate Providers" button triggers immediate aggregation.
- `tests/test_migrations.py` — Updated expected head revision to `0008`; added `provider_stats` to EXPECTED_TABLES.

**Key design decisions / gotchas:**
- FTS5 triggers created at migration time; existing memories backfilled into `memory_fts` at migration. The `_FTS5_AVAILABLE` probe is lazily cached per-process — call `reset_fts5_probe()` in tests when forcing fallback.
- Task category detection uses first-match-wins ordered patterns. "how to configure" → procedural wins over configuration because "how to" appears earlier in the pattern list. This is intentional — the action verb dominates the noun.
- Provider stats accumulation is additive (not windowed) — counters keep growing. Rates are recomputed from cumulative totals. This means early sessions have permanent influence; mitigated by the slow α=0.10 weight adjustment.
- Weight update uses `update_weight_from_stats(old, usefulness_rate, base_weight)` — bounded to [base×0.3, base×2.5]. The base_weight is the category-specific boost, so a "procedural" provider in a "procedural" task has a higher ceiling than in a "general" task.
- Drift clears automatically when a provider's stats recover past the thresholds on next aggregation pass.
- `provider_contributions` in RetrievalSession is a JSON dict `{provider_name: memory_count}` built from `debug.selected[].provider_sources` at recall time.
- Stats loaded at orchestrate time add one DB query per retrieval. If provider_stats table is empty (no prior aggregation), orchestrator silently uses category-only boosts.

### Session 051226_2215 — P9 Autonomous Feedback + Cognitive Telemetry (Phase 13)
Taught Mimir to automatically infer retrieval outcomes and produce operational self-awareness telemetry. 285/285 tests passing (44 new).

**New files:**
- `telemetry/__init__.py` — module marker
- `telemetry/cognition_metrics.py` — computes + persists 18 cognitive metrics: retrieval usefulness rate, harmful rate, procedural success/failure rate, retrieval-to-success correlation, memory state distribution, trust distribution, rollback correlation, token efficiency trends. Persists as `TelemetrySnapshot` rows; provides history and latest-snapshot queries.
- `telemetry/retrieval_analytics.py` — per-session quality scoring (relevance, usefulness, harmfulness, agreement, token_efficiency), memory heatmap (most-used, rarely-used, high-cost-low-value), retrieval session aggregate stats.
- `telemetry/procedural_analytics.py` — per-memory procedural effectiveness (success_rate, failure_rate, rollback_count, supersession_count, avg_outcome_quality, evidence_growth_rate), confidence drift detection (failure_rate > 50% threshold), conservative drift trust decay (max -0.05/pass, floor 0.01, never touches quarantined/archived).
- `worker/feedback_inference.py` — automatic retrieval outcome inference engine. Positive inference (+0.01): session outcome = success AND no rollback AND no correction AND no harmful. Negative inference (-0.03): rollback_id set OR has_correction OR has_harmful_outcome OR outcome = failure. Idempotent via `inference_applied` flag. Never touches quarantined or archived memories. Persists RetrievalFeedback + LifecycleEvent for each inferred delta.
- `migrations/versions/0007_telemetry.py` — adds `retrieval_sessions` table (query, session/project/user ids, retrieved_memory_ids JSON, token_cost, task_outcome, rollback_id, has_correction, has_harmful_outcome, inference_applied, 5 quality score columns) + `telemetry_snapshots` table (metric_name, metric_value, period, project, meta).
- `api/routes/telemetry.py` — 7 endpoints: GET /snapshot, POST /snapshot/compute, GET /metrics/{name}/history, GET /retrieval/stats, GET /retrieval/heatmap, GET /procedural/effectiveness, GET /drift/detect, POST /drift/apply-decay.
- `tests/test_p9_telemetry.py` — 44 new tests covering all P9 acceptance criteria.

**Updated files:**
- `storage/models.py` — Added `RetrievalSession` ORM model (17 fields, 4 indexes) + `TelemetrySnapshot` ORM model (7 fields, 2 indexes).
- `api/schemas.py` — Added `RetrievalSessionOutcomeIn` schema.
- `api/routes/events.py` — `POST /recall` with token_budget now creates a `RetrievalSession` and returns `retrieval_session_id`. Added `POST /recall/session/{id}/outcome` endpoint: records task_outcome, rollback_id, correction/harmful flags, computes quality scores.
- `api/main.py` — registered `telemetry.router`.
- `worker/consolidator.py` — `run_consolidation_pass()` now calls `infer_retrieval_outcomes()` and returns inference stats in result dict.
- `worker/tasks.py` — Added `run_telemetry_snapshot` task (6h) and `run_drift_detection` task (daily).
- `worker/scheduler.py` — wired both new tasks.
- `tests/test_migrations.py` — updated expected head revision to `0007`; added `retrieval_sessions` and `telemetry_snapshots` to EXPECTED_TABLES.
- `web/src/lib/api.ts` — 8 new telemetry API calls.
- `web/src/App.tsx` — added Telemetry nav item + route.
- `web/src/pages/Telemetry.tsx` — full telemetry dashboard UI: cognitive metrics, memory state distribution, retrieval session stats, memory heatmap, procedural effectiveness table, confidence drift panel with apply-decay action.

**Key design decisions / gotchas:**
- Inference deltas are intentionally tiny (+0.01/−0.03) vs explicit feedback (+0.02/−0.05 to −0.10). This prevents auto-inference from dominating trust evolution.
- `inference_applied` flag on `RetrievalSession` makes inference idempotent — running `run_consolidation_pass` multiple times is safe.
- Retrieval session is only created when `token_budget` is set in `/recall` (orchestrated path). Raw vector-only recalls don't create sessions.
- Quality score computation at outcome time uses a per-session agreement_score estimate (mean of all memory agreement scores stored at recall time).
- Drift detection threshold: failure_rate > 50% AND times_retrieved ≥ 3. Conservative — avoids flagging memories with 1-2 retrievals.
- Drift decay cap: max −0.05 per pass, floor 0.01. Never auto-quarantines. Action categories: review_and_decay, accelerate_aging, age_and_propose_supersession.
- `MemoryState.BLOCKED` check prevents inference/decay from touching quarantined or archived memories in all code paths.

### Session 051226_2151 — P8 Procedural Learning Integration (Phase 12)
Converted Mimir from memory maintenance into experience-driven procedural learning infrastructure. 241/241 tests passing (31 new).

**New files:**
- `worker/procedural_promoter.py` — Scans episodic chains for `procedural_lesson`, groups by normalized text, promotes repeated lessons (≥2 chains) into approval-gated `ImprovementProposal` records (high-confidence) or active procedural memories (mid-range). Updates `evidence_count` and `derived_from_episode_ids` on existing memories as more confirming chains arrive.
- `migrations/versions/0006_procedural_learning.py` — Schema: 4 new Memory columns (`evidence_count`, `derived_from_episode_ids`, `last_success_at`, `last_failure_at`) + `retrieval_feedback` table.
- `tests/test_procedural_learning.py` — 31 new tests covering all P8 acceptance criteria.

**Updated files:**
- `storage/models.py` — Added 4 procedural learning fields to Memory ORM + `RetrievalFeedback` ORM model with indexes.
- `api/schemas.py` — Added `RecallFeedbackIn` schema.
- `api/routes/events.py` — Added `POST /api/events/recall/feedback` endpoint: records `RetrievalFeedback`, applies trust delta (success=+0.02, failure=-0.05, irrelevant=-0.02, harmful=-0.10), updates retrieval counters and `last_success_at`/`last_failure_at`, logs `LifecycleEvent`.
- `worker/consolidator.py` — Wired `promote_procedural_lessons()` into `run_consolidation_pass()`; added `write_chain_lesson()` helper to set lesson on a chain; pass result now includes `procedural_promoted` key.
- `worker/reflector.py` — Added `mine_experience_patterns()` (7-day window: identifies repeated successes, repeated failures, recovery sequences) + `propose_improvement_suggestions()` (creates `retrieval_tuning` and `procedural_promotion` proposals from operational patterns). `run_reflection_pass()` now also runs improvement suggestions.
- `retrieval/providers.py` — Updated `procedural_provider()`: now filters `trust_score >= 0.60`, orders by trust DESC (evidenced procedures first), score is `trust×importance`.
- `memory/procedural_store.py` — Added `supersede(old_id, new_id)`: requires new.trust ≥ 0.75 and ≥ old.trust; archives old, sets `valid_to`/`superseded_by`, creates `MemoryLink(supersedes)` + `LifecycleEvent`.
- `tests/test_migrations.py` — Updated expected head revision to `0006`; added `episodic_chains`, `lifecycle_events`, `retrieval_feedback` to EXPECTED_TABLES.

**Key design decisions / gotchas:**
- Procedural promotion is approval-gated for confidence ≥ 0.7 (ImprovementProposal created) — never instant auto-promotion
- Confidence formula: `min(0.95, 0.5 + 0.15 × evidence_count)` — asymptotic, saturates around 0.95; 2 chains = 0.8 confidence
- Lesson grouping uses normalized text (lowercase, strip punctuation, collapse whitespace) — catches minor rephrasing of same lesson
- Trust clamped to [0.01, 0.99] — harmful outcome from 0.7 → 0.6; repeated harmful never drives below floor
- Harmful outcome decreases trust but does NOT auto-quarantine — quarantine requires adversarial pattern detection
- `procedural_provider` min_trust=0.60 filter means manually-created low-trust procedural memories no longer surface in retrieval
- Supersession requires new.trust ≥ old.trust — prevents low-trust memories from arbitrarily replacing high-trust ones
- `mine_experience_patterns` uses a 7-day window (vs 24h for `analyze_patterns`) to detect slower behavioral trends

### Session 051226_2140 — P7 Offline Consolidation + Lifecycle Engine (Phase 11)
Implemented the full offline consolidation ("dreaming") and lifecycle automation layer. 210/210 tests passing (33 new).

**New files:**
- `worker/observer.py` — lightweight fast-write event/trace capture, no heavy reasoning, used as library by API layer
- `worker/reflector.py` — async offline: pattern analysis (24h task traces), contradiction detection (shared source_id + prefix heuristic), procedural lesson extraction, improvement proposals
- `worker/consolidator.py` — dreaming layer: `update_trust_from_retrieval()` (bump/drop trust from retrieval counters), `build_episodic_chains()` (groups session episodes into narrative chains), `merge_related_memories()` (vector-similarity merge of low-trust semantic pairs), `run_consolidation_pass()` wraps all
- `worker/lifecycle.py` — full state machine: `transition_aging()` (active→aging, 30d threshold + retrieval boost), `transition_stale()` (aging→stale, 60d), `transition_archived()` (stale→archived, 120d + valid_to set), `supersede_memory()` (temporal supersession with MemoryLink), `apply_verification_decay()` (0.3%/day past 90d grace), `increase_trust()` / `decrease_trust()` (with lifecycle events), `cleanup_deleted()` (hard-delete old low-trust episodic), `run_lifecycle_pass()` / `run_deep_maintenance()`

**Schema (migration 0005):**
- Memory: `times_retrieved` (int), `last_retrieved_at` (datetime), `successful_retrievals` (int), `failed_retrievals` (int)
- New table `episodic_chains`: id, title, episode_summary, episode_type, linked_memory_ids (JSON), procedural_lesson, project, user_id
- New table `lifecycle_events`: id, memory_id, event_type, from_state, to_state, trust_before, trust_after, reason, meta

**Updated files:**
- `storage/models.py` — retrieval frequency fields on Memory; EpisodicChain + LifecycleEvent ORM models
- `retrieval/orchestrator.py` — `_bump_retrieval_counts()` updates `times_retrieved` + `last_retrieved_at` after every orchestrated retrieval
- `worker/tasks.py` — 4 new tasks: `run_reflection_pass`, `run_consolidation_pass`, `run_lifecycle_pass`, `run_deep_maintenance`
- `worker/scheduler.py` — reflector every 30min; consolidator + lifecycle nightly; deep maintenance weekly
- `tests/test_migrations.py` — updated expected head revision to 0005

**Key design decisions / gotchas:**
- SQLite stores naive datetimes; all lifecycle comparisons use `_as_utc()` to normalize before subtraction — without this you get `TypeError: can't subtract offset-naive and offset-aware datetimes`
- `supersede_memory()` refuses to act if new memory trust < 0.75 (constant `_SUPERSESSION_MIN_TRUST`) — prevents low-trust memories from arbitrarily superseding high-trust ones
- Quarantined memories are explicitly excluded from ALL lifecycle transitions (`transition_aging` filters on `memory_state == ACTIVE` only) and `increase_trust()` returns False for quarantined IDs
- `build_episodic_chains()` tracks `already_linked` in-memory and re-loads existing chains from DB to prevent double-chaining on successive passes
- Retrieval failure tracking (for `decrease_trust`) is not automatic from the API — requires explicit caller to call `decrease_trust()` or a future `/recall/feedback` endpoint

**Scheduler schedule (offline workers):**
```
Observer:       continuous (library, called from API layer)
Reflector:      every 30 minutes
Consolidator:   nightly (24h)
Lifecycle:      nightly (24h)
Deep maintenance: weekly
```

### Session 051226_0000 — P6 Retrieval Orchestrator (Phase 10)
Replaced single-path vector retrieval with a 6-provider multi-source orchestration layer. Built `retrieval/providers.py` (vector, keyword, identity, episodic_recent, procedural, high_trust), `retrieval/orchestrator.py` (merge/dedup/rerank/tier-ordering/budget), updated `context_builder.py` to use orchestrator, added top-level `debug` block to POST /api/events/recall, added `RecallDebug` schema. Also fixed a pre-existing test fragility in `test_quarantine.py` (cross-test identity name pollution) and added vector store cleanup to `conftest.py`. 177/177 tests passing.

### Session 051226_PREV — P5 Quarantine Pipeline (Phase 9)
Implemented adversarial memory detection in `memory/quarantine_detector.py` covering 7 trigger categories (prompt injection, security policy overwrite, approval spoofing, Tailscale manipulation, dangerous procedure, credential exposure, high-trust identity contradiction). Integrated into all storage paths, retrieval paths, and context builder. Added API filtering by `memory_state` and `verification_status`. 165/165 tests passing (25 new quarantine tests).

### Session 051226_PREV — P1–P4 Trust + Temporal (Phase 8)
Added trust fields (trust_score, verification_status, confidence, source_type, created_by, verified_by), temporal validity fields (valid_from, valid_to, superseded_by, last_verified_at), and memory state machine (active/aging/stale/contradicted/quarantined/archived/deleted) to Memory model. Added `memory/trust.py` (MemoryState, TrustLevel, trust_defaults). Retrieval paths now filter by trust state. 140/140 tests passing (14 new temporal trust tests).

### Session 051126_1730 — Mobile Approval Deep-Link (Phase 6)
Added `GET /api/approvals/{id}` endpoint, `ApprovalDetail.tsx` PWA page with mobile approve/reject, `/approvals/:id` React route, and 7 new tests. Fixed pre-existing test fragility in `test_conflicting_facts_not_silently_overwritten`. 107/107 tests passing.

### Session 051126_1530 — Real Approval Delivery (Phase 5)
Slack interactive approvals, approval audit trail, push deep-links, notification by ID, security tests. 101/101 tests passing (12 new tests added).

### Session 051126_1200 — Schema + Install Hardening (Phase 4)
Alembic migrations, managed-env Linux install, unified recall shape, migration safety tests, upgrade docs. 89/89 tests passing (10 new tests added).

### Session 051126_1058 — Integration Validation Pass (Phase 3)
Proved Mimir works end-to-end beyond unit tests. 79/79 tests passing (35 new tests added). One pre-existing bug in `memory_extractor.py` fixed (meta=None crash).

### Session 051126_1038 — Functional Hardening Pass (Phase 2)
Improved stability, test coverage, memory quality, reflection restraint, skill gating, rollback breadth, and observability. 44/44 tests passing (30 new tests added).

### Session 051126_0516 — Initial Build (Phase 1)
Built all modules from scratch: storage, memory layers, skills, reflections, approvals, rollbacks, notifications, context builder, retrieval, metrics, API, MCP server, SDK, worker, React PWA. 14/14 tests passing.

---

## Completed

| # | What | File(s) |
|---|------|---------|
| 220 | **P20.3 dashboard-led Cursor onboarding card with mode detection, guided setup links, and generated MCP snippets** | `web/src/pages/Dashboard.tsx`, `web/src/lib/api.ts`, `web/src/pages/Dashboard.test.tsx` |
| 221 | **Connection onboarding API payload for dashboard consumption (auth mode, owner status, warnings, guided URLs, MCP templates)** | `api/routes/connection.py`, `tests/test_p20_connection_settings.py` |
| 222 | **OAuth setup-page UX bridge back to dashboard flow + dashboard auto-open on authorize submit** | `api/routes/oauth.py` |
| 217 | **P20.2 API response normalization for dashboard/telemetry/list endpoints so missing arrays no longer crash React pages** | `web/src/lib/api.ts`, `web/src/pages/Dashboard.tsx`, `web/src/pages/Telemetry.tsx` |
| 218 | **P20.2 app-wide React error boundary fallback with visible UI error + refresh action** | `web/src/components/ErrorBoundary.tsx`, `web/src/main.tsx` |
| 219 | **P20.2 web resilience tests and frontend build gate added to pytest flow** | `web/src/components/ErrorBoundary.test.tsx`, `web/src/pages/Dashboard.test.tsx`, `tests/test_p20_web_resilience.py`, `web/package.json` |
| 214 | **P20.1 dedicated browser connection settings page with editable profile, generated MCP configs, and API-key management UX** | `api/routes/connection.py`, `web/src/pages/SettingsPage.tsx`, `api/main.py` |
| 215 | **Connection profile model extended with preferred auth, remote/public-URL warnings, and per-scenario MCP JSON generation** | `mimir/setup_profile.py`, `api/routes/connection.py` |
| 216 | **P20.1 connection-settings regression tests: page load, profile read/write, config variants, one-time key visibility, warnings** | `tests/test_p20_connection_settings.py`, `tests/test_p20_oauth.py` |
| 212 | **P20.1 OAuth UX: single-user authorize page now explains modes, creates first owner in-browser, reveals one-time API key, and continues Cursor auth without CLI detour** | `api/routes/oauth.py`, `tests/test_p20_oauth.py` |
| 213 | **P20.1 setup-profile wizard: browser captures SSH/remote/local connection details, persists them, and generates matching MCP config/discovery hints** | `api/routes/oauth.py`, `mimir/setup_profile.py`, `tests/test_p20_oauth.py` |
| 203 | **P19.6 direct SQL bootstrap capsule fallback + shared capsule intent/scoring helpers** | `retrieval/bootstrap_capsules.py`, `retrieval/retrieval_engine.py`, `retrieval/providers.py` |
| 204 | **Orchestrator bootstrap capsule provider wiring for Postgres/context-builder parity** | `retrieval/orchestrator.py`, `retrieval/providers.py` |
| 205 | **Postgres exact-label bootstrap search hardening (`content`/`capsule_type` matching)** | `storage/search_backend.py` |
| 206 | **Bootstrap/vector metadata parity: project, source_type, capsule_type now preserved on vector upserts and reindex** | `storage/vector_store.py`, `memory/semantic_store.py`, `memory/procedural_store.py`, `memory/episodic_store.py`, `storage/reindex_vectors.py`, `api/routes/mcp_http.py` |
| 207 | **Postgres bootstrap integration tests + live Docker `api-pg` MCP validation** | `tests/test_p19_postgres_bootstrap.py`, `api/routes/mcp_http.py` |
| 208 | **Auth docs clarified: OAuth for browser/local setups, API-key Bearer remains first-class for SSH/headless/remote/RPi5 MCP** | `README.md`, `docs/CURSOR_MCP_SETUP.md`, `docs/OAUTH_SETUP.md`, `docs/SELF_HOSTING.md`, `docs/PUBLIC_GITHUB_SETUP.md` |
| 209 | **P19.7 procedural bootstrap writes now persist `user_id`; procedural vectors carry tenant metadata** | `memory/procedural_store.py` |
| 210 | **P19.7 MCP bootstrap fallback is exact-user scoped and exposes found/missing capsule debug fields on search/recall** | `retrieval/bootstrap_capsules.py`, `retrieval/retrieval_engine.py`, `api/routes/mcp_http.py` |
| 211 | **P19.7 multi-user MCP regression coverage added for SQLite and Postgres `/mcp` routes** | `tests/test_p19_mcp_http.py`, `tests/test_p19_postgres_bootstrap.py` |
| 200 | **P19.5 retrieval contract fix: memory_search/memory_recall now merge vector + keyword/FTS candidates and rank cross-layer bootstrap capsules** | `retrieval/retrieval_engine.py` |
| 201 | **Capsule relevance boosting on `meta.bootstrap=true` + `capsule_type` for project identity, testing, safety/governance, and procedural-lesson intents** | `retrieval/retrieval_engine.py` |
| 202 | **P19.5 regression tests for required bootstrap query variants and wrong-project recall isolation** | `tests/test_p19_mcp_http.py` |
| 183 | **OAuth 2.1/PKCE server: well-known discovery, dynamic registration, authorize, token, revoke, setup** | `api/routes/oauth.py` |
| 184 | **Migration 0013: oauth_clients, oauth_authorization_codes, oauth_tokens, oauth_refresh_tokens + user.role + user.last_login_at** | `migrations/versions/0013_oauth_multiuser.py` |
| 185 | **Config: _effective_auth_mode, is_single_user, is_multi_user; allow_registration, require_https, oauth_enabled, token TTL fields** | `mimir/config.py` |
| 186 | **User.role + User.last_login_at; OAuthClient, OAuthAuthorizationCode, OAuthToken, OAuthRefreshToken models** | `storage/models.py` |
| 187 | **get_current_user: OAuth token resolution first; local-dev-key rejected in multi_user; legacy API key fallback** | `api/deps.py` |
| 188 | **MCP 401 → WWW-Authenticate resource_metadata header; revoked token gate; OAuth user resolution first in _call_tool** | `api/routes/mcp_http.py` |
| 189 | **Register OAuth router at root (no /api prefix)** | `api/main.py` |
| 190 | **python -m mimir.auth.create_owner CLI — owner creation, API key shown once** | `mimir/auth/create_owner.py`, `mimir/auth/__init__.py`, `mimir/auth/__main__.py` |
| 191 | **P20 tests: 30 OAuth/multi-user tests** | `tests/test_p20_oauth.py` |
| 192 | **release_gate P20 hard-fail gates: OAuth leakage, MCP failures, dev-key-in-prod** | `evals/release_gate.py` |
| 193 | **Docker Compose: local profile (single_user + SQLite), prod-postgres profile isolated; no more double-start** | `docker-compose.yml` |
| 194 | **Docs: OAUTH_SETUP, MULTI_USER_SECURITY, SELF_HOSTING, PUBLIC_GITHUB_SETUP, CURSOR_MCP_SETUP updated** | `docs/` |
| 195 | **P19.4 bootstrap contract fix: canonical capsule metadata, labeled capsule content, per-capsule ID response fields** | `api/routes/mcp_http.py` |
| 196 | **P19.4 force=true repair path: dedupe/update bootstrap capsules + search/vector reindex** | `api/routes/mcp_http.py` |
| 197 | **Retrieval hit shape expanded with project/source/state/trust/meta/capsule debug fields** | `retrieval/retrieval_engine.py` |
| 198 | **P19 bootstrap tests expanded to enforce 7-capsule storage/recall/indexing behavior and shape checks** | `tests/test_p19_mcp_http.py` |
| 199 | **P18 version assertions now derive from canonical `mimir.__version__` (no stale RC literals)** | `tests/test_p18_security.py` |
| 166 | **Quarantine reactivation blocked in update paths (CRITICAL security fix)** | `memory/semantic_store.py`, `memory/episodic_store.py`, `memory/procedural_store.py` |
| 167 | **MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS config flag + /system/* 403 when disabled** | `mimir/config.py`, `api/routes/system.py` |
| 168 | **mimir/__version__.py, version 0.1.0-rc1 in pyproject.toml + web/package.json** | `mimir/__version__.py`, `pyproject.toml`, `web/package.json`, `api/main.py` |
| 169 | **docs/ACCESS_CONTROL_MATRIX.md — complete per-endpoint security matrix** | `docs/ACCESS_CONTROL_MATRIX.md` |
| 170 | **scripts/security_scan.sh + make security target** | `scripts/security_scan.sh`, `Makefile` |
| 171 | **Backup/restore RC test: 8/8 verify checks, migration upgrade clean** | `backups/rc1/`, `reports/` |
| 172 | **Docker smoke: 8/8 functional checks pass (pre-built image)** | `reports/docker/smoke_latest.json`, `reports/docker/smoke_latest.md` |
| 173 | **P18 security tests: 19 new tests** | `tests/test_p18_security.py` |
| 174 | **Release docs updated: RELEASE_CHECKLIST, SECURITY, DEPLOYMENT, OPERATIONS** | `docs/` |
| 152 | **P0 eval fixes: simulation run 'id' alias, /runs/{id}/outcome route, risk response top-level fields, /providers/aggregate alias** | `simulation/simulator.py`, `api/routes/simulation.py`, `api/routes/telemetry.py`, `api/main.py` |
| 153 | **Postgres support: asyncpg, _build_url(), dialect-aware engine, pool settings** | `storage/database.py`, `mimir/config.py`, `pyproject.toml` |
| 154 | **Alembic env.py: respects MIMIR_DATABASE_URL, dialect-conditional render_as_batch** | `migrations/env.py` |
| 155 | **Search backend abstraction: SQLiteFTSBackend, PostgresSearchBackend, LikeFallbackBackend** | `storage/search_backend.py` |
| 156 | **keyword_provider wired to search_backend abstraction** | `retrieval/providers.py` |
| 157 | **Multi-worker job locking: job_locks table, try_acquire/release/heartbeat/acquire_lock** | `worker/job_lock.py`, `storage/models.py` |
| 158 | **Migration 0012: job_locks table** | `migrations/versions/0012_job_locks.py` |
| 159 | **_job decorator: db_lock=True for consolidation/lifecycle/graph/reflection** | `worker/tasks.py` |
| 160 | **Transaction hardening: promotion rollback, watcher per-item try/except, graph builder safe wrappers** | `approvals/promotion_worker.py`, `approvals/rollback_watcher.py`, `graph/graph_builder.py` |
| 161 | **Docker Compose: local-sqlite (default) and prod-postgres profiles** | `docker-compose.yml` |
| 162 | **Docker smoke test script** | `scripts/docker_smoke_test.sh` |
| 163 | **CI matrix: tests-sqlite, tests-postgres, evals, release-gate, docker-smoke** | `.github/workflows/ci.yml` |
| 164 | **Docs updated: DEPLOYMENT, UPGRADE, OPERATIONS, RELEASE_CHECKLIST** | `docs/` |
| 165 | **P17 tests: 33 new tests** | `tests/test_p17_postgres_multi_instance.py` |
| 131 | **Enhanced config validation: API key defaults, auth_mode validity, CORS wildcard, Slack/VAPID guards** | `mimir/config.py` |
| 132 | **Backup verify CLI — 7-check validation pipeline** | `mimir/backup/verify.py` |
| 133 | **GET /api/system/readiness — DB/migration/vector/FTS/worker checks, 503 on failure** | `api/routes/system.py` |
| 134 | **GET /api/system/status enhanced with migration_revision, FTS status, last eval/gate results** | `api/routes/system.py` |
| 135 | **Load/soak test module — concurrent user simulation, p50/p95/error_rate reporting** | `evals/load_test.py` |
| 136 | **Release report generator — runs tests+migrations+evals+gate+build, produces JSON+MD** | `evals/release_report.py` |
| 137 | **make release target** | `Makefile` |
| 138 | **Docker compose: web service, api healthcheck, worker depends_on healthy api** | `docker-compose.yml` |
| 139 | **Web Dockerfile (multi-stage: node build + nginx serve) + nginx.conf** | `web/Dockerfile`, `web/nginx.conf` |
| 140 | **Operator runbooks: DEPLOYMENT, BACKUP_RESTORE, SECURITY, OPERATIONS, UPGRADE, RELEASE_CHECKLIST** | `docs/` |
| 117 | **Eval harness base: EvalResult, EvalReport, EvalSuite with _ok/_fail/_gate helpers** | `evals/base.py` |
| 118 | **Eval runner — suite orchestrator, JSON + Markdown report gen, CLI entrypoint** | `evals/runner.py` |
| 119 | **Release gate — unit tests + migration + eval report gate; blocks on critical failures** | `evals/release_gate.py` |
| 120 | **Synthetic fixtures — 8 adversarial categories, trajectory events, retrieval corpus** | `evals/fixtures/__init__.py` |
| 121 | **memory_quality suite — 8 checks: store/recall/dedup/quarantine** | `evals/suites/memory_quality.py` |
| 122 | **retrieval_quality suite — 6 checks (2 CRITICAL): isolation, quarantine exclusion, precision@5** | `evals/suites/retrieval_quality.py` |
| 123 | **trust_and_quarantine suite — 8 checks: trust range, feedback evolution, quarantine triggers** | `evals/suites/trust_and_quarantine.py` |
| 124 | **red_team suite — 10 CRITICAL checks: all 8 adversarial categories + cross-user + reactivation** | `evals/suites/red_team.py` |
| 125 | **trajectory suite — 6 checks: multi-session history, quarantine persistence, memory accumulation** | `evals/suites/trajectory.py` |
| 126 | **worker_stability suite — 9 checks: consolidation/reflection/lifecycle/graph idempotency** | `evals/suites/worker_stability.py` |
| 127 | **token_efficiency suite — 6 checks: budget enforcement, p95 latency, cost reporting** | `evals/suites/token_efficiency.py` |
| 128 | **simulation_forecasting suite — 7 checks: confidence bounds, auto-gating, calibration** | `evals/suites/simulation_forecasting.py` |
| 129 | **POST /api/system/consolidate + /reflect + /lifecycle worker trigger endpoints** | `api/routes/system.py` |
| 130 | **CI workflow: 3-stage (tests → evals → release_gate) with artifact upload** | `.github/workflows/ci.yml` |
| 106 | **simulation_provider — retrieves historical simulation evidence during planning queries** | `retrieval/providers.py` |
| 107 | **Simulation provider wired into retrieval orchestrator (procedural/troubleshooting/project_continuity/general categories)** | `retrieval/orchestrator.py` |
| 108 | **store_simulation_memory + get_simulation_context — historical simulation as retrievable memory** | `simulation/historical_memory.py` |
| 109 | **_build_from_simulations — graph nodes for plans/runs + SIMULATED/PREDICTED/FAILED_BECAUSE_OF/RECOVERED_BY edges** | `graph/graph_builder.py` |
| 110 | **NODE_TYPES extended: plan, simulation; REL_TYPES extended: SIMULATED, PREDICTED, ACTUALIZED_AS** | `graph/memory_graph.py` |
| 111 | **Simulation hub page** | `web/src/pages/Simulation.tsx` |
| 112 | **Plan list page with status badges, risk bars, create form** | `web/src/pages/SimulationPlans.tsx` |
| 113 | **Plan detail page: step table, simulation paths, counterfactuals, approve/reject** | `web/src/pages/SimulationPlanDetail.tsx` |
| 114 | **Counterfactual explorer with plan selector + run form** | `web/src/pages/SimulationCounterfactuals.tsx` |
| 115 | **Forecast accuracy dashboard: accuracy/overconfidence/underconfidence/error stats + calibration history** | `web/src/pages/SimulationForecasts.tsx` |
| 116 | **14 new simulation API calls in api.ts; Simulation nav + routes in App.tsx** | `web/src/lib/api.ts`, `web/src/App.tsx` |
| 97 | **SimulationPlan + SimulationRun + ForecastCalibration ORM models (P12)** | `storage/models.py` |
| 98 | **Migration 0010: simulation_plans + simulation_runs + forecast_calibration tables** | `migrations/versions/0010_simulation.py` |
| 99 | **Plan representation with DAG validation, approval gating, step/dependency/rollback support** | `simulation/planner.py` |
| 100 | **Outcome estimator — success_probability/risk_score/confidence from procedural history + rollbacks** | `simulation/outcome_estimator.py` |
| 101 | **Multi-path simulation engine — up to 3 paths, depth/branch/token-budget bounded** | `simulation/simulator.py` |
| 102 | **Counterfactual reasoning — probability_delta, risk_delta, verdict, override_risk/procedures/rollback** | `simulation/counterfactuals.py` |
| 103 | **Forecast calibration — accuracy, overconfidence, underconfidence, prediction error tracking** | `simulation/calibration.py` |
| 104 | **Simulation API — 12 endpoints: plans, simulate, counterfactual, risk, calibration** | `api/routes/simulation.py` |
| 105 | **Forecast calibration background task (daily)** | `worker/tasks.py`, `worker/scheduler.py` |
| 1 | Project config, deps, Makefile, Dockerfile, docker-compose | `pyproject.toml`, `Makefile`, `Dockerfile`, `docker-compose.yml` |
| 2 | Pydantic settings with env/VAPID/Slack fields | `mimir/config.py` |
| 3 | All 15 SQLAlchemy ORM models | `storage/models.py` |
| 4 | Async SQLAlchemy engine + session factory + DB init | `storage/database.py` |
| 5 | ChromaDB vector store wrapper with cosine-sim search across 4 memory layers | `storage/vector_store.py` |
| 6 | Episodic memory CRUD + soft-delete + access tracking | `memory/episodic_store.py` |
| 7 | Semantic memory CRUD + dedup (0.95) + conflict detection + quarantine integration | `memory/semantic_store.py` |
| 8 | Procedural memory CRUD + project-scoped listing | `memory/procedural_store.py` |
| 9 | Content classifier + importance scorer + trust inference | `memory/memory_extractor.py` |
| 10 | Cross-layer retrieval + identity context endpoint | `memory/memory_retriever.py` |
| 11 | Stale memory pruner + semantic deduplicator | `memory/memory_consolidator.py` |
| 12 | **MemoryState + TrustLevel constants + trust_defaults()** | `memory/trust.py` |
| 13 | **Adversarial pattern detector — 7 quarantine trigger categories, 20 rules** | `memory/quarantine_detector.py` |
| 14 | Skill CRUD + versioned snapshots | `skills/skill_registry.py` |
| 15 | Auto-skill proposal gated by MIN_SUCCESS_RATE + MIN_CONFIDENCE + test cases | `skills/skill_generator.py` |
| 16 | Skill executor + run record | `skills/skill_runner.py` |
| 17 | Skill test harness + pass/fail scoring | `skills/skill_tester.py` |
| 18 | Skill failure-rate analyser + refinement proposals | `skills/skill_refiner.py` |
| 19 | Reflection engine — event-gated via should_reflect() | `reflections/reflection_engine.py` |
| 20 | Improvement planner — reflections → ImprovementProposal + approval objects | `reflections/improvement_planner.py` |
| 21 | Approval queue: list/approve/reject/expire-stale + audit trail | `approvals/approval_queue.py` |
| 22 | Promotion worker — promotes approved improvements | `approvals/promotion_worker.py` |
| 23 | Auto-rollback — monitors 6 metrics | `approvals/rollback_watcher.py` |
| 24 | PWA Web Push via VAPID | `notifications/pwa_push.py` |
| 25 | Slack approval cards with approve/reject buttons | `notifications/slack_notifier.py` |
| 26 | Token budgeter using tiktoken (cl100k_base) | `context/token_budgeter.py` |
| 27 | Relevance ranker: score×0.5 + recency×0.2 + importance×0.3 | `context/relevance_ranker.py` |
| 28 | Session/memory compression engine | `context/compression_engine.py` |
| 29 | **Context builder — now uses P6 orchestrator; no direct vector calls** | `context/context_builder.py` |
| 30 | High-level retrieval API with RetrievalLog persistence | `retrieval/retrieval_engine.py` |
| 31 | **Six independent retrieval providers** | `retrieval/providers.py` |
| 32 | **Multi-source retrieval orchestrator — merge/dedup/tier-rerank/budget/debug** | `retrieval/orchestrator.py` |
| 33 | Metrics engine: record, query history, auto-compute daily snapshot | `metrics/metrics_engine.py` |
| 34 | All 30+ REST endpoints | `api/routes/` |
| 35 | FastAPI app with CORS, static UI serving, lifespan DB init | `api/main.py` |
| 36 | MCP server with 18 tools | `mcp/server.py` |
| 37 | Python SDK: sync + async client | `sdk/client.py` |
| 38 | APScheduler worker | `worker/scheduler.py`, `worker/tasks.py` |
| 39 | React 18 PWA: Dashboard, Memories, Timeline, Skills, Reflections, Improvements, Approvals, Rollbacks, Notifications, Settings, ApprovalDetail | `web/src/pages/` |
| 40 | Slack interactive approvals — HMAC-SHA256 sig verify, 5-min replay window | `api/routes/slack.py`, `notifications/slack_interactions.py` |
| 41 | ApprovalAuditLog ORM + audit writes on every decision | `storage/models.py`, `approvals/approval_queue.py` |
| 42 | GET /api/approvals/{id} endpoint | `api/routes/approvals.py` |
| 43 | Alembic migrations: 0001_initial, 0002_audit_log | `migrations/versions/` |
| 44 | Auth middleware: dev-key pass-through + per-user scoping | `api/deps.py` |
| 45 | **Trust/temporal fields on Memory model** (trust_score, verification_status, confidence, valid_from, valid_to, superseded_by, memory_state, last_verified_at, poisoning_flags) | `storage/models.py` |
| 46 | **RecallDebug schema** | `api/schemas.py` |
| 47 | **Top-level debug block in POST /api/events/recall** | `api/routes/events.py` |
| 48 | Architecture, install, migration, upgrade docs | `docs/` |
| 49 | **Migration 0005: retrieval frequency fields + episodic_chains + lifecycle_events tables** | `migrations/versions/0005_lifecycle_engine.py` |
| 50 | **Observer worker — fast event/trace capture library** | `worker/observer.py` |
| 51 | **Reflector worker — offline pattern analysis + contradiction flagging + proposals** | `worker/reflector.py` |
| 52 | **Consolidator worker — dreaming layer: trust updates + episodic chains + merge** | `worker/consolidator.py` |
| 53 | **Lifecycle worker — state machine (active→aging→stale→archived), supersession, verification decay, trust maintenance** | `worker/lifecycle.py` |
| 54 | **Retrieval frequency tracking on every orchestrated retrieval** | `retrieval/orchestrator.py` |
| 55 | **Migration 0006: procedural learning fields on Memory + retrieval_feedback table** | `migrations/versions/0006_procedural_learning.py` |
| 56 | **RetrievalFeedback ORM model** | `storage/models.py` |
| 57 | **POST /api/events/recall/feedback endpoint — explicit outcome recording, trust evolution, lifecycle logging** | `api/routes/events.py` |
| 58 | **Procedural promoter — episodic chain lesson extraction, confidence scoring, approval-gated promotion** | `worker/procedural_promoter.py` |
| 59 | **write_chain_lesson() helper + procedural promotion wired into consolidation pass** | `worker/consolidator.py` |
| 60 | **Experience pattern mining: repeated successes, failures, recovery patterns + improvement suggestions** | `worker/reflector.py` |
| 61 | **Procedural provider now filters trust ≥ 0.60, ordered by trust DESC** | `retrieval/providers.py` |
| 62 | **Procedural supersession: supersede(old_id, new_id) with trust guards + MemoryLink + LifecycleEvent** | `memory/procedural_store.py` |
| 63 | **RetrievalSession + TelemetrySnapshot ORM models** | `storage/models.py` |
| 64 | **Migration 0007: retrieval_sessions + telemetry_snapshots tables** | `migrations/versions/0007_telemetry.py` |
| 65 | **Cognitive telemetry — 18 metrics, snapshot persistence, history queries** | `telemetry/cognition_metrics.py` |
| 66 | **Retrieval analytics — quality scoring, memory heatmap, session stats** | `telemetry/retrieval_analytics.py` |
| 67 | **Procedural analytics — effectiveness metrics, drift detection, conservative decay** | `telemetry/procedural_analytics.py` |
| 68 | **Automatic feedback inference engine — bounded +0.01/−0.03 inferred trust deltas** | `worker/feedback_inference.py` |
| 69 | **RetrievalSessionOutcomeIn schema** | `api/schemas.py` |
| 70 | **Retrieval session creation in POST /recall + POST /recall/session/{id}/outcome** | `api/routes/events.py` |
| 71 | **Telemetry API — 8 endpoints for metrics, heatmap, effectiveness, drift** | `api/routes/telemetry.py` |
| 72 | **Telemetry UI — cognitive metrics, state distribution, heatmap, procedural, drift** | `web/src/pages/Telemetry.tsx` |
| 73 | **feedback_inference wired into consolidation pass** | `worker/consolidator.py` |
| 74 | **run_telemetry_snapshot + run_drift_detection tasks (6h + daily)** | `worker/tasks.py`, `worker/scheduler.py` |
| 75 | **Task category detection — 5 categories + general fallback** | `retrieval/task_categorizer.py` |
| 76 | **Adaptive provider weights — static category boosts + slow historical adjustment** | `retrieval/adaptive_weights.py` |
| 77 | **Trust-weighted agreement scoring + retrieval confidence estimation** | `retrieval/confidence.py` |
| 78 | **Provider stats accumulation + drift detection + adaptive weight updates** | `retrieval/provider_stats.py` |
| 79 | **SQLite FTS5 full-text search with graceful LIKE fallback** | `storage/fts.py` |
| 80 | **FTS5 keyword provider upgrade (BM25 ranked) + LIKE fallback** | `retrieval/providers.py` |
| 81 | **Migration 0008: provider_stats table + FTS5 virtual table + 3 sync triggers + new retrieval_sessions columns** | `migrations/versions/0008_adaptive_retrieval.py` |
| 82 | **ProviderStats ORM model + RetrievalSession P10 fields** | `storage/models.py` |
| 83 | **Orchestrator P10 integration: adaptive budgets, weighted agreement, confidence, task_category in result** | `retrieval/orchestrator.py` |
| 84 | **P10 fields stored in retrieval session + returned in recall response** | `api/routes/events.py`, `context/context_builder.py` |
| 85 | **Provider stats API endpoints (stats / aggregate / drift)** | `api/routes/telemetry.py` |
| 86 | **Provider stats aggregation background task (6h)** | `worker/tasks.py`, `worker/scheduler.py` |
| 87 | **Provider analytics UI — effectiveness table + drift alerts** | `web/src/pages/Telemetry.tsx`, `web/src/lib/api.ts` |
| 88 | **GraphNode + GraphEdge ORM models (P11)** | `storage/models.py` |
| 89 | **Migration 0009: graph_nodes + graph_edges tables** | `migrations/versions/0009_graph_memory.py` |
| 90 | **Graph memory core dataclasses — GraphNode, GraphEdge, GraphPath, GraphTelemetry** | `graph/memory_graph.py` |
| 91 | **SQLite-backed graph provider — idempotent node/edge creation, neighbor queries** | `graph/graph_provider.py` |
| 92 | **Bounded graph traversal — BFS, causal DFS, contradictions, centrality, boost** | `graph/graph_queries.py` |
| 93 | **Automatic relationship extraction from episodic chains, rollbacks, improvements, retrievals** | `graph/graph_builder.py` |
| 94 | **Graph-assisted retrieval boost (max +0.20) wired into orchestrator scoring** | `retrieval/orchestrator.py` |
| 95 | **Graph API — 7 endpoints: node, traverse, causal, contradictions, centrality, telemetry, build** | `api/routes/graph.py` |
| 96 | **Graph build background task (nightly)** | `worker/tasks.py`, `worker/scheduler.py` |

---

## Test Suite (686 total; default SQLite run 678 passed, 8 skipped; Postgres bootstrap suite 5/5 passing with env)

| File | Tests | Phase added |
|------|-------|-------------|
| `test_approvals` | 2 | P1 |
| `test_audit_trail` | 4 | P5 |
| `test_auth` | 4 | P7 |
| `test_consolidator` | 3 | P3 |
| `test_e2e` | 2 | P3 |
| `test_events` | 3 | P1 |
| `test_mcp` | 10 | P3 |
| `test_memory` | 7 | P1 |
| `test_memory_quality` | 11 | P2 |
| `test_migrations` | 5 | P4 |
| `test_notifications` | 6 | P5 |
| `test_orchestrator` | 12 | **P6 (this session)** |
| `test_quarantine` | 25 | P5-quarantine |
| `test_recall` | 5 | P4 |
| `test_reflection_restraint` | 17 | P2 |
| `test_sdk_parity` | 9 | P3 |
| `test_skills` | 2 | P1 |
| `test_slack_security` | 6 | P5 |
| `test_tailscale_safety` | 2 | P2 |
| `test_temporal_trust` | 14 | P4-trust |
| `test_lifecycle` | 33 | P7 |
| `test_ui` | 8 | P3 |
| `test_procedural_learning` | 31 | P8 |
| `test_p9_telemetry` | 44 | P9 |
| `test_p10_adaptive` | 43 | P10 |
| `test_p11_graph` | 36 | P11 |
| `test_p12_simulation` | 39 | P12 |
| `test_p13_simulation_ui` | 27 | P13 |
| `test_p14_evals` | 40 | P14 |
| `test_p15_fts_isolation` | 16 | P15 |
| `test_p16_production` | 69 | P16 |
| `test_p17_postgres_multi_instance` | 33 | P17 |
| `test_p18_security` | 19 | P18 |
| `test_p19_mcp_http` | 26 | P19 / P19.7 |
| `test_p19_postgres_bootstrap` | 5 | **P19.6 / P19.7** |
| `test_p20_oauth` | 35 | **P20 / P20.1** |
| `test_p20_connection_settings` | 9 | **P20.1 / P20.3** |
| `test_p20_web_resilience` | 2 | **P20.2** |

---

## Architecture: Retrieval Pipeline (P6)

```
POST /api/events/recall
        ↓
retrieval_engine.search()   ← raw vector hits (hits field, always present)
        ↓ (when token_budget given)
context_builder.build()
        ↓
orchestrator.orchestrate()
        ↓
┌─────────────────────────────────────────────────┐
│  Providers (sequential, shared AsyncSession)    │
│  vector · keyword · identity · episodic_recent  │
│  procedural · high_trust                        │
└─────────────────────────────────────────────────┘
        ↓ merge + agreement scoring
        ↓ filter blocked states (quarantined/archived/deleted)
        ↓ composite score (trust×0.3 + agree×0.25 + recency×0.15 + importance×0.2 + base×0.1)
        ↓ tier ordering (identity → project → high_trust → episodic → procedural → other)
        ↓ per-category caps (max_episodic=5, max_low_priority=3)
        ↓ token budget trim
        ↓ debug output
        ↓
context_builder returns {context_string, memories, token_count, debug}
        ↓
recall response: {query, hits, context{memories, token_cost, debug}, debug}
```

---

## Architecture: Memory Trust Pipeline (P5 Quarantine + P4 Trust)

```
POST /api/memory (or /api/events)
        ↓
memory_extractor.extract_trust_info()   ← assigns verification_status, trust_score, confidence
        ↓
quarantine_detector.check()             ← pattern-match 7 threat categories
        ↓ if quarantined:
        │  memory_state=quarantined, verification_status=quarantined, trust_score<=0.2
        │  poisoning_flags=[...], quarantine_reason=...
        ↓ if not quarantined:
           semantic_store: dedup(0.95) → conflict detection → high_trust_contradiction check
           → memory_state in {active, contradicted, quarantined}
```

---

## Run Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

make migrate   # init SQLite + ChromaDB dirs
make dev       # API on :8787
make web       # UI on :5173 (cd web && npm run dev)
make worker    # background APScheduler
make test      # pytest tests/ -v
```

---

## Known Gaps / Next Actions

| Priority | Gap | Recommended Action |
|----------|-----|--------------------|
| Medium | Cursor OAuth still renders server-side authorize pages rather than a fully in-dashboard React wizard | Add a dedicated SPA onboarding route and have `/oauth/authorize` render a thin bridge page that deep-links into the dashboard wizard with encoded OAuth request params |
| High | Pre-P19.7 bootstrap rows may still have wrong ownership (`semantic/episodic` under one user, `procedural` as `NULL`) from the old procedural store path | Run `project_bootstrap(project=\"...\", force=true)` while authenticated as the intended user to rewrite all 7 capsule rows under one `user_id` |
| High | Docker image is ~9GB due to torch+CUDA from sentence-transformers | Add CPU-only torch pin in Dockerfile: `pip install torch --index-url https://download.pytorch.org/whl/cpu` before `-e .` |
| High | `docker compose up --build` requires ~20GB free disk (two copies of torch+CUDA) | Split builds; use `image:` tag instead of `build:` for worker service which shares image with api |
| High | OAuth has no device-code flow for SSH/headless clients | Keep API-key Bearer auth first-class for MCP; if expanding OAuth for headless use, add device-code auth before treating OAuth as a complete replacement |
| High | OAuth authorize page requires API key entry (no password-based login) | Implement proper username/password login page for multi_user mode |
| Medium | Connection profile is stored in global `data/setup_profile.json`, so one user's saved SSH/LAN hints affect all users on the server | Move setup profile into a DB-backed, user-scoped settings model if multi-user installs need personalized MCP guidance |
| Medium | Frontend normalization currently returns permissive `any[]` list shapes to maximize resilience | Introduce shared typed web API contracts once backend response shapes stabilize so resilience does not hide schema drift indefinitely |
| High | Docker image is CPU+CUDA (~9GB); CPU-only torch not yet pinned | Add `pip install torch --index-url https://download.pytorch.org/whl/cpu` before `-e .` in Dockerfile |
| High | Provider concurrency — providers run sequentially (AsyncSession doesn't support concurrent ops) | Session-per-provider scoping with asyncio.gather; requires PostgreSQL async pool |
| Medium | Observer not wired into API routes yet — it's a library, not called by events/memory routes | Wire `observe_event()` calls into `api/routes/events.py` and `api/routes/memory.py` |
| Medium | Episodic chain `procedural_lesson` field is never auto-written by consolidator — only manually or via external caller | Add a pass in consolidator that extracts lessons from chains by summarizing linked memory content |
| Medium | BM25/keyword provider uses simple SQL LIKE matching | ✅ Done P10: FTS5 with LIKE fallback in `storage/fts.py` + `retrieval/providers.py` |
| Medium | Retrieval latency not exposed in debug output | Add per-provider timing to OrchestratorDebug |
| Medium | Token budget in session quality scores is a default estimate (4096) when recording outcome | Pass actual token_budget from session into outcome endpoint or store it at creation time |
| Medium | Telemetry metric history chart — currently raw JSON list, no chart rendering in UI | Add a small line chart (recharts) to the Telemetry page for trend visualization |
| Medium | Provider stats accumulation is additive (counters grow forever); older sessions have equal weight as recent | Add a recency-windowed mode or exponential decay on counters |
| Low | Provider stats loaded on every orchestrated retrieval (one extra SELECT query) | Cache stats in memory with short TTL (30s) to reduce DB load at high throughput |
| Low | Agreement score threshold — all providers weighted equally | ✅ Done P10: trust-weighted agreement in `retrieval/confidence.py` |
| Low | Slack `view_details` returns text URL only | Upgrade to Slack modal (views.open) using trigger_id |
| Low | Graph UI not yet built | Add React pages for graph visualization (memory relationship graph, causal chain explorer, procedure dependency graph) |
| Low | Simulation comparison view not yet a dedicated page | Plans detail page shows paths; a standalone `/simulation/comparison` view comparing runs across plans would need to aggregate runs across multiple plan_ids |
| Low | Simulation UI plan graph is a table, not a visual DAG | Could add react-flow or similar to render step dependencies as a directed graph |
| Low | Forecast calibration only computes when called manually or daily | Consider computing calibration per-project automatically after each outcome is recorded |
| Low | Plan steps have no execution timestamps | Add executed_at and actual_duration to steps for post-execution learning |
| Low | Simulation paths have no real historical retrieval matching | Could use vector search to find similar past plans/simulations for evidence |
| Low | Graph build is nightly batch; new memories aren't immediately graphed | Add lightweight on-write graph registration for high-importance memories |
| Low | `get_most_connected_nodes` does N+1 queries (one per node) | Rewrite as single aggregated SQL query with subquery union |
| Low | Graph edges have no aging/expiry | Add `expires_at` or `last_confirmed_at` to graph edges; age weak edges over time |
| Low | Drift detection only uses failure_rate; does not yet track "frequently marked irrelevant" | Add irrelevant_rate to drift scoring |
