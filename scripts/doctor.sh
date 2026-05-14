#!/usr/bin/env bash
# Mimir setup health check. Checks API, web, MCP, auth, database, and config.
# Usage: ./scripts/doctor.sh [BASE_URL]

set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8787}"
WEB_URL="${MIMIR_WEB_URL:-http://127.0.0.1:5173}"
PORT="${BASE_URL##*:}"

PASS="✓"
FAIL="✗"
WARN="⚠"

pass() { echo "  $PASS $1"; }
fail() { echo "  $FAIL $1"; FAILED=1; }
warn() { echo "  $WARN $1"; }

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║            Mimir Doctor                     ║"
echo "╚══════════════════════════════════════════════╝"
echo "  Checking: $BASE_URL"
echo ""

FAILED=0

# ── API health ────────────────────────────────────────────────────────────────
echo "[ API ]"
if HEALTH=$(curl -sf "$BASE_URL/health" 2>/dev/null); then
    STATUS=$(echo "$HEALTH" | grep -o '"status":"[^"]*"' | cut -d'"' -f4 || true)
    VERSION=$(echo "$HEALTH" | grep -o '"version":"[^"]*"' | cut -d'"' -f4 || true)
    pass "API reachable — status=$STATUS version=$VERSION"
else
    fail "API not reachable at $BASE_URL"
    echo ""
    echo "  Possible fixes:"
    echo "    - Run: ./scripts/start_local.sh"
    echo "    - Or:  docker compose --profile local up -d"
    echo ""
    exit 1
fi

# ── Web UI ────────────────────────────────────────────────────────────────────
echo ""
echo "[ Web UI ]"
if curl -sf "$WEB_URL" >/dev/null 2>&1; then
    pass "Web UI reachable at $WEB_URL"
else
    warn "Web UI not reachable at $WEB_URL (API still works)"
fi

# ── Doctor endpoint ───────────────────────────────────────────────────────────
echo ""
echo "[ Setup State ]"
if DOC=$(curl -sf "$BASE_URL/api/system/doctor" 2>/dev/null); then
    SETUP_STATUS=$(echo "$DOC" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4 || true)
    AUTH_MODE=$(echo "$DOC" | grep -o '"auth_mode":"[^"]*"' | cut -d'"' -f4 || true)
    DB_MODE=$(echo "$DOC" | grep -o '"database_mode":"[^"]*"' | cut -d'"' -f4 || true)
    OWNER=$(echo "$DOC" | grep -o '"owner_exists":[^,}]*' | cut -d: -f2 | tr -d ' ' || true)
    MCP_OK=$(echo "$DOC" | grep -o '"mcp_reachable":[^,}]*' | cut -d: -f2 | tr -d ' ' || true)

    [ "$SETUP_STATUS" = "ok" ] && pass "Setup status: ok" || warn "Setup status: $SETUP_STATUS"
    pass "Auth mode: ${AUTH_MODE:-unknown}"
    pass "Database: ${DB_MODE:-unknown}"
    [ "$OWNER" = "true" ] && pass "Owner account exists" || fail "No owner account — open $BASE_URL/setup"
    [ "$MCP_OK" = "true" ] && pass "MCP endpoint reachable" || warn "MCP endpoint not responding (may be auth-gated)"

    # Print any warnings from the doctor
    if echo "$DOC" | grep -q '"code":'; then
        echo ""
        echo "[ Warnings ]"
        echo "$DOC" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for w in d.get('warnings', []):
    print(f'  {w[\"severity\"].upper()}: {w[\"message\"]}')
for s in d.get('fix_suggestions', []):
    print(f'  FIX: {s}')
" 2>/dev/null || true
    fi
else
    warn "Could not reach /api/system/doctor"
fi

# ── MCP tools/list ────────────────────────────────────────────────────────────
echo ""
echo "[ MCP ]"
MCP_RESP=$(curl -sf -X POST "$BASE_URL/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' 2>/dev/null || true)

if echo "$MCP_RESP" | grep -q '"tools"'; then
    TOOL_COUNT=$(echo "$MCP_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('result',{}).get('tools',[])))" 2>/dev/null || echo "?")
    pass "MCP tools/list OK — $TOOL_COUNT tools available"
elif echo "$MCP_RESP" | grep -q '"error"'; then
    ERROR=$(echo "$MCP_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('message',''))" 2>/dev/null || true)
    warn "MCP returned error: $ERROR"
else
    warn "MCP /mcp requires auth — send Authorization: Bearer <API_KEY>"
fi

# ── Port check ────────────────────────────────────────────────────────────────
echo ""
echo "[ Ports ]"
if command -v ss &>/dev/null; then
    if ss -tnlp 2>/dev/null | grep -q ":${PORT}[[:space:]]"; then
        pass "Port $PORT is listening"
    else
        warn "Port $PORT not found in ss output"
    fi
elif command -v lsof &>/dev/null; then
    if lsof -i ":$PORT" &>/dev/null; then
        pass "Port $PORT is listening"
    else
        warn "Nothing found on port $PORT"
    fi
fi

# ── Docker containers ─────────────────────────────────────────────────────────
echo ""
echo "[ Docker ]"
if command -v docker &>/dev/null; then
    RUNNING=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E 'mimir|api|worker|web' || true)
    if [ -n "$RUNNING" ]; then
        while IFS= read -r name; do
            pass "Container running: $name"
        done <<< "$RUNNING"
    else
        warn "No Mimir containers found running"
    fi
else
    warn "docker not available"
fi

echo ""
echo "══════════════════════════════════════════════"
if [ "$FAILED" -eq 0 ]; then
    echo "  Doctor: all critical checks passed."
else
    echo "  Doctor: one or more critical checks FAILED. See above."
fi
echo ""
echo "  Dashboard: $WEB_URL"
echo "  Setup:     $BASE_URL/setup"
echo "  MCP URL:   $BASE_URL/mcp"
echo "══════════════════════════════════════════════"
