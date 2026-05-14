#!/usr/bin/env bash
# Start Mimir as a LAN server (accessible from other machines on your network).
# Detects your LAN IP and sets MIMIR_PUBLIC_URL accordingly.
# Usage: ./scripts/start_lan.sh [LAN_IP]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

PORT="${MIMIR_PORT:-8787}"
WEB_PORT="${MIMIR_WEB_PORT:-5173}"

# Detect or accept LAN IP
if [ -n "${1:-}" ]; then
    LAN_IP="$1"
elif command -v ip &>/dev/null; then
    LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{print $7}' | head -1)"
elif command -v hostname &>/dev/null; then
    LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi

if [ -z "${LAN_IP:-}" ]; then
    echo "ERROR: Could not detect LAN IP. Pass it as an argument:"
    echo "  ./scripts/start_lan.sh 192.168.1.246"
    exit 1
fi

PUBLIC_URL="http://$LAN_IP:$PORT"

echo "╔══════════════════════════════════════════════╗"
echo "║          Mimir — LAN Server Mode            ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  Mode:        single_user (SQLite)"
echo "  LAN IP:      $LAN_IP"
echo "  API URL:     $PUBLIC_URL"
echo "  Web UI:      http://$LAN_IP:$WEB_PORT"
echo "  MCP URL:     $PUBLIC_URL/mcp"
echo ""
echo "  Cursor MCP config (paste into mcp.json):"
echo '  {'
echo '    "mcpServers": {'
echo '      "mimir": {'
echo "        \"url\": \"$PUBLIC_URL/mcp\","
echo '        "headers": {'
echo '          "Authorization": "Bearer YOUR_API_KEY"'
echo '        }'
echo '      }'
echo '    }'
echo '  }'
echo ""

docker compose --profile local down --remove-orphans 2>/dev/null || true

echo "Starting services with PUBLIC_URL=$PUBLIC_URL ..."
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
echo "  Mimir is running on your LAN."
echo ""
echo "  Dashboard:   http://$LAN_IP:$WEB_PORT"
echo "  Setup page:  $PUBLIC_URL/setup"
echo ""
echo "  Paste the Cursor config above into ~/.cursor/mcp.json"
echo "  then restart Cursor."
echo ""
echo "  Your API key: run ./scripts/print_cursor_config.sh after setup."
echo "══════════════════════════════════════════════"
