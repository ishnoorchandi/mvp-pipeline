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

# NOTE: counters are updated with `VAR=$((VAR + 1))`, never `((VAR++))`. The
# postfix-increment form evaluates to the OLD value, so the very first increment
# (0 -> 1) makes `((...))` itself report failure (arithmetic result 0 is "false"),
# which previously made `cmd && ((PASS++)) || ((FAIL++))` silently double-count
# the first successful check into FAIL too. `VAR=$((VAR + 1))` is a plain
# assignment and always exits 0, so it can't corrupt the surrounding && / || chain.
run_check() {
    local name="$1"
    local script="$SCRIPT_DIR/$2"
    echo "--- CHECK: $name ---"
    if [ ! -f "$script" ]; then
        echo "[SKIP] Script not found: $script"
        SKIP=$((SKIP + 1))
    else
        local output exit_code
        # `output=$(...)` is itself a simple command whose exit status is the
        # substituted command's exit status — under `set -e` a failing check (e.g.
        # a real npm build failure) would abort run_smoke.sh right here, before the
        # FAIL counter or the summary ever runs. `cmd && a=0 || a=$?` keeps the exit
        # code without letting -e see a bare failing command.
        output=$(bash "$script" "$MVP_DIR" 2>&1) && exit_code=0 || exit_code=$?
        echo "$output"
        if [ "$exit_code" -ne 0 ]; then
            FAIL=$((FAIL + 1))
        elif printf '%s\n' "$output" | grep -qi '^\[SKIP\]'; then
            # The check itself reported not-applicable (e.g. no DB_NAME in a
            # frontend-only app) — exit 0 but it's a SKIP, not a PASS.
            SKIP=$((SKIP + 1))
        else
            PASS=$((PASS + 1))
        fi
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
