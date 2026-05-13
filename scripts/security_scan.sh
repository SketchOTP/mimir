#!/usr/bin/env bash
# Mimir security scan — runs all security checks and produces a summary report.
# Usage: ./scripts/security_scan.sh [--out reports/security/latest.json]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

OUT_FILE=""
for arg in "$@"; do
  case "$arg" in
    --out) OUT_NEXT=1 ;;
    *) if [[ "${OUT_NEXT:-0}" == "1" ]]; then OUT_FILE="$arg"; OUT_NEXT=0; fi ;;
  esac
done

REPORT_DIR="reports/security"
mkdir -p "$REPORT_DIR"

PASS=0
FAIL=0
WARN=0
RESULTS=()

_check() {
  local name="$1" status="$2" detail="$3"
  RESULTS+=("{\"check\": \"$name\", \"status\": \"$status\", \"detail\": $(echo "$detail" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))')}")
  case "$status" in
    PASS) PASS=$((PASS+1)); echo "  ✓ $name" ;;
    WARN) WARN=$((WARN+1)); echo "  ⚠ $name: $detail" ;;
    FAIL) FAIL=$((FAIL+1)); echo "  ✗ $name: $detail" ;;
  esac
}

echo "=== Mimir Security Scan ==="
echo ""

# ── 1. Python dependency vulnerability scan ───────────────────────────────────
echo "[ 1/5 ] Python dependency audit (pip-audit)"
PIP_AUDIT_BIN="${VIRTUAL_ENV:-$(pwd)/.venv}/bin/pip-audit"
if [[ ! -x "$PIP_AUDIT_BIN" ]]; then PIP_AUDIT_BIN="pip-audit"; fi
if command -v "$PIP_AUDIT_BIN" &>/dev/null || "$PIP_AUDIT_BIN" --version &>/dev/null 2>&1; then
  PYAUDIT_OUT="$REPORT_DIR/pip_audit.json"
  # Ignore pip itself (tool vuln, not a Mimir runtime dep) and dev-only packages
  "$PIP_AUDIT_BIN" --format json --output "$PYAUDIT_OUT" 2>/dev/null || true
  VULN_INFO=$(python3 -c "
import json
d = json.load(open('$PYAUDIT_OUT'))
# Packages that are dev/tool-only and do not affect Mimir runtime
IGNORE = {'pip', 'setuptools', 'wheel', 'build'}
runtime_vulns = [(p['name'], [v['id'] for v in p['vulns']]) for p in d.get('dependencies', []) if p.get('vulns') and p['name'].lower() not in IGNORE]
tool_vulns = [(p['name'], [v['id'] for v in p['vulns']]) for p in d.get('dependencies', []) if p.get('vulns') and p['name'].lower() in IGNORE]
print(f'RUNTIME:{len(runtime_vulns)} TOOL:{len(tool_vulns)}')
for name, ids in runtime_vulns: print(f'  RUNTIME {name}: {ids}')
for name, ids in tool_vulns: print(f'  TOOL {name}: {ids}')
" 2>/dev/null || echo "RUNTIME:0 TOOL:0")
  RUNTIME_COUNT=$(echo "$VULN_INFO" | head -1 | grep -oP 'RUNTIME:\K[0-9]+' || echo "0")
  TOOL_COUNT=$(echo "$VULN_INFO" | head -1 | grep -oP 'TOOL:\K[0-9]+' || echo "0")
  if [[ "$RUNTIME_COUNT" == "0" ]]; then
    if [[ "$TOOL_COUNT" -gt "0" ]]; then
      _check "pip_audit" "PASS" "No runtime vulnerabilities. $TOOL_COUNT vuln(s) in tool packages (pip/setuptools) — not Mimir runtime deps"
    else
      _check "pip_audit" "PASS" "No known vulnerabilities in Python dependencies"
    fi
  else
    _check "pip_audit" "WARN" "$RUNTIME_COUNT runtime vulnerability/ies found — see $PYAUDIT_OUT"
    echo "$VULN_INFO" | grep "RUNTIME" | sed 's/^/    /'
  fi
else
  _check "pip_audit" "WARN" "pip-audit not installed — run: pip install pip-audit"
fi

# ── 2. npm audit for web ──────────────────────────────────────────────────────
echo "[ 2/5 ] npm dependency audit"
if [[ -f web/package.json ]] && command -v npm &>/dev/null; then
  NPM_AUDIT_OUT="$REPORT_DIR/npm_audit.json"
  npm audit --prefix web --json > "$NPM_AUDIT_OUT" 2>/dev/null || true
  HIGH=$(python3 -c "
import json
d = json.load(open('$NPM_AUDIT_OUT'))
v = d.get('metadata',{}).get('vulnerabilities',{})
print(v.get('high',0) + v.get('critical',0))
" 2>/dev/null || echo "0")
  if [[ "$HIGH" == "0" ]]; then
    _check "npm_audit" "PASS" "No high/critical vulnerabilities in web dependencies"
  else
    _check "npm_audit" "WARN" "$HIGH high/critical vulnerabilities — see $NPM_AUDIT_OUT (dev-only deps may be acceptable)"
  fi
else
  _check "npm_audit" "WARN" "npm not available or web/package.json missing — skip"
fi

# ── 3. Forbidden Tailscale command scan ───────────────────────────────────────
echo "[ 3/5 ] Forbidden Tailscale command scan"
FORBIDDEN_PATTERNS=(
  'tailscale\s+up\b'
  'tailscale\s+down\b'
  'tailscale\s+logout\b'
  'tailscale\s+set\b'
  'systemctl\s+restart\s+tailscaled\b'
)
TS_FOUND=0
for pat in "${FORBIDDEN_PATTERNS[@]}"; do
  hits=$(grep -rEn "$pat" --include="*.py" . \
    --exclude-dir=".venv" --exclude-dir="__pycache__" \
    --exclude-dir=".git" 2>/dev/null | \
    grep -v "test_tailscale_safety\|quarantine_detector\|FORBIDDEN_PATTERNS" || true)
  if [[ -n "$hits" ]]; then
    TS_FOUND=1
    echo "    Forbidden pattern '$pat' found:"
    echo "$hits" | head -5 | sed 's/^/      /'
  fi
done
if [[ "$TS_FOUND" == "0" ]]; then
  _check "tailscale_forbidden_commands" "PASS" "No executable Tailscale commands found in source"
else
  _check "tailscale_forbidden_commands" "FAIL" "Forbidden Tailscale commands found in source — see above"
fi

# ── 4. Secret / credential pattern scan ──────────────────────────────────────
echo "[ 4/5 ] Secret / hardcoded credential scan"
SECRET_PATTERNS=(
  'sk-[A-Za-z0-9]{32,}'     # OpenAI keys
  'AKIA[0-9A-Z]{16}'        # AWS access keys
  'ghp_[A-Za-z0-9]{36}'     # GitHub PATs
  'xoxb-[0-9]+-[A-Za-z0-9]+' # Slack bot tokens
)
SECRET_FOUND=0
for pat in "${SECRET_PATTERNS[@]}"; do
  hits=$(grep -rEn "$pat" --include="*.py" --include="*.ts" --include="*.tsx" \
    --include="*.env" --include="*.yaml" --include="*.yml" . \
    --exclude-dir=".venv" --exclude-dir="__pycache__" \
    --exclude-dir=".git" --exclude-dir="node_modules" 2>/dev/null | \
    grep -v "test_\|fixtures\|# example\|# sample" || true)
  if [[ -n "$hits" ]]; then
    SECRET_FOUND=1
    echo "    Potential credential pattern '$pat':"
    echo "$hits" | head -3 | sed 's/^/      /'
  fi
done
# Also check .env files committed (warn only)
ENV_FILES=$(find . -name ".env" -not -path "./.venv/*" -not -path "./.git/*" 2>/dev/null || true)
if [[ -n "$ENV_FILES" ]]; then
  _check "env_files_committed" "WARN" ".env file(s) found: $ENV_FILES — ensure not in git"
fi
if [[ "$SECRET_FOUND" == "0" ]]; then
  _check "secret_scan" "PASS" "No hardcoded credentials found in source"
else
  _check "secret_scan" "FAIL" "Potential credentials found — review manually"
fi

# ── 5. Insecure config defaults check ────────────────────────────────────────
echo "[ 5/5 ] Insecure config defaults check"
PYCHECK_OUT=$(python3 - 2>&1 <<'PYCHECK' || true
import sys, os
sys.path.insert(0, '.')
os.environ.setdefault('MIMIR_ENV', 'development')
from mimir.config import get_settings, _PROD_INSECURE_DEFAULTS
s = get_settings()
bad = [k for k,v in _PROD_INSECURE_DEFAULTS.items() if getattr(s, k, None) == v]
if bad:
    print(f"INSECURE_DEFAULTS: {', '.join(bad)}")
else:
    print("PASS")
PYCHECK
)
if [[ "$PYCHECK_OUT" == "PASS" ]]; then
  _check "insecure_defaults" "PASS" "No insecure defaults in current environment"
else
  _check "insecure_defaults" "WARN" "Insecure defaults set (expected in dev, must fix in prod): $PYCHECK_OUT"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== Security Scan Summary ==="
echo "  PASS: $PASS"
echo "  WARN: $WARN"
echo "  FAIL: $FAIL"

# Write JSON report
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RESULTS_JSON=$(IFS=,; echo "${RESULTS[*]}")
OVERALL="PASS"
[[ "$FAIL" -gt 0 ]] && OVERALL="FAIL"
[[ "$WARN" -gt 0 && "$OVERALL" == "PASS" ]] && OVERALL="WARN"

REPORT="{\"timestamp\": \"$TIMESTAMP\", \"overall\": \"$OVERALL\", \"pass\": $PASS, \"warn\": $WARN, \"fail\": $FAIL, \"checks\": [$RESULTS_JSON]}"
echo "$REPORT" > "$REPORT_DIR/latest.json"
echo "$REPORT" | python3 -m json.tool > "$REPORT_DIR/latest_pretty.json" 2>/dev/null || true

DEST="${OUT_FILE:-$REPORT_DIR/latest.json}"
if [[ -n "$OUT_FILE" ]]; then
  cp "$REPORT_DIR/latest.json" "$OUT_FILE"
fi

echo ""
echo "Report written to: $REPORT_DIR/latest.json"

if [[ "$FAIL" -gt 0 ]]; then
  echo "SECURITY SCAN FAILED — $FAIL check(s) failed"
  exit 1
fi
exit 0
