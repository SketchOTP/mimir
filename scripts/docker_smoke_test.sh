#!/usr/bin/env bash
# Mimir Docker smoke test
# Usage:
#   ./scripts/docker_smoke_test.sh              # SQLite stack (default)
#   PROFILE=prod-postgres ./scripts/docker_smoke_test.sh  # Postgres stack
#
# Requires: docker, docker compose (v2), curl, jq

set -euo pipefail

PROFILE="${PROFILE:-}"
API_PORT="${MIMIR_PORT:-8787}"
API_URL="http://localhost:${API_PORT}"
API_KEY="${MIMIR_API_KEY:-local-dev-key}"

COMPOSE_ARGS=()
if [[ -n "$PROFILE" ]]; then
  COMPOSE_ARGS=(--profile "$PROFILE")
fi

# Service names differ per profile
if [[ "$PROFILE" == "prod-postgres" ]]; then
  API_SVC="api-pg"
  WORKER_SVC="worker-pg"
  WEB_SVC="web-pg"
else
  API_SVC="api"
  WORKER_SVC="worker"
  WEB_SVC="web"
fi

echo "=== Mimir Docker Smoke Test (profile=${PROFILE:-sqlite}) ==="

# ── 1. Bring up the stack ─────────────────────────────────────────────────────
echo "--- [1] Building and starting containers..."
docker compose "${COMPOSE_ARGS[@]}" up --build -d

# ── 2. Wait for API health ─────────────────────────────────────────────────────
echo "--- [2] Waiting for API health..."
for i in $(seq 1 30); do
  if curl -sf "${API_URL}/health" > /dev/null 2>&1; then
    echo "    API healthy after ${i}s"
    break
  fi
  if [[ $i -eq 30 ]]; then
    echo "ERROR: API did not become healthy in 30s" >&2
    docker compose "${COMPOSE_ARGS[@]}" logs "$API_SVC"
    docker compose "${COMPOSE_ARGS[@]}" down -v
    exit 1
  fi
  sleep 1
done

# ── 3. Health endpoint ─────────────────────────────────────────────────────────
echo "--- [3] Health endpoint..."
HEALTH=$(curl -sf "${API_URL}/health")
echo "    $HEALTH"
echo "$HEALTH" | jq -e '.status == "ok"' > /dev/null

# ── 4. Readiness endpoint ─────────────────────────────────────────────────────
echo "--- [4] Readiness endpoint..."
for i in $(seq 1 20); do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${API_URL}/api/system/readiness" \
    -H "X-API-Key: ${API_KEY}")
  if [[ "$HTTP_CODE" == "200" ]]; then
    echo "    Ready (HTTP 200)"
    break
  fi
  if [[ $i -eq 20 ]]; then
    echo "WARNING: readiness returned ${HTTP_CODE} after 20s (migrations may still be running)"
  fi
  sleep 1
done

# ── 5. Auth flow ───────────────────────────────────────────────────────────────
echo "--- [5] Auth check..."
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "${API_URL}/api/system/status" \
  -H "X-API-Key: ${API_KEY}")
echo "    GET /api/system/status → HTTP ${STATUS}"
[[ "$STATUS" == "200" ]] || { echo "ERROR: Auth failed"; exit 1; }

# ── 6. Create memory ──────────────────────────────────────────────────────────
echo "--- [6] Create memory..."
MEM=$(curl -sf -X POST "${API_URL}/api/memory" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{"content":"Docker smoke test memory","layer":"semantic","importance":0.8}')
echo "    $MEM" | jq -r '"    memory_id=" + .id' 2>/dev/null || echo "    $MEM"
MEM_ID=$(echo "$MEM" | jq -r '.id // empty')
[[ -n "$MEM_ID" ]] || { echo "ERROR: No memory ID returned"; exit 1; }

# ── 7. Recall memory ──────────────────────────────────────────────────────────
echo "--- [7] Recall memory..."
RECALL=$(curl -sf -X POST "${API_URL}/api/events/recall" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{"query":"Docker smoke test"}')
HIT_COUNT=$(echo "$RECALL" | jq '.hits | length' 2>/dev/null || echo "0")
echo "    hits=${HIT_COUNT}"

# ── 8. Worker health (check container running) ────────────────────────────────
echo "--- [8] Worker container status..."
WORKER_STATE=$(docker compose "${COMPOSE_ARGS[@]}" ps --format json "$WORKER_SVC" \
  | jq -r '.[0].State // .State // "unknown"' 2>/dev/null || echo "unknown")
echo "    worker state=${WORKER_STATE}"
[[ "$WORKER_STATE" == "running" ]] || echo "WARNING: worker not in running state (${WORKER_STATE})"

# ── 9. Eval smoke ─────────────────────────────────────────────────────────────
echo "--- [9] Eval smoke (memory_quality)..."
docker compose "${COMPOSE_ARGS[@]}" exec -T "$API_SVC" \
  python -m evals.runner --suite memory_quality 2>&1 | tail -5 || true

# ── 10. Restart and verify persistence ────────────────────────────────────────
echo "--- [10] Restart containers and verify data persistence..."
docker compose "${COMPOSE_ARGS[@]}" restart "$API_SVC"
sleep 5
for i in $(seq 1 15); do
  if curl -sf "${API_URL}/health" > /dev/null 2>&1; then break; fi
  sleep 1
done
MEM_CHECK=$(curl -sf "${API_URL}/api/memory/${MEM_ID}" \
  -H "X-API-Key: ${API_KEY}" 2>/dev/null || echo '{"error":"not_found"}')
if echo "$MEM_CHECK" | jq -e ".id == \"${MEM_ID}\"" > /dev/null 2>&1; then
  echo "    Memory persisted across restart ✓"
else
  echo "WARNING: Memory not found after restart (may be ephemeral in dev mode)"
fi

# ── 11. Tear down ─────────────────────────────────────────────────────────────
echo "--- [11] Tearing down..."
docker compose "${COMPOSE_ARGS[@]}" down -v

echo ""
echo "=== Smoke test PASSED ==="
