#!/usr/bin/env bash
# Print the Cursor MCP config for this Mimir instance.
# Usage: ./scripts/print_cursor_config.sh [BASE_URL] [API_KEY]
#
# If API_KEY is omitted, prints a template with YOUR_API_KEY placeholder.
# The key is NOT stored anywhere by this script.

set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8787}"
API_KEY="${2:-YOUR_API_KEY}"

# Try to read public_url from saved profile
if PROFILE=$(curl -sf "$BASE_URL/api/connection/onboarding" 2>/dev/null); then
    MCP_URL=$(echo "$PROFILE" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(d.get('urls', {}).get('mcp_url', ''))
" 2>/dev/null || true)
fi

MCP_URL="${MCP_URL:-$BASE_URL/mcp}"

echo ""
echo "Cursor MCP config for $MCP_URL"
echo "(paste into ~/.cursor/mcp.json or Cursor Settings → MCP)"
echo ""
echo "{"
echo '  "mcpServers": {'
echo '    "mimir": {'
echo "      \"url\": \"$MCP_URL\","
echo '      "headers": {'
echo "        \"Authorization\": \"Bearer $API_KEY\""
echo '      }'
echo '    }'
echo '  }'
echo "}"
echo ""

if [ "$API_KEY" = "YOUR_API_KEY" ]; then
    echo "To generate an API key:"
    echo "  1. Open $BASE_URL/setup"
    echo "  2. Or open the dashboard → Connection Settings → Generate API Key"
fi
