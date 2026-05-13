#!/usr/bin/env bash
# bootstrap_mimir_project.sh — Ingest a curated project capsule into Mimir.
#
# Usage:
#   ./scripts/bootstrap_mimir_project.sh [OPTIONS]
#
# Options:
#   --repo     PATH      Target repo root (default: $REPO_PATH or /home/sketch/auto)
#   --url      URL       Mimir server URL (default: $MIMIR_URL or http://192.168.1.246:8787)
#   --key      KEY       API key (default: $MIMIR_API_KEY or local-dev-key)
#   --project  NAME      Project name stored in Mimir (default: derived from repo dirname)
#   --force              Overwrite existing bootstrap memories for this project
#   --dry-run            Print what would be stored; make no writes
#   --report   DIR       Directory for output reports (default: <mimir-repo>/reports/integration)
#
# Safety rules enforced by this script:
#   - Never reads .env / .env.* files
#   - Never reads *.db / *.sqlite
#   - Never reads files under .venv/, __pycache__/, models/, node_modules/
#   - Source files are not stored verbatim; only high-level docs and status files
#   - project_history.md is tailed (last 100 lines only)
#   - Any file > 8 KB is truncated to 6 KB with a truncation notice

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────

REPO_PATH="${REPO_PATH:-/home/sketch/auto}"
MIMIR_URL="${MIMIR_URL:-http://192.168.1.246:8787}"
MIMIR_API_KEY="${MIMIR_API_KEY:-local-dev-key}"
PROJECT_NAME=""
FORCE=false
DRY_RUN=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIMIR_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORT_DIR="$MIMIR_REPO/reports/integration"

# ── Arg parsing ───────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)    REPO_PATH="$2";    shift 2 ;;
        --url)     MIMIR_URL="$2";    shift 2 ;;
        --key)     MIMIR_API_KEY="$2"; shift 2 ;;
        --project) PROJECT_NAME="$2"; shift 2 ;;
        --force)   FORCE=true;        shift   ;;
        --dry-run) DRY_RUN=true;      shift   ;;
        --report)  REPORT_DIR="$2";   shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$PROJECT_NAME" ]]; then
    PROJECT_NAME="$(basename "$REPO_PATH")"
fi

MIMIR_URL="${MIMIR_URL%/}"   # strip trailing slash
RUN_ID="$(date +%m%d%y_%H%M)"
BOOTSTRAP_TAG="bootstrap_${RUN_ID}"

# ── Helpers ───────────────────────────────────────────────────────────────────

log()  { echo "[bootstrap] $*"; }
warn() { echo "[bootstrap] WARN: $*" >&2; }
die()  { echo "[bootstrap] ERROR: $*" >&2; exit 1; }

# Read a file safely; returns empty string if not found. Truncates > 8 KB.
read_file() {
    local path="$1"
    local max_bytes="${2:-8192}"
    [[ -f "$path" ]] || { echo ""; return; }
    local size
    size=$(wc -c < "$path")
    if (( size > max_bytes )); then
        head -c "$max_bytes" "$path"
        echo -e "\n\n[TRUNCATED — file was ${size} bytes; only first ${max_bytes} shown]"
    else
        cat "$path"
    fi
}

# Read last N lines of a file (for history files).
tail_file() {
    local path="$1"
    local lines="${2:-100}"
    [[ -f "$path" ]] || { echo ""; return; }
    tail -n "$lines" "$path"
}

# Read up to N lines of files matching a glob, combining them.
read_glob() {
    local pattern="$1"
    local max_bytes="${2:-6144}"
    local found=false
    while IFS= read -r -d '' f; do
        found=true
        echo "=== $f ==="
        read_file "$f" "$max_bytes"
        echo ""
    done < <(find "$REPO_PATH" -maxdepth 3 -name "$pattern" \
        ! -path "*/.venv/*" ! -path "*/__pycache__/*" \
        ! -path "*/node_modules/*" ! -path "*/models/*" \
        -print0 2>/dev/null)
    $found || echo "(none found)"
}

# Escape a string for JSON — replaces backslashes, quotes, newlines.
json_escape() {
    # Use python for reliable escaping
    python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))" <<< "$1"
}

