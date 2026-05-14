#!/usr/bin/env bash
# Reset Mimir local setup: stops containers and clears data volume.
# WARNING: This DELETES all memories, users, and API keys.
# Usage: ./scripts/reset_local_setup.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "╔══════════════════════════════════════════════╗"
echo "║         Mimir — Reset Local Setup           ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  WARNING: This will DELETE all memories, users, and API keys."
echo "  The mimir_data Docker volume will be removed."
echo ""
read -rp "  Type 'yes' to confirm: " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Stopping containers..."
docker compose --profile local down --remove-orphans 2>/dev/null || true
docker compose --profile prod-postgres down --remove-orphans 2>/dev/null || true

echo "Removing mimir_data volume..."
docker volume rm mimir_data 2>/dev/null || true

echo "Removing saved setup profile (if any)..."
rm -f data/setup_profile.json 2>/dev/null || true

echo ""
echo "Reset complete. To start fresh, run:"
echo "  ./scripts/start_local.sh"
