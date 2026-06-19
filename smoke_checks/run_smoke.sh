#!/usr/bin/env bash
# =============================================================================
# run_smoke.sh — MVP Smoke Check Orchestrator
# Usage: bash run_smoke.sh <mvp_dir>
#
# Runs all smoke checks in sequence.
# Outputs a plain-text log to stdout (captured by the pipeline).
# Each check prints: [PASS] or [FAIL] + reason.
# =============================================================================

set -euo pipefail

MVP_DIR="${1:?Usage: bash run_smoke.sh <mvp_dir>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================"
echo "  MVP Smoke Check Log"
echo "  Target : $MVP_DIR"
echo "  Date   : $(date)"
echo "========================================"
echo ""

PASS=0
FAIL=0
SKIP=0

run_check() {
    local name="$1"
    local script="$SCRIPT_DIR/$2"
    echo "--- CHECK: $name ---"
    if [ ! -f "$script" ]; then
        echo "[SKIP] Script not found: $script"
        ((SKIP++)) || true
    else
        bash "$script" "$MVP_DIR" && ((PASS++)) || ((FAIL++)) || true
    fi
    echo ""
}

# ── Detect project type ────────────────────────────────────────────────────────
HAS_NODE=false
HAS_PYTHON=false
HAS_FLASK=false

[ -f "$MVP_DIR/package.json" ]             && HAS_NODE=true
[ -f "$MVP_DIR/frontend/package.json" ]    && HAS_NODE=true
[ -f "$MVP_DIR/backend/app.py" ]           && HAS_FLASK=true
[ -f "$MVP_DIR/app.py" ]                   && HAS_FLASK=true
[ -f "$MVP_DIR/requirements.txt" ]         && HAS_PYTHON=true
[ -f "$MVP_DIR/backend/requirements.txt" ] && HAS_PYTHON=true
$HAS_FLASK && HAS_PYTHON=true

echo "Project detection:"
echo "  Node/React : $HAS_NODE"
echo "  Python/Flask: $HAS_PYTHON"
echo ""

# ── Always run ────────────────────────────────────────────────────────────────
run_check "Required files present" "check_files.sh"

# ── Node checks ───────────────────────────────────────────────────────────────
if $HAS_NODE; then
    run_check "npm install" "check_install_node.sh"
    run_check "npm build"   "check_build_node.sh"
fi

# ── Python checks ─────────────────────────────────────────────────────────────
if $HAS_PYTHON; then
    run_check "pip install" "check_install_python.sh"
fi

# ── API check (if Flask) ──────────────────────────────────────────────────────
if $HAS_FLASK; then
    run_check "Flask API starts" "check_api.sh"
fi

# ── DB check (optional) ───────────────────────────────────────────────────────
run_check "Database connection" "check_db.sh"

# ── Summary ───────────────────────────────────────────────────────────────────
echo "========================================"
echo "  SUMMARY"
echo "  PASS : $PASS"
echo "  FAIL : $FAIL"
echo "  SKIP : $SKIP"
if [ "$FAIL" -eq 0 ]; then
    echo "  RESULT: ALL CHECKS PASSED"
else
    echo "  RESULT: $FAIL CHECK(S) FAILED"
fi
echo "========================================"
