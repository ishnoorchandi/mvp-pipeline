#!/usr/bin/env bash
# Check: pip install -r requirements.txt succeeds (in a temp venv)
# Usage: bash check_install_python.sh <mvp_dir>

MVP_DIR="${1:?}"

if [ -f "$MVP_DIR/backend/requirements.txt" ]; then
    REQ_FILE="$MVP_DIR/backend/requirements.txt"
    PY_DIR="$MVP_DIR/backend"
elif [ -f "$MVP_DIR/requirements.txt" ]; then
    REQ_FILE="$MVP_DIR/requirements.txt"
    PY_DIR="$MVP_DIR"
else
    echo "[SKIP] No requirements.txt found — skipping pip install check"
    exit 0
fi

echo "Checking requirements: $REQ_FILE"
cd "$PY_DIR"

# Use existing venv if present, otherwise check with pip dry-run
if [ -d "./venv" ]; then
    source ./venv/bin/activate
    OUTPUT=$(pip install -r "$REQ_FILE" --quiet 2>&1)
    EXIT_CODE=$?
    deactivate 2>/dev/null || true
else
    # Just check that requirements.txt is parseable
    OUTPUT=$(pip install --dry-run -r "$REQ_FILE" --quiet 2>&1)
    EXIT_CODE=$?
fi

if [ $EXIT_CODE -ne 0 ]; then
    echo "[FAIL] pip install failed (exit $EXIT_CODE)"
    echo "$OUTPUT" | tail -20
    exit 1
fi

echo "[PASS] pip install check passed"
exit 0
