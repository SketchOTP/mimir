#!/usr/bin/env bash
# Start Mimir in local single-user mode (SQLite + single_user auth).
# Usage: ./scripts/start_local.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PORT="${MIMIR_PORT:-8787}"
WEB_PORT="${MIMIR_WEB_PORT:-5173}"
PUBLIC_URL="http://127.0.0.1:$PORT"

echo "╔══════════════════════════════════════════════╗"
echo "║          Mimir — Local Single-User           ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Mode:        single_user (SQLite)"
echo "  API URL:     $PUBLIC_URL"
echo "  Web UI:      http://127.0.0.1:$WEB_PORT"
echo "  MCP URL:     $PUBLIC_URL/mcp"
echo ""

# Stop any existing local stack cleanly
docker compose --profile local down --remove-orphans 2>/dev/null || true

echo "Starting services..."
MIMIR_PUBLIC_URL="$PUBLIC_URL" docker compose --profile local up -d --build

echo ""
echo "Waiting for API health check..."
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        echo "  API is healthy."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "  API did not respond in time. Check: docker compose --profile local logs api"
        exit 1
    fi
    sleep 2
done

echo ""
echo "══════════════════════════════════════════════"
echo "  Mimir is running."
echo ""
echo "  NEXT STEP: Open the dashboard to complete setup."
echo "  → http://127.0.0.1:$WEB_PORT"
echo ""
echo "  Or go directly to first-run setup:"
echo "  → http://127.0.0.1:$PORT/setup"
echo ""
echo "  To see health + setup guide, run:"
echo "  → ./scripts/doctor.sh"
echo "══════════════════════════════════════════════"
