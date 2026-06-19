#!/usr/bin/env bash
# Check: npm install succeeds
# Usage: bash check_install_node.sh <mvp_dir>

MVP_DIR="${1:?}"

# Find the package.json location (root or frontend/)
if [ -f "$MVP_DIR/frontend/package.json" ]; then
    INSTALL_DIR="$MVP_DIR/frontend"
elif [ -f "$MVP_DIR/package.json" ]; then
    INSTALL_DIR="$MVP_DIR"
else
    echo "[SKIP] No package.json found — skipping npm install check"
    exit 0
fi

echo "Running: npm install --prefer-offline in $INSTALL_DIR"
cd "$INSTALL_DIR"

OUTPUT=$(npm install --prefer-offline 2>&1)
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[FAIL] npm install failed (exit $EXIT_CODE)"
    echo "$OUTPUT" | tail -20
    exit 1
fi

echo "[PASS] npm install succeeded"
exit 0