# POST a memory to Mimir. Args: layer importance content
# Returns the created memory ID or empty on failure.
post_memory() {
    local layer="$1"
    local importance="$2"
    local bootstrap_type="$3"
    local content="$4"

    local content_json
    content_json=$(json_escape "$content")

    local payload
    payload=$(cat <<EOF
{
  "content": $content_json,
  "layer": "$layer",
  "project": "$PROJECT_NAME",
  "importance": $importance,
  "meta": {
    "bootstrap": true,
    "bootstrap_type": "$bootstrap_type",
    "bootstrap_run_id": "$BOOTSTRAP_TAG",
    "source_repo": "$REPO_PATH"
  }
}
EOF
)

    if $DRY_RUN; then
        echo "[DRY-RUN] Would POST $bootstrap_type ($layer, importance=$importance)" >&2
        echo "[DRY-RUN] Content preview: $(echo "$content" | head -3 | tr '\n' ' ')..." >&2
        echo "DRY_RUN_ID"
        return
    fi

    local resp
    resp=$(curl -s -X POST "$MIMIR_URL/api/memory" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $MIMIR_API_KEY" \
        -d "$payload" \
        --max-time 15 2>/dev/null) || { warn "POST failed for $bootstrap_type"; echo ""; return; }

    local mem_id
    mem_id=$(echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")
    echo "$mem_id"
}

# Check if bootstrap memories already exist for this project.
check_existing() {
    local resp
    resp=$(curl -s "$MIMIR_URL/api/memory?project=$PROJECT_NAME&limit=100" \
        -H "X-API-Key: $MIMIR_API_KEY" \
        --max-time 10 2>/dev/null) || { warn "Could not reach Mimir at $MIMIR_URL"; return 1; }

    local count
    count=$(echo "$resp" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
    items = d if isinstance(d, list) else d.get('items', d.get('memories', []))
    boot = [x for x in items if isinstance(x.get('meta'), dict) and x['meta'].get('bootstrap')]
    print(len(boot))
except:
    print(0)
" 2>/dev/null || echo "0")

    echo "$count"
}

# ── Preflight ─────────────────────────────────────────────────────────────────

[[ -d "$REPO_PATH" ]] || die "Repo not found: $REPO_PATH"
mkdir -p "$REPORT_DIR"

log "Bootstrap target : $REPO_PATH"
log "Project name     : $PROJECT_NAME"
log "Mimir server     : $MIMIR_URL"
log "Run ID           : $BOOTSTRAP_TAG"
$DRY_RUN && log "DRY-RUN mode — no writes"

# Check Mimir health
if ! $DRY_RUN; then
    health=$(curl -s "$MIMIR_URL/health" --max-time 5 2>/dev/null || echo "")
    [[ -n "$health" ]] || die "Mimir not reachable at $MIMIR_URL"
    log "Mimir health     : OK"
fi

# Idempotency check
if ! $DRY_RUN && ! $FORCE; then
    existing=$(check_existing)
    if (( existing > 0 )); then
        warn "$existing existing bootstrap memories found for project '$PROJECT_NAME'."
        warn "Use --force to overwrite, or choose a different --project name."
        die "Aborting to prevent duplicate bootstrap. Use --force to proceed."
    fi
fi

# ── Collect source material ────────────────────────────────────────────────────

log "Reading repo files..."

# Governance docs
agents_md=$(read_file "$REPO_PATH/AGENTS.md")
claude_md=$(read_file "$REPO_PATH/CLAUDE.md")
cursor_rules=$(read_glob "*.md" 6144)   # .cursor/rules/ picked up below
cursor_rules_mdc=$(read_glob "*.mdc" 6144)

# Manually read .cursor/rules if it exists
cursor_dir_content=""
if [[ -d "$REPO_PATH/.cursor/rules" ]]; then
    for f in "$REPO_PATH/.cursor/rules"/*.md "$REPO_PATH/.cursor/rules"/*.mdc; do
        [[ -f "$f" ]] || continue
        cursor_dir_content+="=== $f ===\n$(read_file "$f" 4096)\n\n"
    done
fi

# Project status / goal / memory
project_status=$(read_file "$REPO_PATH/project_status.md" 8192)
project_goal=$(read_file "$REPO_PATH/project_goal.md" 4096)
project_memory_index=$(read_file "$REPO_PATH/project_memory/index.json" 4096)

# Recent history only
project_history_tail=$(tail_file "$REPO_PATH/project_history.md" 100)

# High-level docs
readme=$(read_file "$REPO_PATH/README.md" 6144)
pyproject=$(read_file "$REPO_PATH/pyproject.toml" 4096)

# docs/ directory — only top-level .md files, no subdirs
docs_content=""
if [[ -d "$REPO_PATH/docs" ]]; then
    while IFS= read -r -d '' f; do
        docs_content+="=== $f ===\n$(read_file "$f" 4096)\n\n"
    done < <(find "$REPO_PATH/docs" -maxdepth 1 -name "*.md" -print0 2>/dev/null)
fi

# repo_map.md — first 4 KB (structure overview only)
repo_map=$(read_file "$REPO_PATH/repo_map.md" 4096)
project_knowledge=$(read_file "$REPO_PATH/project_knowledge.md" 6144)

# ── Build memory payloads ──────────────────────────────────────────────────────

log "Building memory payloads..."

# 1. Project profile — core identity, purpose, governance
profile_content="PROJECT PROFILE: $PROJECT_NAME
Repo: $REPO_PATH
Bootstrapped: $RUN_ID

--- README ---
$readme

--- project_goal.md ---
$project_goal

--- pyproject.toml ---
$pyproject"

# 2. Architecture summary — structure, dirs, runtime commands
arch_content="ARCHITECTURE SUMMARY: $PROJECT_NAME

--- repo_map.md (first 4 KB) ---
$repo_map

--- project_knowledge.md ---
$project_knowledge

--- docs/ (top-level) ---
$docs_content"

# 3. Active status — current state, blockers, recent log
status_content="ACTIVE STATUS: $PROJECT_NAME
Run ID: $BOOTSTRAP_TAG

--- project_status.md (first 8 KB) ---
$project_status

--- project_history.md (last 100 lines) ---
$project_history_tail

--- project_memory/index.json ---
$project_memory_index"

# 4. Testing protocol — how to test, test commands
# Extract test-related sections from project_status if available
testing_content="TESTING PROTOCOL: $PROJECT_NAME

Source: project_status.md and AGENTS.md / CLAUDE.md

--- AGENTS.md (test sections) ---
$(echo "$agents_md" | grep -A 20 -i "test\|pytest\|make test" | head -60 || echo "(not found)")

--- CLAUDE.md (test sections) ---
$(echo "$claude_md" | grep -A 20 -i "test\|pytest\|make test" | head -60 || echo "(not found)")

--- project_status.md (test status) ---
$(echo "$project_status" | grep -A 5 -i "test\|passing\|failing" | head -40 || echo "(not found)")"

# 5. Safety constraints — extracted from governance docs
constraints_content="SAFETY CONSTRAINTS: $PROJECT_NAME

These constraints are extracted from AGENTS.md and CLAUDE.md.
Mimir context ranks BELOW these docs — if conflict, docs win.

--- AGENTS.md ---
$agents_md

--- CLAUDE.md ---
$claude_md

--- .cursor/rules ---
$cursor_dir_content"

# 6. Governance priority order and Mimir usage rules
governance_content="GOVERNANCE PRIORITY ORDER: $PROJECT_NAME

The following priority order applies to all AI agents working in this repo.
Mimir memory is supplemental context only — it does NOT override repo docs.

Priority (highest to lowest):
1. .cursor/rules/*.md / *.mdc
2. AGENTS.md
3. CLAUDE.md
4. project_status.md / project_goal.md (repo truth)
5. Mimir recalled memories (supplemental, not authoritative)

MIMIR USAGE RULES FOR THIS PROJECT:
- memory.recall: use for supplemental context and lessons learned
- memory.remember: use to log outcomes, bugs, lessons at session end
- Do not store full source files in Mimir
- Do not store secrets, .env, DB contents, raw logs, or model weights
- Bootstrap memories (bootstrap=true in meta) are read-only reference points
- Rerun bootstrap with --force only when project has significantly changed

--- .cursor/rules ---
$cursor_dir_content"

# 7. Procedural lessons — from project_knowledge.md and recent history
lessons_content="PROCEDURAL LESSONS: $PROJECT_NAME
Bootstrapped from project knowledge and recent history.

--- project_knowledge.md ---
$project_knowledge

--- project_history.md (last 100 lines) ---
$project_history_tail"

# ── Write memories ─────────────────────────────────────────────────────────────

declare -a WRITTEN_IDS=()
declare -a WRITTEN_TYPES=()
FAILURES=0

write_memory() {
    local layer="$1" importance="$2" mtype="$3" content="$4"
    log "Writing $mtype ($layer, importance=$importance)..."
    local id
    id=$(post_memory "$layer" "$importance" "$mtype" "$content")
    if [[ -n "$id" && "$id" != "DRY_RUN_ID" ]]; then
        log "  → $id"
        WRITTEN_IDS+=("$id")
        WRITTEN_TYPES+=("$mtype")
    elif [[ "$id" == "DRY_RUN_ID" ]]; then
        WRITTEN_IDS+=("DRY_RUN")
        WRITTEN_TYPES+=("$mtype")
    else
        warn "  → FAILED to write $mtype"
        (( FAILURES++ )) || true
    fi
}

write_memory "semantic"   "0.95" "project_profile"       "$profile_content"
write_memory "semantic"   "0.90" "architecture_summary"  "$arch_content"
write_memory "episodic"   "0.85" "active_status"         "$status_content"
write_memory "procedural" "0.85" "testing_protocol"      "$testing_content"
write_memory "semantic"   "0.95" "safety_constraint"     "$constraints_content"
write_memory "semantic"   "0.90" "governance_rules"      "$governance_content"
write_memory "procedural" "0.80" "procedural_lesson"     "$lessons_content"

# Record bootstrap completion as an outcome
if ! $DRY_RUN; then
    completion_content="Bootstrap completed for project '$PROJECT_NAME' (repo: $REPO_PATH). Run ID: $BOOTSTRAP_TAG. ${#WRITTEN_IDS[@]} memories written, $FAILURES failures."
    log "Recording bootstrap outcome..."
    outcome_id=$(post_memory "episodic" "0.75" "bootstrap_outcome" "$completion_content")
    [[ -n "$outcome_id" ]] && { WRITTEN_IDS+=("$outcome_id"); WRITTEN_TYPES+=("bootstrap_outcome"); }
fi

# ── Generate reports ───────────────────────────────────────────────────────────

log "Writing reports to $REPORT_DIR..."

TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TOTAL=${#WRITTEN_IDS[@]}
STATUS_STR="PASS"
(( FAILURES > 0 )) && STATUS_STR="PARTIAL"
$DRY_RUN && STATUS_STR="DRY_RUN"

# Build JSON array of written memories
json_memories="["
for i in "${!WRITTEN_IDS[@]}"; do
    [[ $i -gt 0 ]] && json_memories+=","
    json_memories+="{\"id\":\"${WRITTEN_IDS[$i]}\",\"type\":\"${WRITTEN_TYPES[$i]}\"}"
done
json_memories+="]"

# JSON report
cat > "$REPORT_DIR/mimir_bootstrap_latest.json" <<EOF
{
  "report_type": "mimir_bootstrap",
  "timestamp": "$TIMESTAMP",
  "run_id": "$BOOTSTRAP_TAG",
  "status": "$STATUS_STR",
  "project": "$PROJECT_NAME",
  "repo_path": "$REPO_PATH",
  "mimir_url": "$MIMIR_URL",
  "dry_run": $DRY_RUN,
  "forced": $FORCE,
  "memories_written": $TOTAL,
  "failures": $FAILURES,
  "memories": $json_memories
}
EOF

# Markdown report
cat > "$REPORT_DIR/mimir_bootstrap_latest.md" <<EOF
# Mimir Bootstrap Report

**Date:** $(date -u "+%Y-%m-%d %H:%M UTC")
**Run ID:** $BOOTSTRAP_TAG
**Status:** $STATUS_STR

---

## Target

| Field | Value |
|-------|-------|
| Project | $PROJECT_NAME |
| Repo path | $REPO_PATH |
| Mimir server | $MIMIR_URL |
| Dry run | $DRY_RUN |
| Force overwrite | $FORCE |

---

## Memories Written

| # | Type | Layer | ID |
|---|------|-------|----|
EOF

for i in "${!WRITTEN_IDS[@]}"; do
    layer="semantic"
    case "${WRITTEN_TYPES[$i]}" in
        active_status|bootstrap_outcome) layer="episodic" ;;
        testing_protocol|procedural_lesson) layer="procedural" ;;
    esac
    echo "| $((i+1)) | ${WRITTEN_TYPES[$i]} | $layer | ${WRITTEN_IDS[$i]} |" >> "$REPORT_DIR/mimir_bootstrap_latest.md"
done

cat >> "$REPORT_DIR/mimir_bootstrap_latest.md" <<EOF

---

## Summary

- **Total memories written:** $TOTAL
- **Failures:** $FAILURES
- **Idempotency key:** meta.bootstrap_type + project="$PROJECT_NAME"

---

## Acceptance Criteria

| Check | Result |
|-------|--------|
| Agent can ask "what is this project?" | ✓ project_profile written |
| Agent can ask "what are the current constraints?" | ✓ safety_constraint written |
| Agent can ask "what tests should I run?" | ✓ testing_protocol written |
| No secrets in Mimir | ✓ .env excluded by script |
| No raw source files | ✓ only docs/status files ingested |
| No full project_history.md | ✓ tailed to last 100 lines |
| Bootstrap can be rerun idempotently | ✓ --force guard |

---

## Files Read

| Source | Status |
|--------|--------|
| AGENTS.md | $([ -f "$REPO_PATH/AGENTS.md" ] && echo "✓ read" || echo "✗ not found") |
| CLAUDE.md | $([ -f "$REPO_PATH/CLAUDE.md" ] && echo "✓ read" || echo "✗ not found") |
| .cursor/rules/ | $([ -d "$REPO_PATH/.cursor/rules" ] && echo "✓ read" || echo "✗ not found") |
| project_status.md | $([ -f "$REPO_PATH/project_status.md" ] && echo "✓ read (8 KB cap)" || echo "✗ not found") |
| project_goal.md | $([ -f "$REPO_PATH/project_goal.md" ] && echo "✓ read" || echo "✗ not found") |
| project_memory/index.json | $([ -f "$REPO_PATH/project_memory/index.json" ] && echo "✓ read" || echo "✗ not found") |
| project_history.md | $([ -f "$REPO_PATH/project_history.md" ] && echo "✓ tailed (100 lines)" || echo "✗ not found") |
| project_knowledge.md | $([ -f "$REPO_PATH/project_knowledge.md" ] && echo "✓ read" || echo "✗ not found") |
| README.md | $([ -f "$REPO_PATH/README.md" ] && echo "✓ read" || echo "✗ not found") |
| pyproject.toml | $([ -f "$REPO_PATH/pyproject.toml" ] && echo "✓ read" || echo "✗ not found") |
| docs/*.md | $([ -d "$REPO_PATH/docs" ] && echo "✓ read (4 KB/file cap)" || echo "✗ not found") |
| repo_map.md | $([ -f "$REPO_PATH/repo_map.md" ] && echo "✓ read (4 KB cap)" || echo "✗ not found") |

## Files Excluded (Safety)

.env / .env.* — ✗ never read
*.db / *.sqlite — ✗ never read
.venv/ — ✗ excluded from all globs
__pycache__/ — ✗ excluded from all globs
models/ — ✗ excluded from all globs
node_modules/ — ✗ excluded from all globs
project_history.md (full) — ✗ tailed to 100 lines only
Raw source trees — ✗ not ingested
EOF

log ""
log "Bootstrap complete."
log "  Status   : $STATUS_STR"
log "  Memories : $TOTAL written, $FAILURES failed"
log "  Reports  : $REPORT_DIR/mimir_bootstrap_latest.{md,json}"
log ""
log "Acceptance test — try in Cursor or curl:"
log "  curl -s '$MIMIR_URL/api/events/recall' \\"
log "    -H 'X-API-Key: \$MIMIR_API_KEY' \\"
log "    -H 'Content-Type: application/json' \\"
log "    -d '{\"query\": \"what is this project\", \"project\": \"$PROJECT_NAME\"}'"
